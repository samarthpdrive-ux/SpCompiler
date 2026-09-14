import asyncio
import shutil
import tempfile
import sys
import os
import shlex
import re
from pathlib import Path, PurePosixPath

from fastapi import WebSocket, WebSocketDisconnect

from app.models import RunRequest
from app.services.execution_service import (
    ExecutionError,
    RUNNERS,
)


class InteractiveTerminalService:
    TIMEOUT_SECONDS = 60
    MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB max output limit
    RESERVED_FILE_NAME = ".polyworkspace-stdin"

    async def run(
        self,
        websocket: WebSocket,
        request: RunRequest,
    ):
        runner = RUNNERS.get(request.language)

        if runner is None:
            raise ExecutionError(
                f"Unsupported language: {request.language}"
            )

        entrypoint = request.entrypoint or runner.default_entrypoint
        self._validate_entrypoint(
            entrypoint,
            request.language,
        )

        workspace_dir = tempfile.mkdtemp(prefix="polyworkspace-")
        process = None
        output_task = None
        input_task = None
        wait_task = None
        timeout_task = None
        exit_code = 1
        timed_out = False
        output_exceeded = False

        try:
            self._write_project_files(workspace_dir, request.files, request.stdin)

            command = self._build_execution_command(
                request.language,
                entrypoint,
                workspace_dir,
            )

            # Ensure real-time unbuffered output piping across all OS platforms
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )

            await websocket.send_json(
                {
                    "type": "started",
                    "language": request.language,
                    "message": (
                        "Program started. Type in the terminal "
                        "when the program asks for input."
                    ),
                }
            )

            output_task = asyncio.create_task(
                self._stream_output(
                    websocket,
                    process,
                )
            )

            input_task = asyncio.create_task(
                websocket.receive_json()
            )

            wait_task = asyncio.create_task(
                process.wait()
            )

            timeout_task = asyncio.create_task(
                asyncio.sleep(self.TIMEOUT_SECONDS)
            )

            while True:
                completed_tasks, _ = await asyncio.wait(
                    {
                        input_task,
                        wait_task,
                        timeout_task,
                    },
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if wait_task in completed_tasks:
                    exit_code = wait_task.result()
                    break

                if timeout_task in completed_tasks:
                    timed_out = True

                    await websocket.send_json(
                        {
                            "type": "output",
                            "data": (
                                f"\nExecution stopped after "
                                f"{self.TIMEOUT_SECONDS} seconds.\n"
                            ),
                        }
                    )

                    if process.returncode is None:
                        process.kill()

                    exit_code = await wait_task
                    break

                if input_task in completed_tasks:
                    message = input_task.result()

                    if message.get("type") == "input":
                        user_input = str(
                            message.get("data", "")
                        )

                        if len(user_input) > 65_536:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": (
                                        "Terminal input is too long."
                                    ),
                                }
                            )
                        else:
                            await self._send_input(
                                process,
                                user_input,
                            )

                    if message.get("type") == "stop":
                        if process.returncode is None:
                            process.kill()

                    input_task = asyncio.create_task(
                        websocket.receive_json()
                    )

        except WebSocketDisconnect:
            raise

        except FileNotFoundError as error:
            raise ExecutionError(
                "System runtime command was not found."
            ) from error

        finally:
            if input_task is not None:
                input_task.cancel()

            if timeout_task is not None:
                timeout_task.cancel()

            if process is not None and process.returncode is None:
                process.kill()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=5,
                    )
                except asyncio.TimeoutError:
                    pass

            if output_task is not None:
                try:
                    await output_task
                except WebSocketDisconnect:
                    pass

            if wait_task is not None and wait_task.done():
                exit_code = wait_task.result()

            try:
                shutil.rmtree(workspace_dir, ignore_errors=True)
            except Exception:
                pass

            try:
                await websocket.send_json(
                    {
                        "type": "exit",
                        "exit_code": exit_code,
                        "success": exit_code == 0 and not timed_out and not output_exceeded,
                        "timed_out": timed_out,
                    }
                )
            except WebSocketDisconnect:
                pass

    async def _stream_output(
        self,
        websocket: WebSocket,
        process,
    ):
        stdout = process.stdout
        if stdout is None:
            return

        recent_chunks = []
        max_repeats = 15
        total_bytes = 0

        try:
            while True:
                chunk = await stdout.read(256)  # Smaller read buffer for faster real-time flushing
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > self.MAX_OUTPUT_BYTES:
                    await websocket.send_json(
                        {
                            "type": "output",
                            "data": "\n[GUARDRAIL]: Output limit exceeded (1 MB max).\n",
                        }
                    )
                    if process.returncode is None:
                        process.kill()
                    break

                chunk_str = chunk.decode("utf-8", errors="replace")
                recent_chunks.append(chunk_str)
                if len(recent_chunks) > max_repeats:
                    recent_chunks.pop(0)
                    if len(set(recent_chunks)) == 1 and len(chunk_str.strip()) > 0:
                        await websocket.send_json(
                            {
                                "type": "output",
                                "data": "\n[GUARDRAIL]: Infinite repeating print loop detected.\n",
                            }
                        )
                        if process.returncode is None:
                            process.kill()
                        break

                await websocket.send_json(
                    {
                        "type": "output",
                        "data": chunk_str,
                    }
                )
        except Exception:
            pass

    async def _send_input(
        self,
        process,
        user_input: str,
    ):
        if process.stdin is None:
            raise ExecutionError(
                "The terminal input stream is unavailable."
            )

        process.stdin.write(
            f"{user_input}\n".encode("utf-8")
        )

        await process.stdin.drain()

    def _write_project_files(
        self,
        workspace_dir: str,
        files,
        standard_input: str,
    ):
        workspace_path = Path(workspace_dir)

        for source_file in files:
            safe_path = self._safe_file_path(source_file.path)
            # Preserve nested packages, modules, and resource files.
            file_path = workspace_path.joinpath(*safe_path.parts)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(source_file.content, encoding="utf-8")

        input_path = workspace_path / self.RESERVED_FILE_NAME
        input_path.write_text(standard_input, encoding="utf-8")

    def _build_execution_command(
        self,
        language: str,
        entrypoint: str,
        workspace_dir: str,
    ):
        if language == "python":
            interpreter = (
                os.environ.get("SPCOMPILER_PYTHON")
                or ("python" if getattr(sys, "frozen", False) else sys.executable)
            )
            return [interpreter, "-u", "-B", entrypoint]

        if language == "javascript":
            return ["node", entrypoint]

        if language == "cpp":
            executable = ".polyworkspace-program.exe" if sys.platform == "win32" else ".polyworkspace-program"
            if sys.platform == "win32":
                return [
                    "cmd.exe",
                    "/c",
                    f'g++ "{entrypoint}" -std=c++17 -O2 -o "{executable}" && "{executable}"',
                ]
            return [
                "sh",
                "-c",
                f"g++ {shlex.quote(entrypoint)} -std=c++17 -O2 -o {shlex.quote(executable)} && ./{shlex.quote(executable)}",
            ]

        if language == "java":
            java_files = []
            for root, _, names in os.walk(workspace_dir):
                for name in names:
                    if name.endswith(".java"):
                        java_files.append(os.path.relpath(os.path.join(root, name), workspace_dir))

            if not java_files:
                raise ExecutionError("No Java source files were provided.")

            if sys.platform == "win32":
                sources = " ".join(f'"{path}"' for path in java_files)
                return [
                    "cmd.exe",
                    "/c",
                    f"javac -d . {sources} && java {entrypoint}",
                ]
            else:
                sources = " ".join(shlex.quote(path) for path in java_files)
                return [
                    "sh",
                    "-c",
                    f"javac -d . {sources} && java {shlex.quote(entrypoint)}",
                ]

        raise ExecutionError(f"Unsupported execution language: {language}")

    def _safe_file_path(
        self,
        raw_path: str,
    ):
        normalized = raw_path.replace("\\", "/")
        path = PurePosixPath(normalized)

        if (
            path.is_absolute()
            or ".." in path.parts
            or ":" in normalized
                or str(path) in {"", "."}
                or str(path) == self.RESERVED_FILE_NAME
                or re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._ -]*(/[A-Za-z0-9][A-Za-z0-9._ -]*)*",
                    normalized,
                ) is None
        ):
            raise ExecutionError(
                f"Unsafe or reserved file path: {raw_path}"
            )

        return path

    def _validate_entrypoint(
        self,
        entrypoint: str,
        language: str,
    ):
        if language in {"python", "javascript", "cpp"}:
            self._safe_file_path(entrypoint)

        if language == "java":
            if not entrypoint.replace(".", "").isidentifier():
                raise ExecutionError(
                    "Java entrypoint must be a class name, "
                    "for example: Main"
                )

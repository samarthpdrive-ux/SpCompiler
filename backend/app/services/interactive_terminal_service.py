import asyncio
import shutil
import tempfile
import uuid
from pathlib import PurePosixPath

from fastapi import WebSocket, WebSocketDisconnect

from app.models import RunRequest
from app.services.execution_service import (
    ExecutionError,
    RUNNERS,
)


class InteractiveTerminalService:
    TIMEOUT_SECONDS = 60
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

        workspace_dir = tempfile.mkdtemp(prefix="polyworkspace-term-")
        process = None
        output_task = None
        input_task = None
        wait_task = None
        timeout_task = None
        exit_code = 1
        timed_out = False

        try:
            self._write_project_files(workspace_dir, request.files, request.stdin)
            command = self._build_execution_command(request.language, entrypoint)

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
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
                    process.stdout,
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

        except Exception as error:
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
                        "success": exit_code == 0 and not timed_out,
                        "timed_out": timed_out,
                    }
                )
            except WebSocketDisconnect:
                pass

    async def _stream_output(
        self,
        websocket: WebSocket,
        stdout,
    ):
        if stdout is None:
            return

        while True:
            output_chunk = await stdout.read(1024)

            if not output_chunk:
                break

            output_text = output_chunk.decode(
                "utf-8",
                errors="replace",
            )

            await websocket.send_json(
                {
                    "type": "output",
                    "data": output_text,
                }
            )

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
        import os
        workspace_path = PurePosixPath(workspace_dir)

        for source_file in files:
            safe_path = self._safe_file_path(source_file.path)
            file_path = os.path.join(workspace_dir, os.path.basename(str(safe_path)))
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(source_file.content)

        input_path = os.path.join(workspace_dir, self.RESERVED_FILE_NAME)
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(standard_input)

    def _build_execution_command(
        self,
        language: str,
        entrypoint: str,
    ):
        if language == "python":
            return ["python", "-B", entrypoint]

        if language == "java":
            return [
                "sh",
                "-c",
                f"javac *.java && java {entrypoint} < {self.RESERVED_FILE_NAME}",
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
        if language == "python":
            self._safe_file_path(entrypoint)

        if language == "java":
            if not entrypoint.replace(".", "").isidentifier():
                raise ExecutionError(
                    "Java entrypoint must be a class name, "
                    "for example: Main"
                )

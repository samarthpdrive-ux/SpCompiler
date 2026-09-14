import asyncio
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
import os
import sys
import shlex
import re
from pathlib import Path

from app.models import RunRequest, RunResponse


class ExecutionError(Exception):
    """Raised when a project cannot be prepared or executed."""


@dataclass(frozen=True)
class RunnerSpec:
    default_entrypoint: str


RUNNERS = {
    "python": RunnerSpec(
        default_entrypoint="main.py",
    ),
    "java": RunnerSpec(
        default_entrypoint="Main",
    ),
    "javascript": RunnerSpec(
        default_entrypoint="main.js",
    ),
    "cpp": RunnerSpec(
        default_entrypoint="main.cpp",
    ),
}


class ExecutionService:
    TIMEOUT_SECONDS = 10
    MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB max output limit
    INPUT_FILE_NAME = ".polyworkspace-stdin"

    def health_check(self):
        return {"system": "ready"}

    def run(self, request: RunRequest):
        runner = RUNNERS.get(request.language)

        if runner is None:
            raise ExecutionError(
                f"Unsupported language: {request.language}"
            )

        entrypoint = request.entrypoint or runner.default_entrypoint
        self._validate_entrypoint(entrypoint, request.language)

        workspace_dir = tempfile.mkdtemp(prefix="polyworkspace-exec-")
        started_at = time.perf_counter()
        timed_out = False
        memory_exceeded = False
        output_exceeded = False

        try:
            self._write_project_files(
                workspace_dir=workspace_dir,
                files=request.files,
                standard_input=request.stdin,
            )

            command = self._build_execution_command(
                request.language,
                entrypoint,
                workspace_dir,
            )

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    exit_code, stdout, stderr, timed_out, memory_exceeded, output_exceeded = loop.run_until_complete(
                        pool.submit(self._execute_sync, command, workspace_dir)
                    )
            else:
                exit_code, stdout, stderr, timed_out, memory_exceeded, output_exceeded = asyncio.run(
                    self._execute_async(command, workspace_dir)
                )

            duration_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

            return RunResponse(
                success=exit_code == 0 and not timed_out and not memory_exceeded and not output_exceeded,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=timed_out,
            )

        except Exception as error:
            raise ExecutionError(
                f"Execution failed: {error}"
            ) from error

        finally:
            shutil.rmtree(workspace_dir, ignore_errors=True)

    async def _execute_async(self, command: list, workspace_dir: str):
        timed_out = False
        memory_exceeded = False
        output_exceeded = False
        stdout_data = bytearray()
        stderr_data = bytearray()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        exec_kwargs = {
            "cwd": workspace_dir,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": env,
        }

        process = await asyncio.create_subprocess_exec(
            *command,
            **exec_kwargs
        )

        # The REST endpoint accepts initial standard input. The interactive
        # endpoint instead keeps stdin open for terminal messages.
        if process.stdin is not None:
            input_data = Path(workspace_dir, self.INPUT_FILE_NAME).read_bytes()
            process.stdin.write(input_data)
            await process.stdin.drain()
            process.stdin.close()

        start_time = time.monotonic()
        recent_chunks = []
        max_repeats = 15

        async def read_stream(stream, target_array):
            nonlocal output_exceeded
            try:
                while True:
                    if time.monotonic() - start_time > self.TIMEOUT_SECONDS:
                        break
                    try:
                        chunk = await asyncio.wait_for(stream.read(256), timeout=0.2)
                    except asyncio.TimeoutError:
                        if process.returncode is not None:
                            break
                        continue

                    if not chunk:
                        break
                    target_array.extend(chunk)

                    chunk_str = chunk.decode("utf-8", errors="replace")
                    recent_chunks.append(chunk_str)
                    if len(recent_chunks) > max_repeats:
                        recent_chunks.pop(0)
                        if len(set(recent_chunks)) == 1 and len(chunk_str.strip()) > 0:
                            output_exceeded = True
                            try:
                                process.kill()
                            except Exception:
                                pass
                            break

                    if len(stdout_data) + len(stderr_data) > self.MAX_OUTPUT_BYTES:
                        output_exceeded = True
                        try:
                            process.kill()
                        except Exception:
                            pass
                        break
            except Exception:
                pass

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(process.stdout, stdout_data),
                    read_stream(process.stderr, stderr_data),
                    process.wait()
                ),
                timeout=self.TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                process.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except Exception:
                pass

        exit_code = process.returncode if process.returncode is not None else 1

        stdout = bytes(stdout_data).decode("utf-8", errors="replace")
        stderr = bytes(stderr_data).decode("utf-8", errors="replace")

        if output_exceeded:
            stderr += "\nExecution stopped: Output limit or infinite print loop detected.\n"
        if timed_out:
            stderr += f"\nExecution stopped after {self.TIMEOUT_SECONDS} seconds.\n"

        return exit_code, stdout, stderr, timed_out, memory_exceeded, output_exceeded

    def _execute_sync(self, command: list, workspace_dir: str):
        import subprocess
        timed_out = False
        memory_exceeded = False
        output_exceeded = False
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            sub_kwargs = {
                "cwd": workspace_dir,
                "capture_output": True,
                "timeout": self.TIMEOUT_SECONDS,
                "env": env,
            }

            result = subprocess.run(command, **sub_kwargs)
            exit_code = result.returncode

            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            if len(result.stdout) + len(result.stderr) > self.MAX_OUTPUT_BYTES:
                output_exceeded = True
                stderr += "\nExecution stopped: Output limit or infinite print loop detected.\n"

            return exit_code, stdout, stderr, False, memory_exceeded, output_exceeded
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
            stderr = (e.stderr.decode("utf-8", errors="replace") if e.stderr else "") + f"\nExecution stopped after {self.TIMEOUT_SECONDS} seconds.\n"
            return 1, stdout, stderr, True, False, False
        except Exception as e:
            return 1, "", str(e), False, False, False

    def _write_project_files(
            self,
            workspace_dir: str,
            files,
            standard_input: str,
    ):
        for source_file in files:
            safe_path = self._safe_file_path(source_file.path)
            # Keep the directory structure.  Flattening `src/utils.py` into
            # `utils.py` prevents normal imports and makes data files vanish.
            file_path = os.path.join(workspace_dir, *safe_path.parts)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(source_file.content)

        input_path = os.path.join(workspace_dir, self.INPUT_FILE_NAME)
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(standard_input)

    def _build_execution_command(
            self,
            language: str,
            entrypoint: str,
            workspace_dir: str,
    ):
        if language == "python":
            # In development, this is the interpreter that runs the API.  It
            # is more reliable than assuming that `python` is on PATH.
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
                    f'g++ "{entrypoint}" -std=c++17 -O2 -o "{executable}" && "{executable}" < {self.INPUT_FILE_NAME}',
                ]
            return [
                "sh",
                "-c",
                f"g++ {shlex.quote(entrypoint)} -std=c++17 -O2 -o {shlex.quote(executable)} && ./{shlex.quote(executable)} < {shlex.quote(self.INPUT_FILE_NAME)}",
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
                    f"javac -d . {sources} && java {entrypoint} < {self.INPUT_FILE_NAME}",
                ]
            else:
                sources = " ".join(shlex.quote(path) for path in java_files)
                return [
                    "sh",
                    "-c",
                    f"javac -d . {sources} && java {shlex.quote(entrypoint)} < {shlex.quote(self.INPUT_FILE_NAME)}",
                ]

        raise ExecutionError(f"Unsupported execution language: {language}")

    def _safe_file_path(self, raw_path: str):
        normalized = raw_path.replace("\\", "/")
        path = PurePosixPath(normalized)

        if (
                path.is_absolute()
                or ".." in path.parts
                or ":" in normalized
                or str(path) in {"", "."}
                or str(path) == self.INPUT_FILE_NAME
                # C++ and Java use a shell for compiler redirection. Keep
                # filenames shell-safe as well as traversal-safe.
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

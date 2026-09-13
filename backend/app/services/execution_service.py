import asyncio
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
import os

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
}


class ExecutionService:
    TIMEOUT_SECONDS = 10
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

        try:
            self._write_project_files(
                workspace_dir=workspace_dir,
                files=request.files,
                standard_input=request.stdin,
            )

            command = self._build_execution_command(request.language, entrypoint)

            # Synchronous run wrapper using asyncio loop or direct subprocess run
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # If called from an async context synchronously or via threadpool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    exit_code, stdout, stderr, timed_out = loop.run_until_complete(
                        pool.submit(self._execute_sync, command, workspace_dir)
                    )
            else:
                exit_code, stdout, stderr, timed_out = asyncio.run(
                    self._execute_async(command, workspace_dir)
                )

            duration_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

            return RunResponse(
                success=exit_code == 0 and not timed_out,
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
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=self.TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass
                stdout_bytes, stderr_bytes = await process.communicate()

            exit_code = process.returncode if process.returncode is not None else 1
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if timed_out:
                stderr += (
                    f"\nExecution stopped after "
                    f"{self.TIMEOUT_SECONDS} seconds.\n"
                )

            return exit_code, stdout, stderr, timed_out
        except Exception as e:
            return 1, "", str(e), False

    def _execute_sync(self, command: list, workspace_dir: str):
        import subprocess
        timed_out = False
        try:
            result = subprocess.run(
                command,
                cwd=workspace_dir,
                capture_output=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            return result.returncode, stdout, stderr, False
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
            stderr = (e.stderr.decode("utf-8", errors="replace") if e.stderr else "") + f"\nExecution stopped after {self.TIMEOUT_SECONDS} seconds.\n"
            return 1, stdout, stderr, True
        except Exception as e:
            return 1, "", str(e), False

    def _write_project_files(
            self,
            workspace_dir: str,
            files,
            standard_input: str,
    ):
        workspace_path = PurePosixPath(workspace_dir)
        print(f"DEBUG: Received files payload -> {[getattr(f, 'path', str(f)) for f in files]}")

        for source_file in files:
            safe_path = self._safe_file_path(source_file.path)
            file_path = os.path.join(workspace_dir, os.path.basename(str(safe_path)))
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
    ):
        if language == "python":
            return ["python", "-B", entrypoint]

        if language == "java":
            return [
                "sh",
                "-c",
                f"javac *.java && java {entrypoint} < {self.INPUT_FILE_NAME}",
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

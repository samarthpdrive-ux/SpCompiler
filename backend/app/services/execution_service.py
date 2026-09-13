import io
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
import os

import docker
from docker.errors import DockerException

from app.models import RunRequest, RunResponse


class ExecutionError(Exception):
    """Raised when a project cannot be prepared or executed."""


@dataclass(frozen=True)
class RunnerSpec:
    image: str
    default_entrypoint: str
    environment_name: str


RUNNERS = {
    "python": RunnerSpec(
        image="polyworkspace-python-runner:0.1",
        default_entrypoint="main.py",
        environment_name="ENTRYPOINT",
    ),
    "java": RunnerSpec(
        image="polyworkspace-java-runner:0.1",
        default_entrypoint="Main",
        environment_name="JAVA_MAIN_CLASS",
    ),
}


class ExecutionService:
    TIMEOUT_SECONDS = 10
    INPUT_FILE_NAME = ".polyworkspace-stdin"

    def __init__(self):
        try:
            docker_host = os.environ.get("DOCKER_HOST")
            if docker_host:
                self.client = docker.DockerClient(base_url=docker_host)
            else:
                self.client = docker.from_env()
        except DockerException as error:
            raise ExecutionError(
                "Docker is not running or cannot be reached. Ensure the Docker socket is properly mounted."
            ) from error

    def health_check(self):
        self.client.ping()
        return {"docker": "connected"}

    def run(self, request: RunRequest):
        runner = RUNNERS.get(request.language)

        if runner is None:
            raise ExecutionError(
                f"Unsupported language: {request.language}"
            )

        entrypoint = request.entrypoint or runner.default_entrypoint
        self._validate_entrypoint(entrypoint, request.language)

        container = None
        started_at = time.perf_counter()
        timed_out = False

        try:
            container = self.client.containers.create(
                image=runner.image,
                name=f"polyworkspace-run-{uuid.uuid4().hex}",
                working_dir="/workspace",
                environment={
                    runner.environment_name: entrypoint,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                network_disabled=True,
                read_only=False,
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=64m",
                },
                mem_limit="512m",
                memswap_limit="512m",
                nano_cpus=1_000_000_000,
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                user="runner",
            )

            archive = self._create_project_archive(
                files=request.files,
                standard_input=request.stdin,
            )

            success = container.put_archive(
                path="/workspace",
                data=archive,
            )

            if not success:
                raise ExecutionError(
                    "Could not copy project files into runner container."
                )

            container.start()

            deadline = time.monotonic() + self.TIMEOUT_SECONDS

            while True:
                container.reload()

                if container.status in {"exited", "dead"}:
                    break

                if time.monotonic() >= deadline:
                    timed_out = True
                    container.kill()
                    break

                time.sleep(0.1)

            result = container.wait()
            exit_code = int(result.get("StatusCode", 1))

            stdout = container.logs(
                stdout=True,
                stderr=False,
            ).decode("utf-8", errors="replace")

            stderr = container.logs(
                stdout=False,
                stderr=True,
            ).decode("utf-8", errors="replace")

            duration_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

            if timed_out:
                stderr += (
                    f"\nExecution stopped after "
                    f"{self.TIMEOUT_SECONDS} seconds.\n"
                )

            return RunResponse(
                success=exit_code == 0 and not timed_out,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=timed_out,
            )

        except DockerException as error:
            raise ExecutionError(
                f"Docker execution failed: {error}"
            ) from error

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass

    def _create_project_archive(
            self,
            files,
            standard_input: str,
    ):
        archive_buffer = io.BytesIO()

        # Debug print to verify what files are coming from the frontend payload
        print(f"DEBUG: Received files payload -> {[getattr(f, 'path', str(f)) for f in files]}")

        with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
            for source_file in files:
                safe_path = self._safe_file_path(source_file.path)
                content = source_file.content.encode("utf-8")

                file_info = tarfile.TarInfo(
                    name=os.path.basename(str(safe_path))
                )
                file_info.size = len(content)
                file_info.mode = 0o644

                archive.addfile(
                    tarinfo=file_info,
                    fileobj=io.BytesIO(content),
                )

            input_content = standard_input.encode("utf-8")

            input_info = tarfile.TarInfo(
                name=self.INPUT_FILE_NAME
            )
            input_info.size = len(input_content)
            input_info.mode = 0o644

            archive.addfile(
                tarinfo=input_info,
                fileobj=io.BytesIO(input_content),
            )

        archive_buffer.seek(0)
        return archive_buffer.getvalue()

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
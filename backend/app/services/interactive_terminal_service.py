import asyncio
import shutil
import tempfile
import uuid
import tarfile
import io
from pathlib import Path, PurePosixPath
import os

import docker
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

        container_name = (
            f"polyworkspace-terminal-{uuid.uuid4().hex}"
        )

        client = docker.from_env()
        container = None
        process = None
        output_task = None
        input_task = None
        wait_task = None
        timeout_task = None
        exit_code = 1
        timed_out = False

        try:
            print(f"DEBUG TERMINAL: Received request.files -> {request.files}")
            print(f"DEBUG TERMINAL: Entrypoint requested -> {request.entrypoint}")

            if not request.files:
                print("WARNING: request.files is empty! The frontend sent no files.")

            # Create container with interactive TTY enabled so input streams hook up properly
            container = client.containers.create(
                image=runner.image,
                name=container_name,
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
                stdin_open=True,
                tty=True,
            )

            archive = self._create_project_archive(request.files, request.stdin)
            container.put_archive(path="/workspace", data=archive)

            command = [
                "docker",
                "start",
                "--attach",
                "--interactive",
                container_name,
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
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

                    await self._stop_container(
                        container_name
                    )

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
                        await self._stop_container(
                            container_name,
                        )

                    input_task = asyncio.create_task(
                        websocket.receive_json()
                    )

        except WebSocketDisconnect:
            raise

        except FileNotFoundError as error:
            raise ExecutionError(
                "Docker command was not found. "
                "Start Docker Desktop and try again."
            ) from error

        finally:
            if input_task is not None:
                input_task.cancel()

            if timeout_task is not None:
                timeout_task.cancel()

            if process is not None and process.returncode is None:
                await self._stop_container(container_name)

                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=5,
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            if output_task is not None:
                try:
                    await output_task
                except WebSocketDisconnect:
                    pass

            if wait_task is not None and wait_task.done():
                exit_code = wait_task.result()

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

    async def _stop_container(
        self,
        container_name: str,
    ):
        cleanup_process = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "--force",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        try:
            await asyncio.wait_for(
                cleanup_process.wait(),
                timeout=5,
            )
        except asyncio.TimeoutError:
            cleanup_process.kill()
            await cleanup_process.wait()

    def _create_project_archive(
        self,
        files,
        standard_input: str,
    ):
        archive_buffer = io.BytesIO()

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
                print(f"DEBUG TERMINAL: Successfully archived file -> {source_file.path} (content length: {len(content)})")

            input_content = standard_input.encode("utf-8")

            input_info = tarfile.TarInfo(
                name=self.RESERVED_FILE_NAME
            )
            input_info.size = len(input_content)
            input_info.mode = 0o644

            archive.addfile(
                tarinfo=input_info,
                fileobj=io.BytesIO(input_content),
            )

        archive_buffer.seek(0)
        return archive_buffer.getvalue()

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
from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
import os
import sys
import json
from pathlib import Path

from app.models import RunRequest, RunResponse
from app.services.execution_service import (
    ExecutionError,
    ExecutionService,
)
from app.services.interactive_terminal_service import (
    InteractiveTerminalService,
)

app = FastAPI(
    title="PolyWorkspace",
    description="Self-hosted multi-language project runner",
    version="0.2.0",
)

# Allow all origins for seamless ngrok domain switching
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

execution_service = ExecutionService()
interactive_terminal_service = InteractiveTerminalService()

APP_DATA_DIR = Path.home() / "SpCompiler"
WORKSPACE_SETTINGS_FILE = APP_DATA_DIR / "settings.json"
DEFAULT_WORKSPACE_DIR = Path.home() / "SpCompilerProjects"


def resolve_workspace_dir(raw_path: str) -> str:
    """Create and return an absolute user-selected project storage folder."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="The workspace folder must be an absolute path.",
        )

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return str(candidate.resolve())
    except OSError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create or use that workspace folder: {error}",
        ) from error


def load_workspace_dir() -> tuple[str, bool]:
    try:
        saved_settings = json.loads(
            WORKSPACE_SETTINGS_FILE.read_text(encoding="utf-8")
        )
        saved_path = saved_settings.get("workspace_dir")
        if isinstance(saved_path, str):
            return resolve_workspace_dir(saved_path), True
    except (OSError, json.JSONDecodeError):
        pass

    return resolve_workspace_dir(str(DEFAULT_WORKSPACE_DIR)), False


WORKSPACE_DIR, WORKSPACE_IS_CONFIGURED = load_workspace_dir()


class ProjectCreate(BaseModel):
    project_name: str


class FileSaveRequest(BaseModel):
    project_name: str
    file_path: str
    content: str


class FolderCreateRequest(BaseModel):
    project_name: str
    folder_path: str


class WorkspaceDirectoryRequest(BaseModel):
    workspace_dir: str


def get_project_path(project_name: str) -> str:
    """Return a project directory only when it remains inside the workspace."""
    project_path = os.path.abspath(os.path.join(WORKSPACE_DIR, project_name))
    try:
        if os.path.commonpath([WORKSPACE_DIR, project_path]) != WORKSPACE_DIR:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project name")
    return project_path


def get_file_path(project_path: str, relative_path: str) -> str:
    full_file_path = os.path.abspath(os.path.join(project_path, relative_path))
    try:
        if os.path.commonpath([project_path, full_file_path]) != project_path:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return full_file_path


@app.get("/api/settings/workspace")
def get_workspace_settings():
    return {
        "workspace_dir": WORKSPACE_DIR,
        "is_configured": WORKSPACE_IS_CONFIGURED,
    }


@app.put("/api/settings/workspace")
def set_workspace_settings(data: WorkspaceDirectoryRequest):
    """Persist the chosen project folder for future launches and autosaves."""
    global WORKSPACE_DIR, WORKSPACE_IS_CONFIGURED

    workspace_dir = resolve_workspace_dir(data.workspace_dir.strip())
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE_SETTINGS_FILE.write_text(
            json.dumps({"workspace_dir": workspace_dir}, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save the workspace setting: {error}",
        ) from error

    WORKSPACE_DIR = workspace_dir
    WORKSPACE_IS_CONFIGURED = True
    return {
        "workspace_dir": WORKSPACE_DIR,
        "is_configured": WORKSPACE_IS_CONFIGURED,
    }


@app.get("/api/health")
def health_check():
    try:
        details = execution_service.health_check()
        return {
            "status": "ok",
            **details,
        }
    except ExecutionError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error


@app.post("/api/run", response_model=RunResponse)
async def run_project(request: RunRequest):
    try:
        return await run_in_threadpool(
            execution_service.run,
            request,
        )
    except ExecutionError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


@app.post("/api/projects/create")
def create_project(data: ProjectCreate):
    project_path = get_project_path(data.project_name)
    os.makedirs(project_path, exist_ok=True)
    return {"status": "success", "path": project_path}


@app.post("/api/files/save")
def save_file(data: FileSaveRequest):
    project_path = get_project_path(data.project_name)
    if not os.path.exists(project_path):
        raise HTTPException(status_code=404, detail="Project not found")

    # Prevent path traversal attacks
    full_file_path = get_file_path(project_path, data.file_path)

    os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
    with open(full_file_path, "w", encoding="utf-8") as f:
        f.write(data.content)

    return {"status": "saved", "file_path": data.file_path, "path": full_file_path}


@app.post("/api/folders")
def create_folder(data: FolderCreateRequest):
    """Create an empty nested folder inside the current web workspace."""
    project_path = get_project_path(data.project_name)
    if not os.path.isdir(project_path):
        raise HTTPException(status_code=404, detail="Project not found")

    folder_path = data.folder_path.strip().replace("\\", "/")
    if folder_path in {"", "."}:
        raise HTTPException(status_code=400, detail="A folder name is required")

    full_folder_path = get_file_path(project_path, folder_path)
    try:
        os.makedirs(full_folder_path, exist_ok=True)
    except OSError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Could not create folder: {error}",
        ) from error

    return {"status": "created", "folder_path": folder_path}


@app.delete("/api/files")
def delete_file(project_name: str, file_path: str):
    """Delete one project file, never a directory or a path outside the workspace."""
    project_path = get_project_path(project_name)
    full_file_path = get_file_path(project_path, file_path)

    if not os.path.isfile(full_file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        os.remove(full_file_path)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete the file: {error}",
        ) from error

    return {"status": "deleted", "file_path": file_path}


@app.get("/api/files/read")
def read_file(project_name: str, file_path: str):
    project_path = get_project_path(project_name)
    full_file_path = get_file_path(project_path, file_path)

    if not os.path.exists(full_file_path):
        raise HTTPException(status_code=404, detail="File not found")

    with open(full_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"file_path": file_path, "content": content}


@app.get("/api/projects/files")
def list_project_files(project_name: str):
    """Load every text file in a saved workspace, preserving folder paths."""
    project_path = get_project_path(project_name)
    if not os.path.isdir(project_path):
        raise HTTPException(status_code=404, detail="Project not found")

    files = []
    folders = []
    for path in Path(project_path).rglob("*"):
        if path.is_dir():
            folders.append(path.relative_to(project_path).as_posix())
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # The web editor is text-only; safely omit binary files.
            continue
        files.append({
            "path": path.relative_to(project_path).as_posix(),
            "content": content,
        })

    return {
        "files": sorted(files, key=lambda file: file["path"].lower()),
        "folders": sorted(folders, key=str.lower),
    }


@app.websocket("/api/terminal")
async def interactive_terminal(websocket: WebSocket):
    await websocket.accept()

    try:
        start_message = await websocket.receive_json()

        if start_message.get("type") != "start":
            await websocket.send_json(
                {
                    "type": "error",
                    "message": (
                        "The first terminal message must be "
                        "a start message."
                    ),
                }
            )
            await websocket.close(code=1008)
            return

        request_data = start_message.get("request", {})
        request = RunRequest.model_validate(request_data)

        await interactive_terminal_service.run(
            websocket,
            request,
        )

    except WebSocketDisconnect:
        return

    except (ExecutionError, ValueError) as error:
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(error),
                }
            )
            await websocket.close(code=1011)
        except WebSocketDisconnect:
            return


# --- Serve Frontend Production Build via FastAPI ---
if getattr(sys, 'frozen', False):
    # When running inside the compiled PyInstaller executable bundle
    frontend_dist = os.path.abspath(os.path.join(sys._MEIPASS, "frontend", "dist"))
else:
    # When running normally in development mode
    frontend_dist = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../frontend/dist")
    )

print(f"Looking for frontend dist at: {frontend_dist}")

if os.path.exists(frontend_dist):
    print("Frontend dist found! Mounting static files...")
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


    @app.get("/")
    async def serve_root():
        return FileResponse(os.path.join(frontend_dist, "index.html"))


    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=404, detail="Not found")

        target_file = os.path.join(frontend_dist, full_path)
        if os.path.exists(target_file) and os.path.isfile(target_file):
            return FileResponse(target_file)

        return FileResponse(os.path.join(frontend_dist, "index.html"))
else:
    print("WARNING: Frontend dist folder not found! Did you run 'npm run build'?")

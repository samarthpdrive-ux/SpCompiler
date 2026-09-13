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
import os

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
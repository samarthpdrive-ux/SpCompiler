import subprocess
import sys
import importlib.util
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/packages", tags=["packages"])


class PackageRequest(BaseModel):
    package_name: str


@router.post("/check-and-install")
def check_and_install_package(request: PackageRequest):
    pkg_name = request.package_name.strip()

    # Check if package is already installed in the current Python environment
    spec = importlib.util.find_spec(pkg_name.split("==")[0].split(">=")[0])

    if spec is not None:
        return {
            "status": "already_exists",
            "message": f"Package '{pkg_name}' is already installed."
        }

    # If not installed, run pip install
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg_name],
            capture_output=True,
            text=True,
            check=True
        )
        return {
            "status": "installed",
            "message": f"Successfully installed '{pkg_name}'.",
            "output": result.stdout
        }
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to install '{pkg_name}': {e.stderr}"
        )

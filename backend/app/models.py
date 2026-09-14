from typing import Literal

from pydantic import BaseModel, Field


class SourceFile(BaseModel):
    path: str = Field(
        min_length=1,
        max_length=200,
        examples=["main.py"],
    )
    content: str = Field(
        default="",
        max_length=500_000,
    )


class RunRequest(BaseModel):
    language: Literal["python", "java", "javascript", "cpp"]
    files: list[SourceFile] = Field(
        min_length=1,
        max_length=50,
    )
    entrypoint: str | None = Field(
        default=None,
        max_length=200,
        examples=["main.py"],
    )
    stdin: str = Field(
        default="",
        max_length=65_536,
        examples=["Samarth\n20"],
    )


class RunResponse(BaseModel):
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


class ProjectCreate(BaseModel):
    project_name: str = Field(
        min_length=1,
        max_length=100,
        examples=["my_python_project"],
    )


class FileSaveRequest(BaseModel):
    project_name: str = Field(
        min_length=1,
        max_length=100,
        examples=["my_python_project"],
    )
    file_path: str = Field(
        min_length=1,
        max_length=200,
        examples=["src/main.py", "utils.py"],
    )
    content: str = Field(
        default="",
        max_length=1_000_000,
    )

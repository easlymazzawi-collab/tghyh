from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import ROOT_DIR, app_config, settings
from app.login_worker import create_job, get_job, list_allowed_domains, run_login_job
from app.models import JobSummary, TestLoginRequest

STATIC_DIR = ROOT_DIR / "static"

app = FastAPI(
    title="Login Tester",
    description="Test official login APIs on domains you control",
    version="1.0.0",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class MockLoginRequest(BaseModel):
    username: str
    password: str


MOCK_USERS = {
    "admin": "admin123",
    "user1": "pass111",
    "user2": "pass222",
    "demo": "demo2024",
}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def api_config() -> dict:
    return {
        "allowed_domains": list_allowed_domains(),
        "default_rate_limit_rps": app_config.default_rate_limit_rps,
        "default_max_workers": app_config.default_max_workers,
        "max_credentials_per_job": app_config.max_credentials_per_job,
    }


@app.post("/api/jobs", response_model=JobSummary)
def start_job(request: TestLoginRequest) -> JobSummary:
    try:
        job = create_job(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    thread = threading.Thread(target=run_login_job, args=(job.job_id, request), daemon=True)
    thread.start()
    return job


@app.get("/api/jobs/{job_id}", response_model=JobSummary)
def job_status(job_id: str) -> JobSummary:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/mock/login")
def mock_login(payload: MockLoginRequest) -> dict:
    expected = MOCK_USERS.get(payload.username)
    if expected is None or expected != payload.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "success": True,
        "message": "Login successful",
        "token": f"mock-jwt-{payload.username}",
        "user": {
            "id": abs(hash(payload.username)) % 10000,
            "username": payload.username,
            "role": "admin" if payload.username == "admin" else "user",
            "email": f"{payload.username}@example.local",
        },
        "expires_in": 3600,
    }


@app.get("/mock/health")
def mock_health() -> dict:
    return {"status": "ok", "service": "mock-login-api"}

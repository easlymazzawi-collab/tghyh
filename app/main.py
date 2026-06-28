from __future__ import annotations

import threading

import httpx
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import ROOT_DIR, app_config
from app.validators import validate_allowed_domain
from app.form_discovery import discover_forms_from_html
from app.login_worker import create_job, get_job, list_allowed_domains, run_login_job
from app.models import DiscoverFormRequest, DiscoveredFormResponse, JobSummary, TestLoginRequest

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


@app.post("/api/discover-form", response_model=DiscoveredFormResponse)
def discover_form(request: DiscoverFormRequest) -> DiscoveredFormResponse:
    try:
        validate_allowed_domain(request.page_url)
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(request.page_url)
            response.raise_for_status()
            forms = discover_forms_from_html(request.page_url, response.text)
        if not forms:
            raise ValueError(
                "Không tìm thấy form login (cần có input type=password)."
            )
        discovered = forms[0]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Cannot fetch page: {exc}") from exc

    data = discovered.to_dict()
    return DiscoveredFormResponse(**data, all_forms_count=len(forms))


@app.get("/mock/login-page", response_class=HTMLResponse)
def mock_login_page() -> str:
    return """<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Demo Login Page</title></head>
<body>
  <h1>Đăng nhập (HTML form demo)</h1>
  <form id="loginForm" action="/mock/login-form" method="post">
    <input type="hidden" name="csrf_token" value="demo-csrf-token" />
    <label>Email <input type="email" name="email" id="email" /></label><br/>
    <label>Mật khẩu <input type="password" name="password" id="password" /></label><br/>
    <button type="submit">Đăng nhập</button>
  </form>
</body>
</html>"""


@app.post("/mock/login-form")
def mock_login_form(
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(default=""),
) -> HTMLResponse:
    username = email.split("@")[0] if "@" in email else email
    expected = MOCK_USERS.get(username)
    if expected is None or expected != password:
        return HTMLResponse(
            '<h1>Login failed</h1><p>Invalid username or password</p>',
            status_code=401,
        )
    return HTMLResponse(
        f"""<h1>Welcome, {username}!</h1>
        <p>Login successful</p>
        <a href="/mock/logout">Logout</a>""",
        status_code=200,
    )


@app.get("/mock/logout")
def mock_logout() -> RedirectResponse:
    return RedirectResponse(url="/mock/login-page", status_code=302)


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

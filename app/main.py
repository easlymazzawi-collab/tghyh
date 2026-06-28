from __future__ import annotations

import threading
import uuid
from typing import Dict
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.browser_proxy import rewrite_html
from app.config import ROOT_DIR, app_config
from app.validators import validate_allowed_domain
from app.form_discovery import discover_forms_from_html
from app.login_worker import create_job, get_job, list_allowed_domains, run_login_job
from app.models import (
    DiscoverFormRequest,
    DiscoveredFormResponse,
    JobSummary,
    RecordLoginRequest,
    RecordLoginResponse,
    ReplayJobRequest,
    TestLoginRequest,
)
from app.recipe import LoginRecipe, build_recipe_from_browser
from app.replay_worker import create_replay_job, record_login, run_replay_job, save_recipe

STATIC_DIR = ROOT_DIR / "static"

app = FastAPI(
    title="Login Tester",
    description="Test official login APIs on domains you control",
    version="1.0.0",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_browser_clients: Dict[str, httpx.Client] = {}
_browser_page_url: Dict[str, str] = {}
_browser_recipes: Dict[str, LoginRecipe] = {}


def _browser_client(session_id: str) -> httpx.Client:
    if session_id not in _browser_clients:
        _browser_clients[session_id] = httpx.Client(timeout=20.0, follow_redirects=True)
    return _browser_clients[session_id]


class BrowserCaptureRequest(BaseModel):
    sid: str
    action: str
    method: str = "POST"
    fields: Dict[str, str] = Field(default_factory=dict)
    page_url: str = ""


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
        "demo_login_url": "http://127.0.0.1:8080/mock/login-page",
    }


@app.post("/api/browser/capture")
def browser_capture(request: BrowserCaptureRequest) -> dict:
    page_url = request.page_url or _browser_page_url.get(request.sid, "")
    if page_url:
        validate_allowed_domain(page_url)
    validate_allowed_domain(request.action)

    try:
        recipe = build_recipe_from_browser(
            page_url,
            request.action,
            request.method,
            request.fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = save_recipe(recipe)
    _browser_recipes[request.sid] = saved
    return {
        "ok": True,
        "message": "Đã ghi lại cách login!",
        "recipe": saved.model_dump(),
    }


@app.get("/api/browser/recipe")
def browser_recipe(sid: str) -> dict:
    recipe = _browser_recipes.get(sid)
    if not recipe:
        return {"recorded": False}
    return {"recorded": True, "recipe": recipe.model_dump()}


@app.get("/browser/go", response_model=None)
def browser_go_get(
    url: str = Query(...),
    sid: str = Query(default=""),
):
    target = unquote(url)
    session_id = sid or str(uuid.uuid4())
    validate_allowed_domain(target)
    client = _browser_client(session_id)
    _browser_page_url[session_id] = target

    try:
        response = client.get(target)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Không mở được trang: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type.lower():
        html = rewrite_html(response.text, str(response.url), session_id)
        return HTMLResponse(html)

    return Response(
        content=response.content,
        media_type=content_type.split(";")[0] or "application/octet-stream",
    )


@app.post("/browser/go", response_model=None)
async def browser_go_post(
    request: Request,
    url: str = Query(...),
    sid: str = Query(default=""),
):
    target = unquote(url)
    session_id = sid or str(uuid.uuid4())
    validate_allowed_domain(target)
    client = _browser_client(session_id)
    form = await request.form()
    fields = {key: str(value) for key, value in form.items()}

    login_page = _browser_page_url.get(session_id, target)
    if any("pass" in key.lower() for key in fields):
        try:
            recipe = build_recipe_from_browser(login_page, target, "POST", fields)
            _browser_recipes[session_id] = save_recipe(recipe)
        except ValueError:
            pass

    try:
        response = client.post(target, data=fields)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Không gửi form: {exc}") from exc

    _browser_page_url[session_id] = str(response.url)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type.lower():
        html = rewrite_html(response.text, str(response.url), session_id)
        return HTMLResponse(html)

    return Response(
        content=response.content,
        media_type=content_type.split(";")[0] or "application/octet-stream",
    )


@app.post("/api/record-login", response_model=RecordLoginResponse)
def api_record_login(request: RecordLoginRequest) -> RecordLoginResponse:
    try:
        recipe, sample_ok, message = record_login(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RecordLoginResponse(
        recipe=recipe.model_dump(),
        sample_login_ok=sample_ok,
        message=message,
    )


@app.post("/api/replay", response_model=JobSummary)
def api_replay(request: ReplayJobRequest) -> JobSummary:
    try:
        job = create_replay_job(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    thread = threading.Thread(target=run_replay_job, args=(job.job_id, request), daemon=True)
    thread.start()
    return job


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
        f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><title>Welcome {username}</title></head>
<body>
  <h1>Welcome, {username}!</h1>
  <h2>Login successful</h2>
  <table border="1" cellpadding="6">
    <tr><th>Username</th><td>{username}</td></tr>
    <tr><th>Role</th><td>{"admin" if username == "admin" else "user"}</td></tr>
    <tr><th>Email</th><td>{username}@example.local</td></tr>
  </table>
  <p><a href="/mock/logout">Logout</a></p>
</body></html>""",
        status_code=200,
    )


@app.get("/mock/logout")
def mock_logout() -> RedirectResponse:
    return RedirectResponse(url="/mock/login-page", status_code=302)


@app.get("/mock/dashboard", response_class=HTMLResponse)
def mock_dashboard(authorization: str | None = Header(default=None)) -> str:
    username = "unknown"
    role = "user"
    if authorization and "mock-jwt-" in authorization.lower():
        username = authorization.lower().split("mock-jwt-", 1)[1].strip()
        role = "admin" if username == "admin" else "user"

    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><title>Dashboard - {username}</title></head>
<body>
  <h1>Dashboard</h1>
  <h2>Xin chào, {username}</h2>
  <p>Bạn đã đăng nhập thành công vào hệ thống demo.</p>
  <table border="1" cellpadding="6">
    <tr><th>Username</th><td>{username}</td></tr>
    <tr><th>Role</th><td>{role}</td></tr>
    <tr><th>Email</th><td>{username}@example.local</td></tr>
    <tr><th>Plan</th><td>{"Enterprise" if role == "admin" else "Standard"}</td></tr>
  </table>
  <p><button type="button">Cài đặt</button> <button type="button">Đăng xuất</button></p>
</body></html>"""


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

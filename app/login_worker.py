from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import httpx

from app.config import app_config
from app.form_discovery import detect_login_success, discover_login_form
from app.logger import result_logger
from app.models import (
    CredentialResult,
    JobStatus,
    JobSummary,
    LoginPayloadMode,
    TestLoginRequest,
)
from app.rate_limiter import RateLimiter
from app.validators import parse_credentials, validate_allowed_domain


_jobs: Dict[str, JobSummary] = {}
_jobs_lock = threading.Lock()
_form_cache: Dict[str, dict] = {}
_form_cache_lock = threading.Lock()


def _get_html_form_config(request: TestLoginRequest) -> dict:
    page_url = (request.page_url or request.login_url).strip()
    cache_key = f"{page_url}|{request.form_action_url}|{request.username_field}|{request.password_field}"

    with _form_cache_lock:
        if cache_key in _form_cache:
            return _form_cache[cache_key]

    if request.username_field and request.password_field and request.form_action_url:
        config = {
            "page_url": page_url,
            "action_url": request.form_action_url,
            "method": "POST",
            "username_field": request.username_field,
            "password_field": request.password_field,
            "hidden_fields": dict(request.hidden_fields),
        }
    else:
        discovered = discover_login_form(page_url)
        config = discovered.to_dict()

    with _form_cache_lock:
        _form_cache[cache_key] = config
    return config


def _extract_success_details(response: httpx.Response) -> Dict[str, object]:
    details: Dict[str, object] = {
        "content_type": response.headers.get("content-type", ""),
    }

    try:
        body = response.json()
        details["response_json"] = body
        for key in ("token", "access_token", "refresh_token", "user", "data", "message"):
            if isinstance(body, dict) and key in body:
                details[key] = body[key]
    except Exception:
        text = response.text[:500]
        details["response_preview"] = text

    auth_header = response.headers.get("authorization") or response.headers.get("Authorization")
    if auth_header:
        details["authorization_header"] = auth_header

    set_cookie = response.headers.get("set-cookie")
    if set_cookie:
        details["set_cookie"] = set_cookie[:200]

    return details


def _attempt_html_form_login(
    request: TestLoginRequest,
    username: str,
    password: str,
    limiter: RateLimiter,
    form_config: dict,
) -> CredentialResult:
    limiter.acquire()
    page_url = form_config["page_url"]
    action_url = form_config["action_url"]
    method = form_config.get("method", "POST").upper()

    payload = dict(form_config.get("hidden_fields") or {})
    payload[form_config["username_field"]] = username
    payload[form_config["password_field"]] = password

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            client.get(page_url)
            if method == "GET":
                response = client.get(action_url, params=payload, headers=request.extra_headers or None)
            else:
                response = client.post(action_url, data=payload, headers=request.extra_headers or None)

            elapsed_ms = response.elapsed.total_seconds() * 1000
            success = detect_login_success(response, page_url)

            details: Dict[str, object] = {
                "page_url": page_url,
                "action_url": action_url,
                "username_field": form_config["username_field"],
                "password_field": form_config["password_field"],
                "final_url": str(response.url),
                "content_type": response.headers.get("content-type", ""),
            }
            if success:
                details.update(_extract_success_details(response))
            else:
                details["response_preview"] = response.text[:300]

            return CredentialResult(
                username=username,
                success=success,
                status_code=response.status_code,
                response_time_ms=round(elapsed_ms, 2),
                message="Login successful" if success else "Login failed",
                details=details,
            )
    except httpx.RequestError as exc:
        return CredentialResult(
            username=username,
            success=False,
            message=f"Request error: {exc}",
        )


def _attempt_login(
    request: TestLoginRequest,
    username: str,
    password: str,
    limiter: RateLimiter,
    form_config: Optional[dict] = None,
) -> CredentialResult:
    if request.payload_mode == LoginPayloadMode.HTML_FORM and form_config:
        return _attempt_html_form_login(request, username, password, limiter, form_config)

    limiter.acquire()
    payload = {
        request.username_field: username,
        request.password_field: password,
    }

    try:
        # httpx.Client is not thread-safe — one client per worker call.
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            if request.payload_mode == LoginPayloadMode.JSON:
                response = client.post(
                    request.login_url,
                    json=payload,
                    headers=request.extra_headers or None,
                )
            else:
                response = client.post(
                    request.login_url,
                    data=payload,
                    headers=request.extra_headers or None,
                )

            elapsed_ms = response.elapsed.total_seconds() * 1000
            success = 200 <= response.status_code < 300

            body_text = response.text.lower()
            if success and any(
                marker in body_text
                for marker in ("invalid", "incorrect", "failed", "unauthorized")
            ):
                if response.status_code == 200 and "error" in body_text:
                    success = False

            return CredentialResult(
                username=username,
                success=success,
                status_code=response.status_code,
                response_time_ms=round(elapsed_ms, 2),
                message="Login successful" if success else "Login failed",
                details=_extract_success_details(response) if success else {
                    "response_preview": response.text[:300],
                },
            )
    except httpx.RequestError as exc:
        return CredentialResult(
            username=username,
            success=False,
            message=f"Request error: {exc}",
        )


def _append_result(job: JobSummary, result: CredentialResult) -> None:
    with _jobs_lock:
        job.results.append(result)
        if result.success:
            job.success_count += 1
        else:
            job.failure_count += 1


def run_login_job(job_id: str, request: TestLoginRequest) -> None:
    job = _jobs[job_id]
    job.status = JobStatus.RUNNING

    try:
        pairs = parse_credentials(request.credentials)
        validate_allowed_domain(request.login_url)
        if request.payload_mode == LoginPayloadMode.HTML_FORM:
            validate_allowed_domain(request.page_url or request.login_url)
        job.total = len(pairs)
        job.login_url = request.login_url
        job.results = []
        job.success_count = 0
        job.failure_count = 0

        form_config = None
        if request.payload_mode == LoginPayloadMode.HTML_FORM:
            form_config = _get_html_form_config(request)
            job.login_url = form_config["action_url"]

        limiter = RateLimiter(request.rate_limit_rps)

        with ThreadPoolExecutor(max_workers=request.max_workers) as executor:
            futures = {
                executor.submit(
                    _attempt_login, request, username, password, limiter, form_config
                ): username
                for username, password in pairs
            }
            for future in as_completed(futures):
                result = future.result()
                _append_result(job, result)
                result_logger.write(
                    job_id,
                    {
                        "username": result.username,
                        "success": result.success,
                        "status_code": result.status_code,
                        "response_time_ms": result.response_time_ms,
                        "message": result.message,
                        "details": result.details,
                    },
                )

        job.results.sort(key=lambda item: item.username.lower())
        job.status = JobStatus.COMPLETED
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)


def create_job(request: TestLoginRequest) -> JobSummary:
    validate_allowed_domain(request.login_url)
    if request.payload_mode == LoginPayloadMode.HTML_FORM:
        validate_allowed_domain(request.page_url or request.login_url)
    parse_credentials(request.credentials)

    job_id = str(uuid.uuid4())
    job = JobSummary(job_id=job_id, status=JobStatus.PENDING, login_url=request.login_url)
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> JobSummary | None:
    return _jobs.get(job_id)


def list_allowed_domains() -> List[str]:
    return list(app_config.allowed_domains)

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import httpx

from app.config import app_config
from app.logger import result_logger
from app.models import (
    CredentialResult,
    JobStatus,
    JobSummary,
    LoginPayloadMode,
    TestLoginRequest,
)
from app.rate_limiter import RateLimiter


_jobs: Dict[str, JobSummary] = {}


def parse_credentials(raw: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Line {line_no}: expected format user:pass")
        username, password = line.split(":", 1)
        username = username.strip()
        password = password.strip()
        if not username or not password:
            raise ValueError(f"Line {line_no}: username and password cannot be empty")
        pairs.append((username, password))
    if not pairs:
        raise ValueError("No credentials provided")
    if len(pairs) > app_config.max_credentials_per_job:
        raise ValueError(
            f"Maximum {app_config.max_credentials_per_job} credentials per job"
        )
    return pairs


def validate_allowed_domain(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid login URL")

    allowed = [d.lower() for d in app_config.allowed_domains]
    if hostname not in allowed and not any(
        hostname.endswith(f".{domain}") for domain in allowed if domain not in {"localhost", "127.0.0.1"}
    ):
        raise ValueError(
            f"Domain '{hostname}' is not in allowlist. "
            f"Allowed: {', '.join(app_config.allowed_domains)}"
        )


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


def _attempt_login(
    client: httpx.Client,
    request: TestLoginRequest,
    username: str,
    password: str,
    limiter: RateLimiter,
) -> CredentialResult:
    limiter.acquire()
    payload = {
        request.username_field: username,
        request.password_field: password,
    }

    try:
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
            for marker in ("invalid", "incorrect", "failed", "error", "unauthorized")
        ):
            if response.status_code == 200:
                success = False

        result = CredentialResult(
            username=username,
            success=success,
            status_code=response.status_code,
            response_time_ms=round(elapsed_ms, 2),
            message="Login successful" if success else "Login failed",
            details=_extract_success_details(response) if success else {
                "response_preview": response.text[:300],
            },
        )
        return result
    except httpx.RequestError as exc:
        return CredentialResult(
            username=username,
            success=False,
            message=f"Request error: {exc}",
        )


def run_login_job(job_id: str, request: TestLoginRequest) -> None:
    job = _jobs[job_id]
    job.status = JobStatus.RUNNING

    try:
        pairs = parse_credentials(request.credentials)
        validate_allowed_domain(request.login_url)
        job.total = len(pairs)
        job.login_url = request.login_url

        limiter = RateLimiter(request.rate_limit_rps)
        results: List[CredentialResult] = []

        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            with ThreadPoolExecutor(max_workers=request.max_workers) as executor:
                futures = {
                    executor.submit(
                        _attempt_login, client, request, username, password, limiter
                    ): username
                    for username, password in pairs
                }
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
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

        results.sort(key=lambda item: item.username.lower())
        job.results = results
        job.success_count = sum(1 for item in results if item.success)
        job.failure_count = job.total - job.success_count
        job.status = JobStatus.COMPLETED
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)


def create_job(request: TestLoginRequest) -> JobSummary:
    validate_allowed_domain(request.login_url)
    parse_credentials(request.credentials)

    job_id = str(uuid.uuid4())
    job = JobSummary(job_id=job_id, status=JobStatus.PENDING, login_url=request.login_url)
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> JobSummary | None:
    return _jobs.get(job_id)


def list_allowed_domains() -> List[str]:
    return list(app_config.allowed_domains)

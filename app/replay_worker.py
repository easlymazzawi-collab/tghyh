from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

import httpx

from app.form_discovery import detect_login_success
from app.logger import result_logger
from app.login_worker import (
    _append_result,
    _attach_screen_report,
    _extract_success_details,
    _get_html_form_config,
    _jobs,
    _jobs_lock,
)
from app.models import (
    CredentialResult,
    JobStatus,
    JobSummary,
    LoginPayloadMode,
    ReplayJobRequest,
    RecordLoginRequest,
    TestLoginRequest,
)
from app.rate_limiter import RateLimiter
from app.recipe import LoginRecipe, build_recipe_from_request, parse_curl_command, render_body
from app.validators import parse_credentials, validate_allowed_domain

_recipes: Dict[str, LoginRecipe] = {}


def save_recipe(recipe: LoginRecipe) -> LoginRecipe:
    recipe.validate_domains()
    _recipes[recipe.recipe_id] = recipe
    return recipe


def get_recipe(recipe_id: str) -> LoginRecipe | None:
    return _recipes.get(recipe_id)


def record_login(request: RecordLoginRequest) -> tuple[LoginRecipe, bool, str]:
    if request.curl_command.strip():
        recipe = record_from_curl(request)
        sample_ok = True
        message = "Đã ghi lại từ lệnh cURL"
    else:
        if not request.login_url.strip():
            raise ValueError("Nhập URL login hoặc dán lệnh cURL")
        pairs = parse_credentials(request.credentials)
        if len(pairs) != 1:
            raise ValueError("Ghi lại login cần đúng 1 dòng user:pass mẫu")

        username, password = pairs[0]
        test_request = TestLoginRequest(
            login_url=request.login_url,
            credentials=f"{username}:{password}",
            username_field=request.username_field,
            password_field=request.password_field,
            payload_mode=request.payload_mode,
            page_url=request.page_url,
            form_action_url=request.form_action_url,
            post_login_url=request.post_login_url,
            capture_screen=request.capture_screen,
            hidden_fields=request.hidden_fields,
            extra_headers=request.extra_headers,
            max_workers=1,
            rate_limit_rps=1,
        )

        form_config = None
        if request.payload_mode == LoginPayloadMode.HTML_FORM:
            form_config = _get_html_form_config(test_request)

        recipe = build_recipe_from_request(
            test_request, username, password, form_config=form_config
        )
        recipe.name = request.name or recipe.name
        save_recipe(recipe)

        sample_ok = True
        message = "Đã ghi lại cách login"
        if request.verify_sample:
            result = _replay_one(recipe, username, password, RateLimiter(10.0))
            sample_ok = result.success
            message = (
                "Login mẫu thành công — sẵn sàng replay"
                if sample_ok
                else f"Đã ghi recipe nhưng login mẫu thất bại: {result.message}"
            )

    return recipe, sample_ok, message


def record_from_curl(request: RecordLoginRequest) -> LoginRecipe:
    if not request.curl_command.strip():
        raise ValueError("Thiếu lệnh cURL")

    pairs = parse_credentials(request.credentials) if request.credentials.strip() else []
    sample_user = pairs[0][0] if pairs else ""
    sample_pass = pairs[0][1] if pairs else ""

    recipe = parse_curl_command(request.curl_command, sample_user, sample_pass)
    recipe.name = request.name or recipe.name
    recipe.post_login_url = request.post_login_url
    recipe.capture_screen = request.capture_screen
    return save_recipe(recipe)


def _replay_one(
    recipe: LoginRecipe,
    username: str,
    password: str,
    limiter: RateLimiter,
    max_workers_context: bool = True,
) -> CredentialResult:
    limiter.acquire()

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            last_response: Optional[httpx.Response] = None
            page_url = recipe.login_page_url or recipe.login_url

            for step in recipe.steps:
                body = render_body(step.body_template, username, password)
                headers = dict(recipe.headers)

                if step.method == "GET":
                    last_response = client.get(step.url, headers=headers or None)
                else:
                    if step.use_json:
                        last_response = client.post(
                            step.url, json=body, headers=headers or None
                        )
                    else:
                        last_response = client.post(
                            step.url, data=body, headers=headers or None
                        )

            if last_response is None:
                raise ValueError("Recipe không có bước nào")

            response = last_response
            elapsed_ms = response.elapsed.total_seconds() * 1000

            if recipe.payload_mode == LoginPayloadMode.HTML_FORM:
                success = detect_login_success(response, page_url)
            else:
                success = 200 <= response.status_code < 300
                body_text = response.text.lower()
                if success and "error" in body_text and response.status_code == 200:
                    if any(m in body_text for m in ("invalid", "incorrect", "failed")):
                        success = False

            details: Dict[str, object] = {
                "recipe_id": recipe.recipe_id,
                "replayed": True,
            }
            if success:
                details.update(_extract_success_details(response))
            else:
                details["response_preview"] = response.text[:300]

            fake_request = TestLoginRequest(
                login_url=recipe.login_url,
                credentials=f"{username}:{password}",
                username_field=recipe.username_field,
                password_field=recipe.password_field,
                payload_mode=recipe.payload_mode,
                page_url=recipe.login_page_url,
                post_login_url=recipe.post_login_url,
                capture_screen=recipe.capture_screen,
            )
            details = _attach_screen_report(
                fake_request, username, success, details, response, client
            )

            return CredentialResult(
                username=username,
                success=success,
                status_code=response.status_code,
                response_time_ms=round(elapsed_ms, 2),
                message="Replay login successful" if success else "Replay login failed",
                details=details,
            )
    except httpx.RequestError as exc:
        return CredentialResult(
            username=username,
            success=False,
            message=f"Replay request error: {exc}",
            details={"recipe_id": recipe.recipe_id, "replayed": True},
        )


def create_replay_job(request: ReplayJobRequest) -> JobSummary:
    recipe = _resolve_recipe(request)
    recipe.validate_domains()
    parse_credentials(request.credentials)

    job_id = str(uuid.uuid4())
    job = JobSummary(
        job_id=job_id,
        status=JobStatus.PENDING,
        login_url=recipe.login_url,
    )
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _resolve_recipe(request: ReplayJobRequest) -> LoginRecipe:
    if request.recipe_id:
        stored = get_recipe(request.recipe_id)
        if stored:
            return stored
    if request.recipe:
        return LoginRecipe(**request.recipe)
    raise ValueError("Thiếu recipe — ghi lại login trước hoặc dán JSON recipe")


def run_replay_job(job_id: str, request: ReplayJobRequest) -> None:
    try:
        recipe = _resolve_recipe(request)
    except ValueError as exc:
        job = _jobs[job_id]
        job.status = JobStatus.FAILED
        job.error = str(exc)
        return

    job = _jobs[job_id]
    job.status = JobStatus.RUNNING

    try:
        pairs = parse_credentials(request.credentials)
        job.total = len(pairs)
        job.login_url = recipe.login_url
        job.results = []
        job.success_count = 0
        job.failure_count = 0

        limiter = RateLimiter(request.rate_limit_rps)

        with ThreadPoolExecutor(max_workers=request.max_workers) as executor:
            futures = {
                executor.submit(_replay_one, recipe, user, pwd, limiter): user
                for user, pwd in pairs
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
                        "screen": result.details.get("screen"),
                        "recipe_id": recipe.recipe_id,
                        "details": result.details,
                    },
                )

        job.results.sort(key=lambda item: item.username.lower())
        job.status = JobStatus.COMPLETED
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)

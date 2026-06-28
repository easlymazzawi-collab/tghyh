from __future__ import annotations

import json
import re
import shlex
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from app.models import LoginPayloadMode, TestLoginRequest
from app.validators import validate_allowed_domain


USERNAME_MARKERS = ("user", "email", "login", "account")
PASSWORD_MARKERS = ("pass", "pwd", "matkhau", "mat_khau")


class RecipeStep(BaseModel):
    method: str = "GET"
    url: str
    purpose: str
    content_type: str = ""
    body_template: Dict[str, str] = Field(default_factory=dict)
    use_json: bool = False


class LoginRecipe(BaseModel):
    recipe_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "recorded-login"
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    sample_username: str = ""
    payload_mode: LoginPayloadMode = LoginPayloadMode.JSON
    login_page_url: str = ""
    login_url: str = ""
    username_field: str = "username"
    password_field: str = "password"
    headers: Dict[str, str] = Field(default_factory=dict)
    hidden_fields: Dict[str, str] = Field(default_factory=dict)
    post_login_url: str = ""
    capture_screen: bool = True
    steps: List[RecipeStep] = Field(default_factory=list)

    def validate_domains(self) -> None:
        for url in (self.login_page_url, self.login_url, self.post_login_url):
            if url.strip():
                validate_allowed_domain(url.strip())
        for step in self.steps:
            validate_allowed_domain(step.url)


def _guess_credential_fields(
    fields: Dict[str, str], sample_username: str, sample_password: str
) -> Tuple[str, str]:
    username_field = ""
    password_field = ""

    for key, value in fields.items():
        if value == sample_password:
            password_field = key
        elif value == sample_username:
            username_field = key

    if not username_field or not password_field:
        for key in fields:
            lowered = key.lower()
            if not password_field and any(m in lowered for m in PASSWORD_MARKERS):
                password_field = key
            if not username_field and any(m in lowered for m in USERNAME_MARKERS):
                username_field = key

    if not username_field and fields:
        username_field = next(iter(fields.keys()))
    if not password_field:
        for key in fields:
            if key != username_field:
                password_field = key
                break

    return username_field, password_field


def _template_body(
    fields: Dict[str, str],
    username_field: str,
    password_field: str,
    sample_username: str,
    sample_password: str,
) -> Dict[str, str]:
    template: Dict[str, str] = {}
    for key, value in fields.items():
        if key == username_field or value == sample_username:
            template[key] = "{{username}}"
        elif key == password_field or value == sample_password:
            template[key] = "{{password}}"
        else:
            template[key] = value
    return template


def build_recipe_from_api_json(
    login_page_url: str,
    action_url: str,
    method: str,
    fields: Dict[str, str],
    headers: Optional[Dict[str, str]] = None,
) -> LoginRecipe:
    from app.browser_proxy import resolve_real_url

    login_page_url = resolve_real_url(login_page_url)
    action_url = resolve_real_url(action_url)
    username_field, password_field = _guess_credential_fields(fields, "", "")
    sample_username = fields.get(username_field, "")
    sample_password = fields.get(password_field, "")

    body_template = _template_body(
        fields,
        username_field,
        password_field,
        sample_username,
        sample_password,
    )

    steps = []
    if login_page_url.strip():
        steps.append(
            RecipeStep(
                method="GET",
                url=login_page_url,
                purpose="load_login_page",
            )
        )
    steps.append(
        RecipeStep(
            method=method.upper(),
            url=action_url,
            purpose="submit_login",
            content_type="application/json",
            body_template=body_template,
            use_json=True,
        )
    )

    return LoginRecipe(
        name="browser-api-recipe",
        sample_username=sample_username,
        payload_mode=LoginPayloadMode.JSON,
        login_page_url=login_page_url,
        login_url=action_url,
        username_field=username_field,
        password_field=password_field,
        headers=dict(headers or {}),
        hidden_fields={
            k: v
            for k, v in fields.items()
            if k not in {username_field, password_field}
        },
        steps=steps,
    )


def build_recipe_from_browser(
    login_page_url: str,
    action_url: str,
    method: str,
    fields: Dict[str, str],
) -> LoginRecipe:
    from app.browser_proxy import extract_form_fields, resolve_real_url

    login_page_url = resolve_real_url(login_page_url)
    action_url = resolve_real_url(action_url)

    username_field, password_field, normalized = extract_form_fields(fields)
    sample_username = normalized.get(username_field, "")
    sample_password = normalized.get(password_field, "")

    body_template = _template_body(
        normalized,
        username_field,
        password_field,
        sample_username,
        sample_password,
    )

    steps = []
    if login_page_url.strip():
        steps.append(
            RecipeStep(
                method="GET",
                url=login_page_url,
                purpose="load_login_page",
            )
        )
    steps.append(
        RecipeStep(
            method=method.upper(),
            url=action_url,
            purpose="submit_login",
            content_type="application/x-www-form-urlencoded",
            body_template=body_template,
            use_json=False,
        )
    )

    return LoginRecipe(
        name="browser-recipe",
        sample_username=sample_username,
        payload_mode=LoginPayloadMode.HTML_FORM,
        login_page_url=login_page_url,
        login_url=action_url,
        username_field=username_field,
        password_field=password_field,
        hidden_fields={
            k: v
            for k, v in normalized.items()
            if k not in {username_field, password_field}
        },
        steps=steps,
    )


def build_recipe_from_request(
    request: TestLoginRequest,
    username: str,
    password: str,
    *,
    form_config: Optional[dict] = None,
) -> LoginRecipe:
    recipe = LoginRecipe(
        name=f"recipe-{username}",
        sample_username=username,
        payload_mode=request.payload_mode,
        login_url=request.login_url,
        login_page_url=(request.page_url or request.login_url),
        username_field=request.username_field,
        password_field=request.password_field,
        headers=dict(request.extra_headers),
        hidden_fields=dict(request.hidden_fields),
        post_login_url=request.post_login_url,
        capture_screen=request.capture_screen,
    )

    if request.payload_mode == LoginPayloadMode.HTML_FORM and form_config:
        recipe.login_page_url = form_config["page_url"]
        recipe.login_url = form_config["action_url"]
        recipe.username_field = form_config["username_field"]
        recipe.password_field = form_config["password_field"]
        recipe.hidden_fields = dict(form_config.get("hidden_fields") or {})

        body_fields = dict(recipe.hidden_fields)
        body_fields[recipe.username_field] = username
        body_fields[recipe.password_field] = password
        body_template = _template_body(
            body_fields,
            recipe.username_field,
            recipe.password_field,
            username,
            password,
        )

        recipe.steps = [
            RecipeStep(
                method="GET",
                url=recipe.login_page_url,
                purpose="load_login_page",
            ),
            RecipeStep(
                method=form_config.get("method", "POST"),
                url=recipe.login_url,
                purpose="submit_login",
                content_type="application/x-www-form-urlencoded",
                body_template=body_template,
                use_json=False,
            ),
        ]
        return recipe

    body = {
        request.username_field: username,
        request.password_field: password,
    }
    body_template = _template_body(
        body, request.username_field, request.password_field, username, password
    )
    use_json = request.payload_mode == LoginPayloadMode.JSON
    recipe.steps = [
        RecipeStep(
            method="POST",
            url=request.login_url,
            purpose="submit_login",
            content_type="application/json" if use_json else "application/x-www-form-urlencoded",
            body_template=body_template,
            use_json=use_json,
        )
    ]
    return recipe


def _normalize_curl_text(raw: str) -> str:
    text = raw.strip()
    if text.lower().startswith("curl "):
        text = text[5:].strip()
    text = text.replace("^\\^", "").replace("^\"", '"').replace("^\n", " ")
    text = re.sub(r"\^\s*$", "", text, flags=re.MULTILINE)
    return text


def parse_curl_command(curl_text: str, sample_username: str = "", sample_password: str = "") -> LoginRecipe:
    normalized = _normalize_curl_text(curl_text)
    try:
        tokens = shlex.split(normalized, posix=False)
    except ValueError as exc:
        raise ValueError(f"Không parse được cURL: {exc}") from exc

    method = "GET"
    url = ""
    headers: Dict[str, str] = {}
    body_raw = ""
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()
        if lower in {"-x", "--request"}:
            method = tokens[i + 1].upper()
            i += 2
            continue
        if lower in {"-h", "--header"}:
            header = tokens[i + 1]
            if ":" in header:
                key, value = header.split(":", 1)
                headers[key.strip()] = value.strip()
            i += 2
            continue
        if lower in {"-d", "--data", "--data-raw", "--data-binary"}:
            body_raw = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
            continue
        if token.startswith("http://") or token.startswith("https://"):
            url = token.strip("'\"")
        i += 1

    if not url:
        raise ValueError("Không tìm thấy URL trong lệnh cURL")

    validate_allowed_domain(url)
    content_type = headers.get("Content-Type", headers.get("content-type", "")).lower()
    fields: Dict[str, str] = {}
    use_json = False

    if body_raw:
        stripped = body_raw.strip()
        if stripped.startswith("{") or "application/json" in content_type:
            use_json = True
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    fields = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError:
                use_json = False

        if not fields:
            fields = {k: v[0] for k, v in parse_qs(body_raw, keep_blank_values=True).items()}

    username_field, password_field = _guess_credential_fields(
        fields, sample_username, sample_password
    )
    body_template = _template_body(
        fields, username_field, password_field, sample_username, sample_password
    )

    payload_mode = LoginPayloadMode.JSON if use_json else LoginPayloadMode.FORM
    if not use_json and "multipart/form-data" in content_type:
        payload_mode = LoginPayloadMode.FORM

    steps = [
        RecipeStep(
            method=method,
            url=url,
            purpose="submit_login",
            content_type="application/json" if use_json else "application/x-www-form-urlencoded",
            body_template=body_template,
            use_json=use_json,
        )
    ]

    return LoginRecipe(
        name="curl-recipe",
        sample_username=sample_username,
        payload_mode=payload_mode,
        login_url=url,
        username_field=username_field,
        password_field=password_field,
        headers=headers,
        steps=steps,
    )


def render_body(template: Dict[str, str], username: str, password: str) -> Dict[str, str]:
    rendered: Dict[str, str] = {}
    for key, value in template.items():
        rendered[key] = (
            value.replace("{{username}}", username).replace("{{password}}", password)
        )
    return rendered

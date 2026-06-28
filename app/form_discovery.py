from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.validators import validate_allowed_domain


USERNAME_HINTS = (
    "username",
    "user",
    "email",
    "login",
    "account",
    "userid",
    "user_name",
    "user_id",
    "matkhau",  # sometimes mislabeled; lower priority
)

PASSWORD_HINTS = ("password", "pass", "passwd", "pwd", "matkhau", "mat_khau")


class DiscoveredForm:
    def __init__(
        self,
        page_url: str,
        action_url: str,
        method: str,
        username_field: str,
        password_field: str,
        hidden_fields: Dict[str, str],
        form_index: int,
        score: int,
    ) -> None:
        self.page_url = page_url
        self.action_url = action_url
        self.method = method.upper()
        self.username_field = username_field
        self.password_field = password_field
        self.hidden_fields = hidden_fields
        self.form_index = form_index
        self.score = score

    def to_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "action_url": self.action_url,
            "method": self.method,
            "username_field": self.username_field,
            "password_field": self.password_field,
            "hidden_fields": self.hidden_fields,
            "form_index": self.form_index,
            "confidence": self.score,
        }


def _field_name(tag) -> str:
    return (tag.get("name") or tag.get("id") or "").strip()


def _score_username_field(name: str, input_type: str) -> int:
    lowered = name.lower()
    score = 0
    if input_type in {"text", "email", "tel"}:
        score += 2
    if input_type == "email":
        score += 3
    for hint in USERNAME_HINTS:
        if hint in lowered:
            score += 5
    if lowered in {"username", "email", "user", "login"}:
        score += 4
    return score


def _score_password_field(name: str, input_type: str) -> int:
    if input_type == "password":
        return 20
    lowered = name.lower()
    score = 0
    for hint in PASSWORD_HINTS:
        if hint in lowered:
            score += 5
    return score


def _score_form(form, password_input) -> int:
    score = 10
    action = (form.get("action") or "").lower()
    form_id = (form.get("id") or "").lower()
    form_class = " ".join(form.get("class") or []).lower()
    blob = f"{action} {form_id} {form_class}"
    if any(k in blob for k in ("login", "signin", "sign-in", "auth", "dangnhap", "dang-nhap")):
        score += 8
    if password_input is not None:
        score += 5
    return score


def _pick_input(candidates: List, scorer) -> Optional[str]:
    best_name = None
    best_score = 0
    for tag in candidates:
        name = _field_name(tag)
        if not name:
            continue
        input_type = (tag.get("type") or "text").lower()
        score = scorer(name, input_type)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def discover_forms_from_html(page_url: str, html: str) -> List[DiscoveredForm]:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    discovered: List[DiscoveredForm] = []

    for index, form in enumerate(forms):
        inputs = form.find_all("input")
        password_inputs = [
            i for i in inputs if (i.get("type") or "").lower() == "password"
        ]
        if not password_inputs:
            continue

        text_inputs = [
            i
            for i in inputs
            if (i.get("type") or "text").lower() in {"text", "email", "tel", ""}
        ]
        username_field = _pick_input(text_inputs, _score_username_field)
        if not username_field:
            # fallback: first non-hidden, non-submit input before password
            for tag in inputs:
                input_type = (tag.get("type") or "text").lower()
                if input_type in {"hidden", "submit", "button", "password", "checkbox", "radio"}:
                    continue
                name = _field_name(tag)
                if name:
                    username_field = name
                    break

        password_field = _pick_input(password_inputs, _score_password_field)
        if not username_field or not password_field:
            continue

        hidden_fields: Dict[str, str] = {}
        for tag in inputs:
            if (tag.get("type") or "").lower() != "hidden":
                continue
            name = _field_name(tag)
            if name:
                hidden_fields[name] = tag.get("value") or ""

        action = form.get("action") or page_url
        action_url = urljoin(page_url, action)
        method = (form.get("method") or "post").upper()
        score = _score_form(form, password_inputs[0] if password_inputs else None)

        discovered.append(
            DiscoveredForm(
                page_url=page_url,
                action_url=action_url,
                method=method,
                username_field=username_field,
                password_field=password_field,
                hidden_fields=hidden_fields,
                form_index=index,
                score=score,
            )
        )

    discovered.sort(key=lambda item: item.score, reverse=True)
    return discovered


def discover_login_form(page_url: str, html: str | None = None) -> DiscoveredForm:
    validate_allowed_domain(page_url)

    if html is None:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(page_url)
            response.raise_for_status()
            html = response.text

    forms = discover_forms_from_html(page_url, html)
    if not forms:
        raise ValueError(
            "Không tìm thấy form login (cần có input type=password). "
            "Thử nhập thủ công tên trường username/password."
        )
    return forms[0]


def detect_login_success(response: httpx.Response, page_url: str) -> bool:
    if not (200 <= response.status_code < 400):
        return False

    if response.history:
        final_url = str(response.url).lower()
        if "login" not in final_url and "signin" not in final_url:
            return True

    if response.cookies:
        session_markers = ("session", "token", "auth", "sid", "jwt")
        if any(any(m in name.lower() for m in session_markers) for name in response.cookies.keys()):
            return True

    body = response.text.lower()
    failure_markers = (
        "invalid",
        "incorrect",
        "sai ",
        "thất bại",
        "that bai",
        "failed",
        "wrong password",
        "unauthorized",
    )
    success_markers = ("dashboard", "welcome", "logout", "đăng xuất", "dang xuat")

    if any(m in body for m in failure_markers):
        return False
    if any(m in body for m in success_markers):
        return True
    page_lower = page_url.lower()
    final_url = str(response.url).lower()
    if "login" not in final_url and final_url != page_lower:
        return True

    return response.status_code == 200 and "error" not in body[:800]

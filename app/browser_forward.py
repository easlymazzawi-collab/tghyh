from __future__ import annotations

import json
from typing import Dict, Iterable, List, Tuple

from fastapi import Request
from httpx import Client, Response

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

RESPONSE_SKIP_HEADERS = HOP_BY_HOP_HEADERS | {
    "set-cookie",
    "content-encoding",
    "content-length",
}


def forward_request_headers(request: Request) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered in {"cookie", "content-type", "content-length"}:
            continue
        headers[key] = value
    return headers


def request_content_type(request: Request) -> str:
    return request.headers.get("content-type", "")


def response_headers_to_forward(response: Response) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in response.headers.items():
        if key.lower() in RESPONSE_SKIP_HEADERS:
            continue
        out[key] = value
    return out


def collect_set_cookies(response: Response) -> List[str]:
  cookies: List[str] = []
  for key, value in response.headers.items():
    if key.lower() == "set-cookie":
      cookies.append(value)
  return cookies


def is_html_response(content_type: str) -> bool:
    return "text/html" in (content_type or "").lower()


def is_json_request(content_type: str) -> bool:
    return "application/json" in (content_type or "").lower()


def extract_json_fields(body: bytes) -> Dict[str, str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            out[str(key)] = json.dumps(value, ensure_ascii=False)
        else:
            out[str(key)] = str(value)
    return out


def looks_like_login_payload(fields: Dict[str, str]) -> bool:
    if not fields:
        return False
    has_pass = any("pass" in k.lower() or "pwd" in k.lower() for k in fields)
    has_user = any(
        x in k.lower() for k in fields for x in ("user", "email", "login", "account")
    )
    return has_pass and has_user


def login_capture_headers(request: Request) -> Dict[str, str]:
    keep = ("g-captcha", "authorization", "x-csrf-token", "x-requested-with")
    out: Dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in keep or lowered.startswith("x-"):
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            out[key] = value
    return out

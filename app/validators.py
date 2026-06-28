from __future__ import annotations

from typing import List, Tuple
from urllib.parse import urlparse

from app.config import app_config


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
        hostname.endswith(f".{domain}")
        for domain in allowed
        if domain not in {"localhost", "127.0.0.1"}
    ):
        raise ValueError(
            f"Domain '{hostname}' is not in allowlist. "
            f"Allowed: {', '.join(app_config.allowed_domains)}"
        )

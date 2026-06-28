from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup


WELCOME_PATTERNS = (
    r"welcome[,\s]+([^\!<\n\.]+)",
    r"chào[,\s]+([^\!<\n\.]+)",
    r"xin chào[,\s]+([^\!<\n\.]+)",
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_headings(soup: BeautifulSoup, limit: int = 5) -> List[str]:
    headings: List[str] = []
    for tag in soup.find_all(["h1", "h2", "h3"], limit=limit):
        text = _clean_text(tag.get_text(" ", strip=True))
        if text and text not in headings:
            headings.append(text)
    return headings


def _extract_links(soup: BeautifulSoup, limit: int = 8) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    for tag in soup.find_all("a", href=True, limit=limit):
        text = _clean_text(tag.get_text(" ", strip=True))
        href = tag.get("href", "").strip()
        if not href:
            continue
        links.append({"text": text or href, "href": href})
    return links


def _extract_buttons(soup: BeautifulSoup, limit: int = 6) -> List[str]:
    buttons: List[str] = []
    for tag in soup.find_all(["button", "input"], limit=limit):
        if tag.name == "input" and (tag.get("type") or "").lower() not in {"submit", "button"}:
            continue
        text = _clean_text(tag.get_text(" ", strip=True) or tag.get("value") or "")
        if text and text not in buttons:
            buttons.append(text)
    return buttons


def _extract_tables(soup: BeautifulSoup, limit: int = 2) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for table in soup.find_all("table", limit=limit):
        headers = [
            _clean_text(th.get_text(" ", strip=True))
            for th in table.find_all("th")
        ]
        rows: List[List[str]] = []
        for tr in table.find_all("tr"):
            cells = [
                _clean_text(td.get_text(" ", strip=True))
                for td in tr.find_all(["td"])
            ]
            if cells:
                rows.append(cells)
        if headers or rows:
            tables.append({"headers": headers, "rows": rows[:10]})
    return tables


def _extract_main_text(soup: BeautifulSoup, max_len: int = 500) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        return ""

    text = _clean_text(main.get_text(" ", strip=True))
    return text[:max_len]


def _detect_welcome_name(text: str) -> str:
    lowered = text.lower()
    for pattern in WELCOME_PATTERNS:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            return _clean_text(match.group(1))
    return ""


def _cookies_summary(client: httpx.Client) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for name, value in client.cookies.items():
        preview = value if len(value) <= 24 else value[:24] + "..."
        items.append({"name": name, "value_preview": preview})
    return items


def _user_from_api(api_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not api_data:
        return {}

    user = api_data.get("user")
    if isinstance(user, dict):
        return {
            key: str(user.get(key, ""))
            for key in ("username", "email", "role", "id", "name")
            if user.get(key) not in (None, "")
        }

    data = api_data.get("data")
    if isinstance(data, dict):
        return {
            key: str(data.get(key, ""))
            for key in ("username", "email", "role", "id", "name")
            if data.get(key) not in (None, "")
        }
    return {}


def build_screen_report(
    username: str,
    success: bool,
    *,
    response: Optional[httpx.Response] = None,
    client: Optional[httpx.Client] = None,
    post_login_url: str = "",
    api_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a unified post-login screen report for any account."""
    report: Dict[str, Any] = {
        "account": username,
        "login_status": "success" if success else "failed",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "page": {
            "title": "",
            "url": "",
            "headings": [],
            "main_text": "",
            "buttons": [],
            "links": [],
            "tables": [],
        },
        "user_detected": {"username": username},
        "session": {"cookies": [], "token": ""},
        "api_data": api_data or {},
    }

    if api_data:
        if api_data.get("token"):
            report["session"]["token"] = str(api_data["token"])
        elif api_data.get("access_token"):
            report["session"]["token"] = str(api_data["access_token"])
        report["user_detected"].update(_user_from_api(api_data))

    view_response = response
    if success and client and post_login_url.strip():
        try:
            headers: Dict[str, str] = {}
            token = report["session"].get("token") or ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
            view_response = client.get(post_login_url.strip(), headers=headers or None)
        except httpx.RequestError:
            view_response = response

    if view_response is not None:
        report["page"]["url"] = str(view_response.url)
        content_type = (view_response.headers.get("content-type") or "").lower()

        if "json" in content_type:
            try:
                body = view_response.json()
                if isinstance(body, dict):
                    report["api_data"] = {**report["api_data"], **body}
                    report["user_detected"].update(_user_from_api(body))
                    report["page"]["title"] = str(body.get("title") or "JSON Response")
                    report["page"]["main_text"] = _clean_text(
                        str(body.get("message") or body.get("summary") or body)[:500]
                    )
            except Exception:
                report["page"]["main_text"] = view_response.text[:500]
        else:
            soup = BeautifulSoup(view_response.text, "html.parser")
            title = soup.find("title")
            report["page"]["title"] = _clean_text(title.get_text()) if title else ""
            report["page"]["headings"] = _extract_headings(soup)
            report["page"]["main_text"] = _extract_main_text(soup)
            report["page"]["buttons"] = _extract_buttons(soup)
            report["page"]["links"] = _extract_links(soup)
            report["page"]["tables"] = _extract_tables(soup)

            welcome = _detect_welcome_name(report["page"]["main_text"])
            if welcome:
                report["user_detected"]["name"] = welcome
            elif report["page"]["headings"]:
                report["user_detected"]["headline"] = report["page"]["headings"][0]

    if client:
        report["session"]["cookies"] = _cookies_summary(client)

    return report

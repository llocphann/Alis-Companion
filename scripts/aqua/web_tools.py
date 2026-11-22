#!/usr/bin/env python3
"""Web Search, URL Reader, and Browser Control tools for Alis-Companion MCP (Eyes & Hands).

Provides:
- `web_search`: Zero-key live web search across DuckDuckGo HTML/Instant Answer, Wikipedia,
  GitHub API, and optional SearXNG / Brave Search API.
- `fetch_url_excerpt`: Clean HTML/JSON/Markdown reader that strips scripts/styles/boilerplate
  and extracts page title, headings, readable paragraphs, and links.
- `open_in_browser`: Opens URLs or search queries (Google, DuckDuckGo, YouTube, GitHub,
  Wikipedia) in the user's default desktop browser (`xdg-open`) while staying dry-run safe
  under automated test harnesses.
"""
from __future__ import annotations
from html.parser import HTMLParser
from pathlib import Path
import html
import http.client
import ipaddress
import json
import os
import re
import select
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class WebToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


class _ReadableHTMLParser(HTMLParser):
    _SKIP_TAGS = frozenset(("script", "style", "svg", "noscript", "iframe", "template", "head"))
    _BLOCK_TAGS = frozenset((
        "p", "div", "section", "article", "main", "aside", "header",
        "li", "tr", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
    ))
    _HEADING_TAGS = frozenset(("h1", "h2", "h3"))
    _INTERACTIVE_ROLES = frozenset((
        "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "tab", "menuitem", "switch",
    ))

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.skip_depth = 0
        self.current_heading = ""
        self.current_href = ""
        self.link_buf: list[str] = []
        self.title_parts: list[str] = []
        self.headings: list[str] = []
        self.heading_buf: list[str] = []
        self.blocks: list[str] = []
        self.current_block: list[str] = []
        self.links: list[dict] = []
        self.elements: list[dict] = []
        self._active_interactive: dict | None = None
        self._interactive_buf: list[str] = []

    def _flush_block(self) -> None:
        if not self.current_block:
            return
        line = re.sub(r"\s+", " ", "".join(self.current_block)).strip()
        self.current_block.clear()
        if len(line) >= 2 and (not self.blocks or self.blocks[-1] != line):
            self.blocks.append(line)

    def _register_element(self, elem: dict) -> None:
        if len(self.elements) >= 60:
            return
        ref = f"e{len(self.elements) + 1}"
        elem["ref"] = ref
        elem["tagRef"] = f"[{ref}]"
        self.elements.append(elem)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "title":
            self.in_title = True
            return
        if t in self._SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return
        if t in self._BLOCK_TAGS:
            self._flush_block()
        if t in self._HEADING_TAGS:
            self.current_heading = t
            self.heading_buf.clear()

        attr_map = {str(k or "").lower(): str(v or "") for k, v in attrs}
        el_id = attr_map.get("id", "").strip()
        el_name = attr_map.get("name", "").strip()
        aria_label = attr_map.get("aria-label", "").strip() or attr_map.get("title", "").strip()
        role_attr = attr_map.get("role", "").strip().lower()
        placeholder = attr_map.get("placeholder", "").strip()

        if t == "a":
            href = attr_map.get("href", "").strip()
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                self.current_href = href
                self.link_buf.clear()
                self._active_interactive = {
                    "tag": "a",
                    "role": role_attr or "link",
                    "type": "link",
                    "id": el_id,
                    "name": el_name,
                    "label": aria_label,
                    "href": href[:512],
                    "value": "",
                    "placeholder": "",
                    "selector": f"a#{el_id}" if el_id else f"a[href='{href[:80]}']",
                }
                self._interactive_buf.clear()
        elif t in ("input", "select", "textarea"):
            inp_type = attr_map.get("type", "text" if t != "select" else "select").strip().lower() or "text"
            if inp_type == "hidden":
                return
            role = (
                "button"
                if inp_type in ("submit", "button", "reset")
                else ("checkbox" if inp_type == "checkbox" else ("radio" if inp_type == "radio" else (role_attr or "textbox")))
            )
            val = attr_map.get("value", "").strip()
            lbl = aria_label or placeholder or el_name or el_id or val or inp_type
            sel = f"{t}#{el_id}" if el_id else (f"{t}[name='{el_name}']" if el_name else f"{t}[type='{inp_type}']")
            if t == "input":
                self._register_element({
                    "tag": t,
                    "role": role,
                    "type": inp_type,
                    "id": el_id,
                    "name": el_name,
                    "label": lbl[:120],
                    "href": "",
                    "value": val[:200],
                    "placeholder": placeholder[:120],
                    "selector": sel,
                })
            else:
                self._active_interactive = {
                    "tag": t,
                    "role": "combobox" if t == "select" else "textbox",
                    "type": t,
                    "id": el_id,
                    "name": el_name,
                    "label": lbl[:120],
                    "href": "",
                    "value": val[:200],
                    "placeholder": placeholder[:120],
                    "selector": sel,
                }
                self._interactive_buf.clear()
        elif t == "button" or role_attr in self._INTERACTIVE_ROLES:
            sel = f"{t}#{el_id}" if el_id else (f"{t}[name='{el_name}']" if el_name else t)
            self._active_interactive = {
                "tag": t,
                "role": role_attr or "button",
                "type": attr_map.get("type", "button").strip().lower() or "button",
                "id": el_id,
                "name": el_name,
                "label": aria_label,
                "href": attr_map.get("href", "").strip()[:512],
                "value": attr_map.get("value", "").strip()[:200],
                "placeholder": placeholder[:120],
                "selector": sel,
            }
            self._interactive_buf.clear()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self.in_title = False
            return
        if t in self._SKIP_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return
        if t in self._HEADING_TAGS and self.current_heading == t:
            h_text = re.sub(r"\s+", " ", "".join(self.heading_buf)).strip()
            if h_text and h_text not in self.headings:
                self.headings.append(h_text)
            self.current_heading = ""
            self.heading_buf.clear()
        if self._active_interactive and self._active_interactive.get("tag") == t:
            inner_txt = re.sub(r"\s+", " ", "".join(self._interactive_buf)).strip()
            elem = self._active_interactive
            self._active_interactive = None
            self._interactive_buf.clear()
            if not elem.get("label"):
                elem["label"] = (inner_txt or elem.get("name") or elem.get("id") or elem.get("value") or t)[:120]
            if elem.get("label") or elem.get("href") or elem.get("name") or elem.get("id"):
                self._register_element(elem)
        if t == "a" and self.current_href:
            l_text = re.sub(r"\s+", " ", "".join(self.link_buf)).strip()
            if l_text and len(self.links) < 25:
                self.links.append({"text": l_text[:120], "url": self.current_href[:512]})
            self.current_href = ""
            self.link_buf.clear()
        if t in self._BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_depth > 0:
            return
        self.current_block.append(data)
        if self.current_heading:
            self.heading_buf.append(data)
        if self.current_href:
            self.link_buf.append(data)
        if self._active_interactive is not None:
            self._interactive_buf.append(data)


def html_to_markdown(raw_html: str) -> str:
    """Convert raw HTML/XHTML into clean readable Markdown text using `_ReadableHTMLParser`."""
    parser = _ReadableHTMLParser()
    try:
        parser.feed(str(raw_html or ""))
        parser._flush_block()
    except Exception:
        pass
    out_lines: list[str] = []
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    if title:
        out_lines.append(f"# {title}")
    heading_set = set(parser.headings)
    for blk in parser.blocks:
        if blk in heading_set:
            out_lines.append(f"## {blk}")
        else:
            out_lines.append(blk)
    return "\n\n".join(out_lines).strip()


class _DuckDuckGoHTMLParser(HTMLParser):
    """Parse DuckDuckGo HTML search results (`https://html.duckduckgo.com/html/`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._in_title_link = False
        self._in_snippet = False
        self._skip_current = False
        self._cur_url = ""
        self._title_buf: list[str] = []
        self._snippet_buf: list[str] = []

    @staticmethod
    def _unwrap_ddg_url(raw_href: str) -> str:
        href = str(raw_href or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/?" in href or "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("uddg"):
                return urllib.parse.unquote(qs["uddg"][0])
        return href

    @staticmethod
    def _is_ad_url(url: str) -> bool:
        u = str(url or "").lower()
        return "duckduckgo.com/y.js" in u or "ad_domain=" in u or "bing.com/aclick" in u

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = dict(attrs)
        cls = str(attr_map.get("class") or "")
        if t == "a" and "result__a" in cls:
            self._in_title_link = True
            self._cur_url = self._unwrap_ddg_url(str(attr_map.get("href") or ""))
            self._skip_current = self._is_ad_url(self._cur_url)
            self._title_buf.clear()
        elif t in ("a", "div", "span") and "result__snippet" in cls:
            self._in_snippet = True
            self._snippet_buf.clear()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "a" and self._in_title_link:
            self._in_title_link = False
            title = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
            if not self._skip_current and title and self._cur_url and self._cur_url.startswith(("http://", "https://")):
                self.results.append({
                    "title": title[:160],
                    "url": self._cur_url[:512],
                    "snippet": "",
                    "source": "duckduckgo",
                })
        elif t in ("a", "div", "span") and self._in_snippet:
            self._in_snippet = False
            snippet = re.sub(r"\s+", " ", "".join(self._snippet_buf)).strip()
            if not self._skip_current and snippet and self.results and not self.results[-1].get("snippet"):
                self.results[-1]["snippet"] = snippet[:320]

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._title_buf.append(data)
        elif self._in_snippet:
            self._snippet_buf.append(data)


def normalize_url(raw_url: str) -> str:
    u = str(raw_url or "").strip().strip("'\"`<>")
    if not u:
        raise WebToolError("invalid_url", "URL must not be empty.")
    if not u.startswith(("http://", "https://")):
        if re.match(r"^[A-Za-z0-9.\-]+(?:\.[A-Za-z]{2,}|:\d+)(?:/.*)?$", u):
            u = "https://" + u
        else:
            raise WebToolError("invalid_url", f"Unsupported URL scheme in '{u}'. Use http:// or https://.")
    if any(ord(ch) > 127 for ch in u):
        try:
            parts = urllib.parse.urlsplit(u)
            netloc = parts.netloc.encode("idna").decode("ascii") if any(ord(c) > 127 for c in parts.netloc) else parts.netloc
            safe_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@!$&'()*+,;=-._~%")
            safe_query = urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&/:@!$'()*+,;-._~%?")
            safe_frag = urllib.parse.quote(urllib.parse.unquote(parts.fragment), safe="/:@!$&'()*+,;=-._~%")
            u = urllib.parse.urlunsplit((parts.scheme, netloc, safe_path, safe_query, safe_frag))
        except Exception:
            pass
    return u


def _format_dom_snapshot_lines(elements: list[dict], base_url: str = "") -> list[str]:
    lines: list[str] = []
    for el in elements[:40]:
        ref = str(el.get("ref") or "e1")
        role = str(el.get("role") or el.get("tag") or "widget")
        lbl = str(el.get("label") or el.get("name") or el.get("id") or "")
        href = str(el.get("href") or "")
        if href and base_url:
            href = urllib.parse.urljoin(base_url, href)
        val = str(el.get("value") or "")
        ph = str(el.get("placeholder") or "")
        extra = []
        if el.get("name"):
            extra.append(f'name="{el["name"]}"')
        if ph and ph != lbl:
            extra.append(f'placeholder="{ph}"')
        if val:
            extra.append(f'value="{val}"')
        if href:
            extra.append(f"-> {href}")
        suffix = (" " + " ".join(extra)) if extra else ""
        lines.append(f"[{ref}] {role} \"{lbl}\"{suffix}")
    return lines


_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.ip_network("64:ff9b:1::/48")


def _is_public_ip(ip_obj) -> bool:
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_ip(mapped)
    if isinstance(ip_obj, ipaddress.IPv6Address):
        # Guard NAT64 well-known (64:ff9b::/96) and local-use (64:ff9b:1::/48) prefixes;
        # on Python 3.14 ip_address("64:ff9b::127.0.0.1").is_global is True!
        if ip_obj in _NAT64_LOCAL_USE:
            return False
        if ip_obj in _NAT64_WELL_KNOWN:
            embedded_v4 = ipaddress.IPv4Address(int(ip_obj) & 0xFFFFFFFF)
            return _is_public_ip(embedded_v4)
        sixtofour = getattr(ip_obj, "sixtofour", None)
        if sixtofour is not None and not _is_public_ip(sixtofour):
            return False
        teredo = getattr(ip_obj, "teredo", None)
        if teredo is not None:
            for t_ip in teredo:
                if not _is_public_ip(t_ip):
                    return False
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return False
    return bool(ip_obj.is_global)


def _resolve_and_validate_host(bare_host: str, port: int, allow_local: bool = False) -> tuple[bool, str, list[str]]:
    if not bare_host:
        return False, "missing_host", []
    if allow_local:
        return True, "", []
    low_host = bare_host.lower()
    if low_host in ("localhost", "localhost.localdomain") or low_host.endswith((".local", ".internal")):
        return False, "loopback_or_local_blocked", []
    try:
        direct_ip = ipaddress.ip_address(bare_host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None:
        if not _is_public_ip(direct_ip):
            return False, "private_or_loopback_ip_blocked", []
        return True, "", [str(direct_ip)]
    try:
        infos = socket.getaddrinfo(bare_host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False, "dns_resolution_failed", []
    if not infos:
        return False, "dns_resolution_empty", []
    resolved_ips: list[str] = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else ()
        ip_str = str(sockaddr[0]).split("%")[0] if sockaddr else ""
        if not ip_str:
            continue
        try:
            resolved_ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, "invalid_resolved_ip", []
        if not _is_public_ip(resolved_ip):
            return False, "private_or_loopback_ip_blocked", []
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)
    if not resolved_ips:
        return False, "dns_resolution_empty", []
    return True, "", resolved_ips


def _is_loopback_host(host: str) -> bool:
    low = str(host or "").strip().strip("[]").lower()
    if low in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip_obj = ipaddress.ip_address(low)
        mapped = getattr(ip_obj, "ipv4_mapped", None)
        if mapped is not None:
            return bool(mapped.is_loopback)
        return bool(ip_obj.is_loopback)
    except ValueError:
        return False


def validate_external_url(url: str, allow_local: bool = False, *, allow_loopback_only: bool = False) -> tuple[bool, str]:
    raw = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return False, "invalid_url"
    if parts.scheme.lower() not in ("http", "https"):
        return False, "unsupported_scheme"
    host = (parts.hostname or "").strip()
    if not host:
        return False, "missing_host"
    bare_host = host.strip("[]")
    if allow_local:
        if allow_loopback_only and not _is_loopback_host(bare_host):
            # When scoped to loopback-only (e.g. local SearXNG/MCP redirect target),
            # never allow jumping to link-local/metadata/RFC1918 private IPs.
            ok, reason, _ips = _resolve_and_validate_host(bare_host, port, allow_local=False)
            return ok, reason
        return True, ""
    ok, reason, _ips = _resolve_and_validate_host(bare_host, port, allow_local=False)
    return ok, reason


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        bare_host = str(self.host or "").strip("[]")
        ok, reason, ips = _resolve_and_validate_host(bare_host, int(self.port or 80), allow_local=False)
        if not ok or not ips:
            raise WebToolError("ssrf_blocked", f"Blocked connection to unsafe target ({reason}): {self.host}")
        last_exc: Exception | None = None
        for ip_str in ips:
            try:
                self.sock = socket.create_connection((ip_str, int(self.port or 80)), self.timeout, self.source_address)
                if getattr(self, "_tunnel_host", None):
                    self._tunnel()
                return
            except OSError as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise WebToolError("ssrf_blocked", f"Failed to connect to validated IPs for {self.host}")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        bare_host = str(self.host or "").strip("[]")
        ok, reason, ips = _resolve_and_validate_host(bare_host, int(self.port or 443), allow_local=False)
        if not ok or not ips:
            raise WebToolError("ssrf_blocked", f"Blocked TLS connection to unsafe target ({reason}): {self.host}")
        last_exc: Exception | None = None
        for ip_str in ips:
            raw_sock = None
            try:
                raw_sock = socket.create_connection((ip_str, int(self.port or 443)), self.timeout, self.source_address)
                if getattr(self, "_tunnel_host", None):
                    self.sock = raw_sock
                    self._tunnel()
                    server_hostname = self._tunnel_host
                else:
                    server_hostname = bare_host
                self.sock = self._context.wrap_socket(raw_sock, server_hostname=server_hostname)
                return
            except OSError as exc:
                if raw_sock is not None:
                    try:
                        raw_sock.close()
                    except OSError:
                        pass
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise WebToolError("ssrf_blocked", f"Failed to connect to validated TLS IPs for {self.host}")


class _PinnedSafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedSafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_local: bool = False, max_hops: int = 3):
        self.allow_local = allow_local
        self.max_hops = max_hops
        self._hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._hops += 1
        if self._hops > self.max_hops:
            raise urllib.error.HTTPError(newurl, code, "Too many HTTP redirects", headers, fp)
        ok, reason = validate_external_url(
            newurl,
            allow_local=self.allow_local,
            allow_loopback_only=self.allow_local,
        )
        if not ok:
            raise WebToolError("ssrf_blocked", f"Blocked redirect to unsafe target ({reason}): {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_safe_opener(allow_local: bool = False) -> urllib.request.OpenerDirector:
    redirect_handler = _SafeRedirectHandler(allow_local=allow_local, max_hops=3)
    if allow_local:
        # Bypass system/VPN HTTP_PROXY for explicit localhost / loopback probes
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), redirect_handler)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedSafeHTTPHandler(),
        _PinnedSafeHTTPSHandler(),
        redirect_handler,
    )


def fetch_url_excerpt(url: str, max_chars: int = 3600, timeout_sec: float = 8.0) -> dict:
    """Fetch a web URL and return clean readable text, headings, links, and interactive DOM elements."""
    raw_u = str(url or "").strip().strip("'\"`<>")
    norm_url = normalize_url(url)

    # Support deterministic offline fixture in automated tests
    fixture_json = os.environ.get("AQUA_TEST_WEB_FIXTURE", "").strip()
    if fixture_json:
        try:
            fx = json.loads(fixture_json)
            pages_map = fx.get("pages") if isinstance(fx, dict) and isinstance(fx.get("pages"), dict) else {}
            matched_key = norm_url if norm_url in pages_map else (raw_u if raw_u in pages_map else "")
            if matched_key:
                page = pages_map[matched_key]
                if page.get("html"):
                    p = _ReadableHTMLParser()
                    p.feed(str(page["html"]))
                    p._flush_block()
                    elems = p.elements[:40]
                    return {
                        "url": norm_url,
                        "title": str(page.get("title") or "".join(p.title_parts).strip() or norm_url),
                        "headings": list(page.get("headings") or p.headings[:12]),
                        "lines": list(page.get("lines") or p.blocks[:35]),
                        "links": list(page.get("links") or p.links[:20]),
                        "elements": elems,
                        "domSnapshot": _format_dom_snapshot_lines(elems, norm_url),
                    }
                elems = list(page.get("elements") or [])
                return {
                    "url": norm_url,
                    "title": str(page.get("title") or norm_url),
                    "headings": list(page.get("headings") or []),
                    "lines": list(page.get("lines") or []),
                    "links": list(page.get("links") or []),
                    "elements": elems,
                    "domSnapshot": _format_dom_snapshot_lines(elems, norm_url),
                }
        except Exception:
            pass

    allow_local = os.environ.get("ALIS_TEST_ALLOW_LOCAL_WEB") == "1"
    ok_url, ssrf_reason = validate_external_url(norm_url, allow_local=allow_local)
    if not ok_url:
        raise WebToolError("ssrf_blocked", f"Refusing unsafe URL target ({ssrf_reason}): {norm_url}")

    req = urllib.request.Request(
        norm_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
        },
    )
    opener = build_safe_opener(allow_local=allow_local)
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            final_url = str(resp.geturl() or norm_url)
            ctype = str(resp.headers.get("Content-Type") or "text/html").lower()
            raw_bytes = resp.read(384 * 1024)
    except WebToolError:
        raise
    except urllib.error.HTTPError as exc:
        raise WebToolError("http_error", f"HTTP {exc.code} when fetching {norm_url}") from exc
    except Exception as exc:
        raise WebToolError("fetch_failed", f"Could not fetch {norm_url}: {exc}") from exc

    text_body = raw_bytes.decode("utf-8", errors="replace")

    if "application/json" in ctype:
        try:
            parsed_json = json.loads(text_body)
            pretty = json.dumps(parsed_json, ensure_ascii=False, indent=2)[:max_chars]
            lines = [ln for ln in pretty.splitlines() if ln.strip()][:40]
        except Exception:
            lines = [ln.strip() for ln in text_body[:max_chars].splitlines() if ln.strip()][:40]
        return {
            "url": final_url,
            "title": final_url,
            "headings": [],
            "lines": lines,
            "links": [],
            "elements": [],
            "domSnapshot": [],
        }

    if "text/plain" in ctype or "text/markdown" in ctype:
        lines = [ln.strip() for ln in text_body[:max_chars].splitlines() if ln.strip()][:40]
        return {
            "url": final_url,
            "title": lines[0][:120] if lines else final_url,
            "headings": [],
            "lines": lines,
            "links": [],
            "elements": [],
            "domSnapshot": [],
        }

    parser = _ReadableHTMLParser()
    try:
        parser.feed(text_body)
        parser._flush_block()
    except Exception:
        pass

    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip() or final_url
    heading_set = set(parser.headings)
    substantive_blocks = [
        b for b in parser.blocks
        if (len(b) >= 35 or b in heading_set)
        and not re.match(r"^(?:skip to content|no results|log in|sign in|sign up|cookie policy|accept cookies)$", b, flags=re.I)
    ]
    source_blocks = substantive_blocks if len(substantive_blocks) >= 4 else parser.blocks
    lines: list[str] = []
    char_count = 0
    for blk in source_blocks:
        if len(blk) < 3:
            continue
        lines.append(blk)
        char_count += len(blk)
        if char_count >= max_chars or len(lines) >= 35:
            break

    resolved_links = []
    for lnk in parser.links[:20]:
        resolved_href = urllib.parse.urljoin(final_url, lnk["url"])
        if resolved_href.startswith(("http://", "https://")):
            resolved_links.append({"text": lnk["text"], "url": resolved_href})

    resolved_elements = []
    for el in parser.elements[:45]:
        ecopy = dict(el)
        if ecopy.get("href"):
            ecopy["href"] = urllib.parse.urljoin(final_url, ecopy["href"])
        resolved_elements.append(ecopy)

    return {
        "url": final_url,
        "title": title[:160],
        "headings": parser.headings[:12],
        "lines": lines,
        "links": resolved_links,
        "elements": resolved_elements,
        "domSnapshot": _format_dom_snapshot_lines(resolved_elements, final_url),
    }


def _search_duckduckgo_html(query: str, max_results: int = 6, timeout_sec: float = 6.0) -> list[dict]:
    encoded_q = urllib.parse.urlencode({"q": query})
    url = f"https://html.duckduckgo.com/html/?{encoded_q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw_html = resp.read(256 * 1024).decode("utf-8", errors="replace")
    parser = _DuckDuckGoHTMLParser()
    parser.feed(raw_html)
    return parser.results[:max_results]


def _search_duckduckgo_instant(query: str, max_results: int = 5, timeout_sec: float = 5.0) -> list[dict]:
    encoded_q = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    })
    url = f"https://api.duckduckgo.com/?{encoded_q}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read(128 * 1024).decode("utf-8", errors="replace"))
    results: list[dict] = []
    abstract_text = str(data.get("AbstractText") or "").strip()
    abstract_url = str(data.get("AbstractURL") or "").strip()
    heading = str(data.get("Heading") or query).strip()
    if abstract_text and abstract_url:
        results.append({
            "title": heading,
            "url": abstract_url,
            "snippet": abstract_text[:320],
            "source": "duckduckgo-instant",
        })
    for topic in (data.get("RelatedTopics") or []):
        if len(results) >= max_results:
            break
        if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
            txt = str(topic["Text"]).strip()
            results.append({
                "title": txt.split(" - ", 1)[0][:120],
                "url": str(topic["FirstURL"]),
                "snippet": txt[:280],
                "source": "duckduckgo-instant",
            })
    return results[:max_results]


def _search_wikipedia(query: str, lang: str = "vi", max_results: int = 5, timeout_sec: float = 5.0) -> list[dict]:
    wiki_host = "vi.wikipedia.org" if lang == "vi" else "en.wikipedia.org"
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(max_results),
        "format": "json",
    })
    url = f"https://{wiki_host}/w/api.php?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read(128 * 1024).decode("utf-8", errors="replace"))
    search_items = ((data.get("query") or {}).get("search")) or []
    results: list[dict] = []
    for item in search_items[:max_results]:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        title = str(item["title"]).strip()
        raw_snip = str(item.get("snippet") or "")
        clean_snip = html.unescape(re.sub(r"<[^>]+>", "", raw_snip)).strip()
        page_url = f"https://{wiki_host}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        results.append({
            "title": f"{title} (Wikipedia)",
            "url": page_url,
            "snippet": clean_snip[:300],
            "source": f"wikipedia-{lang}",
        })
    return results


def _search_github_repos(query: str, max_results: int = 5, timeout_sec: float = 6.0) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": str(max_results),
    })
    url = f"https://api.github.com/search/repositories?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read(192 * 1024).decode("utf-8", errors="replace"))
    results: list[dict] = []
    for repo in (data.get("items") or [])[:max_results]:
        if not isinstance(repo, dict):
            continue
        full_name = str(repo.get("full_name") or "")
        html_url = str(repo.get("html_url") or "")
        desc = str(repo.get("description") or "No description").strip()
        stars = int(repo.get("stargazers_count") or 0)
        lang = str(repo.get("language") or "")
        meta = f"★ {stars}" + (f" · {lang}" if lang else "")
        if full_name and html_url:
            results.append({
                "title": f"{full_name} ({meta})",
                "url": html_url,
                "snippet": desc[:280],
                "source": "github",
            })
    return results


def _search_searxng(base_url: str, query: str, max_results: int = 6, timeout_sec: float = 5.0) -> list[dict]:
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{base_url.rstrip('/')}/search?{params}"
    parsed_host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    explicit_loopback = parsed_host in ("127.0.0.1", "localhost", "::1")
    ok_url, reason = validate_external_url(url, allow_local=explicit_loopback)
    if not ok_url:
        raise WebToolError("ssrf_blocked", f"Blocked SearXNG request ({reason}): {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = build_safe_opener(allow_local=explicit_loopback)
    with opener.open(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read(256 * 1024).decode("utf-8", errors="replace"))
    results: list[dict] = []
    for item in (data.get("results") or [])[:max_results]:
        if isinstance(item, dict) and item.get("url") and item.get("title"):
            results.append({
                "title": str(item["title"])[:160],
                "url": str(item["url"])[:512],
                "snippet": str(item.get("content") or "")[:300],
                "source": "searxng",
            })
    return results


def short_source_domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(str(url or "").strip()).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "nguồn"
    except Exception:
        return "nguồn"


def format_markdown_sources(results: list[dict], max_sources: int = 5) -> str:
    seen: set[str] = set()
    badges: list[str] = []
    for r in results or []:
        url = str(r.get("url") or "").strip()
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        domain = short_source_domain(url)
        badges.append(f"[{domain}]({url})")
        if len(badges) >= max_sources:
            break
    return " · ".join(badges)


_LOW_QUALITY_SEARCH_DOMAINS = (
    "studocu.com",
    "studocu.vn",
    "scribd.com",
    "coursehero.com",
    "chegg.com",
    "123docz.net",
    "123doc.net",
    "123doc.org",
    "tailieu.vn",
    "luanvan.co",
    "luanvan.net.vn",
    "doc.edu.vn",
    "hoc24.vn",
    "vietjack.com",
    "loigiaihay.com",
    "vndoc.com",
    "violet.vn",
    "hocmai.vn",
    "tuyensinh247.com",
    "hoidap247.com",
    "lazi.vn",
    "slideshare.net",
    "ieltsgeneral.info",
)

_SEARCH_STOPWORDS = frozenset((
    "lên", "len", "lập", "lap", "một", "mot", "1", "lúc", "luc",
    "vào", "vao", "giờ", "gio", "cho", "của", "cua", "và", "va",
    "các", "cac", "những", "nhung", "cách", "cach", "với", "voi",
    "trên", "tren", "trong", "để", "de", "từ", "tu", "hay", "hoặc", "hoac",
    "checklist", "ideas", "guide", "plan", "planning", "schedule", "itinerary", "template",
    "tomorrow", "tonight", "at", "for", "the",
    "a", "an", "in", "on", "to", "of", "with", "and", "or", "how", "best", "top",
))


def _strip_meta_parentheticals(text: str) -> str:
    """Remove parenthetical or bracketed search/internet/synthesis meta-instructions."""
    def _repl(match: re.Match) -> str:
        inner = match.group(1).strip()
        if re.search(
            r"\b(?:internet|web|mạng|mang|google|online|wikipedia|tra\s+cứu|tra\s+cuu|tìm\s+kiếm|tim\s+kiem|"
            r"tổng\s+hợp|tong\s+hop|sử\s+dụng|su\s+dung|dùng|dung|tham\s+khảo|tham\s+khao|search|lookup|look\s+up|"
            r"summarize|synthesis|gather\s+info|use\s+the\s+web|use\s+the\s+internet)\b",
            inner,
            flags=re.I,
        ):
            return " "
        return match.group(0)

    out = re.sub(r"\(([^()]{3,160})\)", _repl, text)
    out = re.sub(r"\[([^\[\]]{3,160})\]", _repl, out)
    return out


def clean_search_query(raw_query: str, for_plan: bool = False) -> str:
    """Strip conversational search permissions, slash prefixes, and meta-instructions from a search query."""
    s = str(raw_query or "").strip()
    if not s:
        return ""
    s = _strip_meta_parentheticals(s).strip()
    m_multi = _MULTI_CLAUSE_BROWSER_ACTION_RE.match(s) if not for_plan else None
    if m_multi and m_multi.group("rest").strip():
        s = m_multi.group("rest").strip()
    else:
        # Strip conversational spoken filler / request lead-ins at the start
        for _ in range(4):
            prev_s = s
            s = _LEADING_FILLER_RE.sub("", s).strip()
            s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
            if not for_plan:
                s = _LEADING_POLITE_RE.sub("", s).strip()
                s = _LEADING_BROWSER_NAV_RE.sub("", s).strip()
                s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
                m_srch = _LEADING_SEARCH_ACTION_RE.match(s)
                if m_srch:
                    s = s[m_srch.end():].strip()
                    break
            if s == prev_s:
                break
    # Strip leading slash commands and explicit search/plan prefixes
    s = re.sub(
        r"^(?:/(?:plan|web|search)\s+|"
        r"(?:hãy\s+|vui\s+lòng\s+|please\s+|can\s+you\s+|could\s+you\s+|help\s+me\s+)*"
        r"(?:tìm\s+kiếm|tìm|tra\s+cứu|sử\s+dụng|dùng|tham\s+khảo|search|look\s+up|find|use)\s+"
        r"(?:thông\s+tin\s+)?"
        r"(?:trên\s+|ở\s+|on\s+|in\s+|via\s+)?(?:the\s+)?"
        r"(?:web|mạng|internet|google|online|wikipedia|youtube)\s*"
        r"(?:(?:để|và|rồi|to|and)\s+(?:tổng\s+hợp|tra\s+cứu|tìm\s+kiếm|tìm|lấy|thu\s+thập|summarize|gather|find)(?:\s+(?:thông\s+tin|dữ\s+liệu|ý\s+tưởng|info|information|ideas))?(?:\s+(?:cho\s+tôi|cho\s+mình|giúp\s+tôi|giúp\s+mình|for\s+me))?\s*)?"
        r"(?:về\s+|cho\s+|for\s+|about\s+|:|-)*)",
        "",
        s,
        flags=re.I,
    ).strip()
    # Strip leading permission clause if placed at the start: "You can search the internet. Plan a party..."
    s = re.sub(
        r"^(?:(?:you\s+(?:can|may|could|should)|feel\s+free\s+to|please|bạn\s+(?:có\s+thể|hãy|cứ)|hãy|cứ)\s+)?"
        r"(?:(?:use|using|sử\s+dụng|su\s+dung|dùng|dung|tham\s+khảo|tham\s+khao)\s+(?:the\s+)?(?:internet|web|mạng|mang|google|online)(?:\s+search)?|"
        r"(?:search|look\s+up|browse|check|tra\s+cứu|tìm\s+kiếm|tìm)\s+(?:thông\s+tin\s+)?(?:trên\s+|ở\s+|on\s+|in\s+|via\s+)?(?:the\s+)?(?:internet|web|mạng|online|google))"
        r"(?:\s+(?:(?:để|và|to|and)\s+(?:tổng\s+hợp|tìm|lấy|summarize|gather)(?:\s+(?:thông\s+tin|info|information))?))?"
        r"(?:\s+(?:for\s+(?:me|this|it|ideas|info)|cho\s+tôi|cho\s+mình|giúp\s+tôi|giúp\s+mình|nhé|nha))?\s*(?:[.,;!\-—]+|\b(?:and|to|for|và|để)\b)\s*",
        "",
        s,
        flags=re.I,
    ).strip()
    # Strip trailing conversational search permission / meta-instruction sentences or clauses (including compound follow-ups like "and check ideas for me" / "hãy sử dụng internet để tổng hợp thông tin cho tôi")
    for _ in range(3):
        s = re.sub(
            r"(?:[.,;!?—\-()\[\]]+|\s+\b(?:and|also|or|và|hoặc|để|nhưng)\b)?\s*"
            r"(?:(?:you\s+(?:can|may|could|should)|can\s+you|could\s+you|please|feel\s+free\s+to|go\s+ahead\s+and|"
            r"bạn\s+(?:có\s+thể|hãy|cứ)|hãy|hay|vui\s+lòng|vui\s+long|cứ|nhớ)\s+)?"
            r"(?:(?:use|using|utilize|sử\s+dụng|su\s+dung|dùng|dung|tham\s+khảo|tham\s+khao|kết\s+hợp|ket\s+hop)\s+(?:thông\s+tin\s+)?(?:trên\s+|ở\s+|từ\s+|on\s+|in\s+|from\s+)?(?:the\s+)?(?:internet|web|mạng|mang|google|online)(?:\s+search)?|"
            r"(?:search|look\s+(?:it\s+)?up|find\s+(?:it|info|information|ideas)|check|browse|tra\s+cứu|tra\s+cuu|tìm\s+kiếm|tim\s+kiem|tìm|tim|lên|len)\s+"
            r"(?:thông\s+tin\s+|thong\s+tin\s+)?(?:trên\s+|ở\s+|on\s+|in\s+|via\s+|across\s+)?(?:the\s+)?(?:internet|web|mạng|mang|online|google|wikipedia))"
            r"(?:\s+(?:if\s+you\s+(?:need|want|like)|(?:and|to|và|va|để|de|rồi|roi)\s+(?:check|find|get|see|gather|summarize|synthesize|plan|kiểm\s+tra|kiem\s+tra|tìm|tim|xem|tổng\s+hợp|tong\s+hop|thu\s+thập|thu\s+thap|lấy|lay|lên\s+kế\s+hoạch|lập\s+kế\s+hoạch)(?:\s+(?:lại|ra|thêm|các|những|ideas|info|information|it|them|ý\s+tưởng|y\s+tuong|thông\s+tin|thong\s+tin|dữ\s+liệu|du\s+lieu))*|for\s+(?:me|this|it|ideas|info|information)|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|cho\s+tôi|cho\s+toi|cho\s+mình|cho\s+minh|nhé|nhe|nha|đi|di))*"
            r"\s*[.?!)\]]*$",
            "",
            s,
            flags=re.I,
        ).strip()
    # Convert inline second-sentence search-for topic instructions (e.g. ". Search online for games and food ideas") into the topic words
    s = re.sub(
        r"(?:[.,;!?]+\s+)"
        r"(?:(?:please|can\s+you|could\s+you|you\s+can|feel\s+free\s+to|hãy|vui\s+lòng|bạn\s+có\s+thể)\s+)*"
        r"(?:search|look\s+up|find|check|browse|tìm\s+kiếm|tìm|tra\s+cứu|sử\s+dụng|dùng)\s+"
        r"(?:thông\s+tin\s+)?"
        r"(?:(?:trên\s+|ở\s+|on\s+|in\s+|via\s+)?(?:the\s+)?(?:internet|web|mạng|online|google|wikipedia)\s+)?"
        r"(?:for|about|về|cho)\s+",
        " ",
        s,
        flags=re.I,
    ).strip()
    # Strip trailing "và lên kế hoạch giúp tôi" / "để tổng hợp thông tin cho tôi" / "and plan it for me" meta-clauses when searching
    s = re.sub(
        r"\s+(?:và|va|để|de|and|to)\s+(?:hãy\s+|hay\s+)?(?:lên\s+kế\s+hoạch|lập\s+kế\s+hoạch|tổng\s+hợp(?:\s+lại|\s+thông\s+tin|\s+dữ\s+liệu)?|tong\s+hop(?:\s+thong\s+tin)?|plan(?:\s+it)?|summarize(?:\s+it|\s+information)?)(?:\s+(?:giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|cho\s+tôi|cho\s+toi|cho\s+mình|cho\s+minh|for\s+me))?\s*[.?!)\]]*$",
        "",
        s,
        flags=re.I,
    ).strip()
    if for_plan:
        s = re.sub(
            r"^(?:hãy\s+|vui\s+lòng\s+|giúp\s+tôi\s+|giúp\s+mình\s+|please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|help\s+me\s+|i\s+want\s+to\s+|i\s+need\s+to\s+|we\s+need\s+to\s+)+",
            "",
            s,
            flags=re.I,
        ).strip()
        s = re.sub(
            r"^(?:lập\s+kế\s+hoạch|lên\s+kế\s+hoạch|thiết\s+kế\s+kế\s+hoạch|create\s+a\s+plan\s+for|make\s+a\s+plan\s+for|build\s+a\s+plan\s+for|implementation\s+plan\s+for)\s*",
            "",
            s,
            flags=re.I,
        ).strip()
        s = re.sub(r"^(?:cho\s+|về\s+|ve\s+|for\s+|about\s+)*(?:1\s+|một\s+|mot\s+|a\s+|an\s+)?", "", s, flags=re.I).strip()
        s = re.sub(r"\b(?:for\s+me|help\s+me|cho\s+tôi|cho\s+mình|giúp\s+tôi|giúp\s+mình)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .,;:!?-—\"'()[]")
    if for_plan and s:
        low = s.lower()
        is_code = bool(re.search(r"\b(?:code|repo|git|python|qml|rust|js|ts|api|database|sql|bug|test|shader|module|refactor)\b", low))
        has_plan_kw = bool(re.search(r"\b(?:checklist|guide|ideas|schedule|itinerary|template|kế\s+hoạch|lịch\s+trình|cẩm\s+nang|thực\s+đơn|trò\s+chơi)\b", low))
        has_vi = bool(re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", s))
        if not is_code and not has_plan_kw and len(s) <= 80:
            if has_vi and re.search(r"\b(?:tiệc|tiec|sinh\s+nhật|liên\s+hoan|party)\b", low):
                s = f"tổ chức {s} thực đơn trò chơi"
            else:
                s = f"{s} {'kế hoạch chi tiết' if has_vi else 'checklist ideas'}"
    return (s or str(raw_query or "").strip())[:140]


def _filter_and_rank_search_results(results: list[dict], query: str, limit: int = 6) -> list[dict]:
    """Filter out homework/paywall dump domains and rank/prune off-topic search results."""
    if not results:
        return []
    q_low = str(query or "").lower()
    # Strip synthetic planning/time boilerplate before extracting core topic tokens
    q_core = re.sub(
        r"\b(?:kế\s+hoạch(?:\s+chi\s+tiết)?|lập\s+kế\s+hoạch|lên\s+kế\s+hoạch|cách\s+tổ\s+chức|kinh\s+nghiệm\s+tổ\s+chức|"
        r"ý\s+tưởng\s+tổ\s+chức|danh\s+sách\s+chuẩn\s+bị|checklist\s+ideas|vào\s+ngày\s+mai|ngày\s+mai|hôm\s+nay|"
        r"tuần\s+sau|lúc\s+\d{1,2}\s*(?:giờ|h)(?:\s*(?:sáng|trưa|chiều|tối|đêm))?)\b",
        " ",
        q_low,
        flags=re.I,
    )
    has_vi_query = bool(re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", q_low))
    raw_tokens = [
        tok for tok in re.findall(r"[0-9a-zA-ZăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]+", q_core)
        if len(tok) >= 2 and tok not in _SEARCH_STOPWORDS and not tok.isdigit()
    ]
    if not raw_tokens:
        raw_tokens = [
            tok for tok in re.findall(r"[0-9a-zA-ZăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]+", q_low)
            if len(tok) >= 2 and tok not in _SEARCH_STOPWORDS and not tok.isdigit()
        ]
    # Keep track of diacritic tokens so Vietnamese homographs (e.g. "tiệc" vs "tiếc") are not confused
    diacritic_tokens = [t for t in raw_tokens if re.search(r"[ăâđêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", t)]
    party_aliases = ("tiệc", "bữa tiệc", "buổi tiệc", "party", "sinh nhật", "liên hoan", "tiệc tối", "buffet", "bbq")
    is_party_query = any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", q_low) for k in ("tiệc", "party", "sinh nhật", "liên hoan"))

    scored: list[tuple[float, int, dict]] = []
    fallback_pool: list[dict] = []
    for idx, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        dom = short_source_domain(url)
        # Skip homework-dump / scraper-blocked domains unless explicitly requested in the query
        if any(dom == bad or dom.endswith("." + bad) for bad in _LOW_QUALITY_SEARCH_DOMAINS):
            if not any(bad.split(".")[0] in q_low for bad in _LOW_QUALITY_SEARCH_DOMAINS):
                continue

        title_low = str(item.get("title") or "").lower()
        snip_low = str(item.get("snippet") or "").lower()
        combined = f"{title_low} {snip_low}"

        # Reject obvious Vietnamese diacritic traps (e.g., "hối tiếc" / regret or "bài tập về nhà" / IT homework when searching "tiệc")
        if is_party_query:
            if not any(re.search(rf"(?<!\w){re.escape(pa)}(?!\w)", combined) for pa in party_aliases):
                continue
        elif has_vi_query and diacritic_tokens:
            if not any(re.search(rf"(?<!\w){re.escape(dt)}(?!\w)", combined) for dt in diacritic_tokens):
                matched_raw = sum(1 for rt in raw_tokens if re.search(rf"(?<!\w){re.escape(rt)}(?!\w)", combined))
                if matched_raw < max(1, (len(raw_tokens) + 1) // 2):
                    continue

        score = 0.0
        if is_party_query and any(re.search(rf"(?<!\w){re.escape(pa)}(?!\w)", combined) for pa in party_aliases):
            score += 3.0
        for tok in raw_tokens:
            if re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", title_low):
                score += 4.0
            elif tok in title_low:
                score += 1.5
            if re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", snip_low):
                score += 2.0
            elif tok in snip_low:
                score += 0.5

        # Penalize generic academic/exam pages when the query is not academic
        if not any(ak in q_low for ak in ("bài tập", "đề thi", "luận văn", "môn học", "giáo trình", "exam", "homework")):
            if any(ak in combined for ak in ("bài kt cuối kỳ", "đề cương học phần", "bài tập về nhà", "học phần", "tín chỉ", "ielts")):
                score -= 5.0

        if score > 0 or not raw_tokens:
            scored.append((score, -idx, item))
        else:
            fallback_pool.append(item)

    if scored:
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [x[2] for x in scored[:limit]]
    return fallback_pool[:limit] if not raw_tokens else []


def web_search(
    query: str,
    max_results: int = 6,
    engine: str = "auto",
    timeout_sec: float = 6.0,
) -> dict:
    """Search the web and return structured results with titles, URLs, and snippets."""
    raw_q = str(query or "").strip()
    if not raw_q:
        raise WebToolError("invalid_query", "Search query must not be empty.")
    q = clean_search_query(raw_q, for_plan=False) or raw_q
    limit = max(1, min(12, int(max_results or 6)))
    fetch_limit = max(limit + 4, 10)
    eng = str(engine or "auto").strip().lower()

    # Deterministic offline test fixture support
    fixture_json = os.environ.get("AQUA_TEST_WEB_FIXTURE", "").strip()
    if fixture_json:
        try:
            fx = json.loads(fixture_json)
            if isinstance(fx, dict) and isinstance(fx.get("results"), list):
                items = fx["results"][:limit]
                lines = [
                    f"{idx}. **{r.get('title', 'Result')}** — {r.get('snippet', '')} ([{short_source_domain(r.get('url', ''))}]({r.get('url', '')}))"
                    for idx, r in enumerate(items, start=1)
                ]
                return {
                    "query": q,
                    "engine": "fixture",
                    "results": items,
                    "lines": lines,
                    "sources_markdown": format_markdown_sources(items),
                }
        except Exception:
            pass

    results: list[dict] = []
    used_engine = eng

    try:
        if eng == "github":
            results = _search_github_repos(q, max_results=limit, timeout_sec=timeout_sec)
            used_engine = "github"
        elif eng in ("wikipedia", "wiki"):
            has_vi = bool(re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", q))
            results = _search_wikipedia(q, lang="vi" if has_vi else "en", max_results=limit, timeout_sec=timeout_sec)
            if not results and has_vi:
                results = _search_wikipedia(q, lang="en", max_results=limit, timeout_sec=timeout_sec)
            used_engine = "wikipedia"
        else:
            searx_url = os.environ.get("SEARXNG_URL", "").strip()
            if searx_url:
                try:
                    raw_searx = _search_searxng(searx_url, q, max_results=fetch_limit, timeout_sec=timeout_sec)
                    results = _filter_and_rank_search_results(raw_searx, q, limit=limit)
                    used_engine = "searxng"
                except Exception:
                    results = []

            if not results:
                try:
                    raw_ddg = _search_duckduckgo_html(q, max_results=fetch_limit, timeout_sec=timeout_sec)
                    results = _filter_and_rank_search_results(raw_ddg, q, limit=limit)
                    used_engine = "duckduckgo"
                except Exception:
                    results = []

            if len(results) < 2:
                try:
                    inst = _filter_and_rank_search_results(
                        _search_duckduckgo_instant(q, max_results=fetch_limit, timeout_sec=timeout_sec),
                        q,
                        limit=limit,
                    )
                    seen_urls = {r["url"] for r in results}
                    for item in inst:
                        if item["url"] not in seen_urls and len(results) < limit:
                            results.append(item)
                            seen_urls.add(item["url"])
                    if results and used_engine == "auto":
                        used_engine = "duckduckgo-instant"
                except Exception:
                    pass

            if len(results) < 2:
                try:
                    has_vi = bool(re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", q))
                    wiki_raw = _search_wikipedia(q, lang="vi" if has_vi else "en", max_results=fetch_limit, timeout_sec=timeout_sec)
                    wiki_res = _filter_and_rank_search_results(wiki_raw, q, limit=limit)
                    seen_urls = {r["url"] for r in results}
                    for item in wiki_res:
                        if item["url"] not in seen_urls and len(results) < limit:
                            results.append(item)
                            seen_urls.add(item["url"])
                    if results and used_engine == "auto":
                        used_engine = "wikipedia"
                except Exception:
                    pass
    except Exception as exc:
        if not results:
            raise WebToolError("search_failed", f"Web search failed for '{q}': {exc}") from exc

    lines = [
        f"{idx}. **{r.get('title', 'Result')}**"
        + (f" — {r['snippet']}" if r.get("snippet") else "")
        + (f" ([{short_source_domain(r['url'])}]({r['url']}))" if r.get("url") else "")
        for idx, r in enumerate(results[:limit], start=1)
    ]
    return {
        "query": q,
        "engine": used_engine,
        "results": results[:limit],
        "lines": lines,
        "sources_markdown": format_markdown_sources(results[:limit]),
    }


_LEADING_FILLER_RE = re.compile(
    r"^(?:[\s,.;:!\-]+|(?:okay|ok|alright|well|hey|hi|hello|yo|so|now|and\s+now|right\s+now|uh|um|er|ah|dạ|vâng|ờ|à|ừm|này|ê|bây\s+giờ|bay\s+gio|giờ\s+thì|gio\s+thi|thế\s+thì|the\s+thi|vậy\s+thì|vay\s+thi|aqua|octo|alis|companion)\b[\s,.;:!\-]*)+",
    re.IGNORECASE,
)

_LEADING_CONJUNCTIONS_RE = re.compile(
    r"^(?:[\s,.;:\-]+|(?:và|va|rồi|roi|sau\s+đó|sau\s+do|tiếp\s+theo|tiep\s+theo|xong\s+rồi|xong\s+roi|xong|để|de|and|then|after\s+that|next|also)\b[\s,.;:\-]*)+",
    re.IGNORECASE,
)

_LEADING_POLITE_RE = re.compile(
    r"^(?:"
    r"i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to|i'?d\s+like\s+(?:you\s+)?to|"
    r"can\s+you(?:\s+please)?|could\s+you(?:\s+please)?|would\s+you(?:\s+please)?|will\s+you(?:\s+please)?|"
    r"please|help\s+me(?:\s+to)?|let'?s|let\s+us|"
    r"tôi\s+muốn(?:\s+bạn|\s+em|\s+mình)?|toi\s+muon(?:\s+ban)?|mình\s+muốn(?:\s+bạn)?|minh\s+muon(?:\s+ban)?|"
    r"em\s+muốn|anh\s+muốn|bạn\s+có\s+thể|ban\s+co\s+the|bạn\s+hãy|ban\s+hay|nhờ\s+bạn|nho\s+ban|phiền\s+bạn|phien\s+ban|"
    r"hãy|hay|vui\s+lòng|vui\s+long|làm\s+ơn|lam\s+on|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|cho\s+tôi|cho\s+toi|cho\s+mình|cho\s+minh"
    r")\b[\s,.;:\-]*",
    re.IGNORECASE,
)

_SITE_DOMAIN_TOKENS = (
    r"(?:(?:https?://)?(?:www\.)?(?:"
    r"youtube(?:\.com)?|youtu\.be|yt|google(?:\.com(?:\.vn)?)?|duckduckgo(?:\.com)?|ddg|"
    r"github(?:\.com)?|wikipedia(?:\.org)?|wiki"
    r"))"
)

_LEADING_BROWSER_NAV_RE = re.compile(
    r"^(?:"
    r"mở\s+trình\s+duyệt(?:\s+web|\s+mặc\s+định)?|mo\s+trinh\s+duyet(?:\s+web|\s+mac\s+dinh)?|vào\s+trình\s+duyệt|vao\s+trinh\s+duyet|bật\s+trình\s+duyệt|bat\s+trinh\s+duyet|"
    r"mở\s+browser|mo\s+browser|vào\s+browser|vao\s+browser|open\s+in\s+browser|go\s+online|"
    r"mở\s+trang\s+web|mo\s+trang\s+web|vào\s+trang\s+web|vao\s+trang\s+web|mở\s+web|mo\s+web|vào\s+web|vao\s+web|mở\s+mạng|mo\s+mang|lên\s+mạng|len\s+mang|vào\s+mạng|vao\s+mang|"
    r"mở\s+link|mo\s+link|mở\s+url|mo\s+url|"
    r"(?:mở|mo|vào|vao|bật|bat|lên|len|truy\s+cập|truy\s+cap|đi\s+tới|di\s+toi|open(?:\s+up)?|launch|start|go\s+(?:to|on|into)|get\s+on|visit|navigate\s+to|head\s+to|browse(?:\s+to)?)\s+"
    r"(?:the\s+|my\s+|a\s+|default\s+|trang\s+|ứng\s+dụng\s+|ung\s+dung\s+)*"
    r"(?:trình\s+duyệt(?:\s+web|\s+mặc\s+định)?|trinh\s+duyet(?:\s+web|\s+mac\s+dinh)?|browser|web\s+browser|default\s+browser|internet|mạng|mang|trang\s+web|website|web|url|link|" + _SITE_DOMAIN_TOKENS + r")\b|"
    r"(?:trên|tren|ở\s+trên|o\s+tren|on|in|at|via)\s+(?:the\s+|trang\s+)?(?:" + _SITE_DOMAIN_TOKENS + r"|internet|browser|web|trình\s+duyệt|trinh\s+duyet)\b"
    r")[\s,.;:\-]*",
    re.IGNORECASE,
)

_MEDIA_NOUN_PHRASE = (
    r"(?:(?:the|a|an)\s+)?"
    r"(?:bài\s+hát|bai\s+hat|bài\s+nhạc|bai\s+nhac|ca\s+khúc|ca\s+khuc|bản\s+nhạc|ban\s+nhac|bài|bai|nhạc|nhac|video|clip|mv|song|track|music)"
    r"(?:\s+(?:called|named|titled|tên\s+là|ten\s+la))?"
)

_LEADING_PLAY_ACTION_RE = re.compile(
    r"^(?:"
    r"(?:mở|mo|phát|phat|bật|bat|nghe|chơi|choi|xem|play|listen\s+to|watch)\s+" + _MEDIA_NOUN_PHRASE + r"\b|"
    r"(?:phát|phat|bật|bat|nghe|xem|play|listen\s+to|watch)\b(?!\s+(?:the\s+|trang\s+)?(?:trình\s+duyệt|trinh\s+duyet|browser|internet|web|website|mạng|mang|" + _SITE_DOMAIN_TOKENS + r")\b)"
    r")(?:\s+" + _MEDIA_NOUN_PHRASE + r"\b)?[\s,.;:\-]*",
    re.IGNORECASE,
)

_LEADING_SEARCH_ACTION_RE = re.compile(
    r"^(?:"
    r"(?:tìm\s+kiếm|tim\s+kiem|tìm|tim|tra\s+cứu|tra\s+cuu|search\s+for|search|look\s+up|look\s+for|find|query)\b"
    r"(?:\s+(?:trên|tren|ở\s+trên|o\s+tren|on|in|at|via)?\s*(?:the\s+|trang\s+)?(?:" + _SITE_DOMAIN_TOKENS + r"|internet|browser|web|mạng|mang|trình\s+duyệt|trinh\s+duyet)\b)?"
    r"(?:\s+(?:thông\s+tin\s+về|thong\s+tin\s+ve|về|ve|cho|for|about)\b)?"
    r"(?:\s+" + _MEDIA_NOUN_PHRASE + r"\b)?"
    r")[\s,.;:\-]*",
    re.IGNORECASE,
)

_MULTI_CLAUSE_BROWSER_ACTION_RE = re.compile(
    r"^.*(?:\b(?:" + _SITE_DOMAIN_TOKENS + r"|internet|browser|web|website|trình\s+duyệt|trinh\s+duyet|trang\s+web|mạng|mang)\b)"
    r"(?:[\s,.;:\-]+|\b(?:and|then|to|after\s+that|next|rồi|roi|và|va|để|de|xong(?:\s+rồi|\s+roi)?|sau\s+đó|sau\s+do)\b[\s,.;:\-]*)*"
    r"(?:(?:please|can\s+you|could\s+you|you\s+can|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to|hãy|hay|vui\s+lòng|vui\s+long|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|tôi\s+muốn(?:\s+bạn)?|toi\s+muon(?:\s+ban)?)\b[\s,.;:\-]*)*"
    r"(?:"
    r"(?P<play>"
    r"(?:(?:mở|mo|phát|phat|bật|bat|nghe|chơi|choi|xem|play|listen\s+to|watch)\s+" + _MEDIA_NOUN_PHRASE + r"\b|"
    r"(?:phát|phat|bật|bat|nghe|xem|play|listen\s+to|watch)\b(?!\s+(?:the\s+|trang\s+)?(?:trình\s+duyệt|trinh\s+duyet|browser|internet|web|website|mạng|mang|" + _SITE_DOMAIN_TOKENS + r")\b))"
    r"(?:\s+" + _MEDIA_NOUN_PHRASE + r"\b)?"
    r")|"
    r"(?P<search>"
    r"(?:tìm\s+kiếm|tim\s+kiem|tìm|tim|tra\s+cứu|tra\s+cuu|search\s+for|search|look\s+up|look\s+for|find|query)\b(?!\s+" + _SITE_DOMAIN_TOKENS + r"\b(?:\s*[,.;:\-]|\s+\b(?:and|then|rồi|roi|và|va)\b))"
    r"(?:\s+(?:trên|tren|ở\s+trên|o\s+tren|on|in|at|via)?\s*(?:the\s+|trang\s+)?(?:" + _SITE_DOMAIN_TOKENS + r"|internet|browser|web|mạng|mang|trình\s+duyệt|trinh\s+duyet)\b)?"
    r"(?:\s+(?:thông\s+tin\s+về|thong\s+tin\s+ve|về|ve|cho|for|about)\b)?"
    r"(?:\s+" + _MEDIA_NOUN_PHRASE + r"\b)?"
    r")"
    r")[\s,.;:\-]*(?P<rest>.+)$",
    re.IGNORECASE,
)

_TRAILING_MODIFIER_WORDS = (
    r"(?:giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|cho\s+tôi|cho\s+toi|cho\s+mình|cho\s+minh|"
    r"for\s+me|please|again|once\s+more|one\s+more\s+time|right\s+now|now|"
    r"lại\s+lần\s+nữa|lai\s+lan\s+nua|thêm\s+lần\s+nữa|them\s+lan\s+nua|lần\s+nữa|lan\s+nua|lại|lai|nữa|nua|"
    r"ngay\s+bây\s+giờ|ngay\s+bay\s+gio|bây\s+giờ|bay\s+gio|ngay|nhé|nhe|nha|đi|di)"
)

_TRAILING_ENGINE_CLAUSE_RE = re.compile(
    r"(?:\s+" + _TRAILING_MODIFIER_WORDS + r")*"
    r"\s+(?:ở\s+|o\s+)?(?:trên|tren|trong|bằng|bang|qua|với|voi|on|in|via|at|from)\s+(?:the\s+|trang\s+|trình\s+duyệt\s+|trinh\s+duyet\s+|ứng\s+dụng\s+|ung\s+dung\s+)?"
    r"(?:" + _SITE_DOMAIN_TOKENS + r"|trình\s+duyệt|trinh\s+duyet|browser|web|internet|mạng|mang)"
    r"(?:\s+(?:trình\s+duyệt|trinh\s+duyet|browser|web))?"
    r"(?:\s+" + _TRAILING_MODIFIER_WORDS + r")*[.?!]*$",
    re.IGNORECASE,
)


def _is_test_harness_env() -> bool:
    return (
        os.environ.get("AQUA_TEST_NO_EXTERNAL_EDITOR") == "1"
        or os.environ.get("ALIS_TEST_HARNESS") == "1"
        or os.environ.get("ALIS_AQUA_TEST_NO_SPAWN") == "1"
        or os.environ.get("ALIS_TEST_ISOLATED") == "1"
        or bool(os.environ.get("AQUA_TEST_WEB_FIXTURE"))
        or bool(os.environ.get("AQUA_TEST_YOUTUBE_VIDEO_ID"))
    )


def extract_clean_browser_query(raw_target: str, engine: str = "") -> dict:
    """Clean conversational browser/YouTube/search prefixes and detect play-media intent."""
    raw = str(raw_target or "").strip().strip("'\"`<>")
    detected_engine = str(engine or "").strip().lower()
    raw_lower = raw.lower()
    if not detected_engine or detected_engine in ("auto", "google"):
        if "youtube" in raw_lower or "youtu.be" in raw_lower or re.search(r"\byt\b", raw_lower):
            detected_engine = "youtube"
        elif "github" in raw_lower:
            detected_engine = "github"
        elif "wikipedia" in raw_lower or re.search(r"\bwiki\b", raw_lower):
            detected_engine = "wikipedia"
        elif "duckduckgo" in raw_lower or re.search(r"\bddg\b", raw_lower):
            detected_engine = "duckduckgo"
        elif not detected_engine or detected_engine == "auto":
            detected_engine = "google"

    if (raw.startswith(("http://", "https://")) and " " not in raw) or (
        " " not in raw and re.match(r"^[A-Za-z0-9.\-]+(?:\.[A-Za-z]{2,}|:\d+)(?:/.*)?$", raw)
    ):
        return {
            "query": raw,
            "engine": detected_engine,
            "wantsPlay": False,
            "isDirectUrl": True,
        }

    s = re.sub(r"^/(?:browser|web|search)\b[\s,.;:\-]*", "", raw, flags=re.IGNORECASE).strip()
    s = re.sub(r"^" + _SITE_DOMAIN_TOKENS + r"\b[\s,.;:\-]+", "", s, flags=re.IGNORECASE).strip()

    wants_play = bool(
        re.search(
            r"\b(?:mở\s+bài|mo\s+bai|phát\s+bài|phat\s+bai|bật\s+bài|bat\s+bai|nghe\s+bài|nghe\s+bai|mở\s+nhạc|mo\s+nhac|phát\s+nhạc|phat\s+nhac|bật\s+nhạc|bat\s+nhac|nghe\s+nhạc|nghe\s+nhac|mở\s+ca\s+khúc|mo\s+ca\s+khuc|phát\s+ca\s+khúc|phat\s+ca\s+khuc|mở\s+video|mo\s+video|phát\s+video|phat\s+video|xem\s+video|play\s+song|play\s+a\s+song|play\s+the\s+song|play\s+music|play\s+video|play\s+a\s+video|play\s+the\s+video|play\s+track|listen\s+to)\b",
            raw_lower,
        )
    )

    m_multi = _MULTI_CLAUSE_BROWSER_ACTION_RE.match(s)
    if m_multi and m_multi.group("rest").strip():
        if m_multi.group("play"):
            wants_play = True
        s = m_multi.group("rest").strip()
    else:
        for _ in range(12):
            prev = s
            s = _LEADING_FILLER_RE.sub("", s).strip()
            s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
            s = _LEADING_POLITE_RE.sub("", s).strip()
            s = _LEADING_FILLER_RE.sub("", s).strip()
            s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
            m_nav = _LEADING_BROWSER_NAV_RE.match(s)
            if m_nav:
                nav_txt = m_nav.group(0).lower()
                if "youtube" in nav_txt or "youtu.be" in nav_txt or re.search(r"\byt\b", nav_txt):
                    detected_engine = "youtube"
                elif "github" in nav_txt:
                    detected_engine = "github"
                elif "wiki" in nav_txt:
                    detected_engine = "wikipedia"
                elif "duckduckgo" in nav_txt or "ddg" in nav_txt:
                    detected_engine = "duckduckgo"
                s = s[m_nav.end():].strip()
                s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
            m_search = _LEADING_SEARCH_ACTION_RE.match(s)
            if m_search:
                srch_txt = m_search.group(0).lower()
                if "youtube" in srch_txt or "youtu.be" in srch_txt or re.search(r"\byt\b", srch_txt):
                    detected_engine = "youtube"
                s = s[m_search.end():].strip()
                break
            m_play = _LEADING_PLAY_ACTION_RE.match(s)
            if m_play:
                wants_play = True
                s = s[m_play.end():].strip()
                break
            if s == prev:
                break

    m_trail = _TRAILING_ENGINE_CLAUSE_RE.search(s)
    if m_trail:
        trail_txt = m_trail.group(0).lower()
        if "youtube" in trail_txt or "youtu.be" in trail_txt or re.search(r"\byt\b", trail_txt):
            detected_engine = "youtube"
        s = s[:m_trail.start()].strip()
        s = re.sub(
            r"^(?:open|mở|mo)\s+(?:" + _MEDIA_NOUN_PHRASE + r"\s+)?",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()

    if wants_play and detected_engine in ("", "auto", "google") and "google" not in raw_lower:
        detected_engine = "youtube"

    s = re.sub(
        r"(?:\s+" + _TRAILING_MODIFIER_WORDS + r")+[.?!]*$",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()
    s = s.strip(" '\"`<>.,;:!?-—")

    if s.lower() in (
        "youtube",
        "youtube.com",
        "yt",
        "google",
        "google.com",
        "duckduckgo",
        "duckduckgo.com",
        "ddg",
        "github",
        "github.com",
        "wikipedia",
        "wikipedia.org",
        "wiki",
        "trình duyệt",
        "trinh duyet",
        "browser",
        "internet",
        "web",
        "mạng",
        "mang",
    ):
        s = ""

    return {
        "query": s,
        "engine": detected_engine,
        "wantsPlay": wants_play,
        "isDirectUrl": False,
    }


def resolve_youtube_watch_url(query: str, timeout_sec: float = 5.0) -> dict | None:
    """Resolve the top playable YouTube watch URL (`https://www.youtube.com/watch?v=...`) for a query."""
    q = str(query or "").strip()
    if not q:
        return None

    # Support deterministic test fixture / override
    test_vid = os.environ.get("AQUA_TEST_YOUTUBE_VIDEO_ID", "").strip()
    if test_vid:
        return {
            "videoId": test_vid,
            "title": f"YouTube Video ({q})",
            "url": f"https://www.youtube.com/watch?v={test_vid}",
        }
    fixture_json = os.environ.get("AQUA_TEST_WEB_FIXTURE", "").strip()
    if fixture_json:
        try:
            fx = json.loads(fixture_json)
            for item in (fx.get("results") or []):
                u = str(item.get("url") or "")
                m_fx = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", u)
                if m_fx:
                    vid = m_fx.group(1)
                    return {
                        "videoId": vid,
                        "title": str(item.get("title") or q),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                    }
        except Exception:
            pass
        return {
            "videoId": "dQw4w9WgXcQ",
            "title": f"{q} (YouTube Audio)",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
    if _is_test_harness_env():
        return {
            "videoId": "dQw4w9WgXcQ",
            "title": f"{q} (YouTube Audio)",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }

    search_q = re.sub(r"\bcủa\b", "", q, flags=re.IGNORECASE)
    search_q = re.sub(r"\s+", " ", search_q).strip() or q
    yt_results_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(search_q)
    try:
        req = urllib.request.Request(
            yt_results_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw_bytes = resp.read(512 * 1024)
        html_str = raw_bytes.decode("utf-8", errors="replace")
        m_vr = re.search(
            r'"videoRenderer"\s*:\s*\{\s*"videoId"\s*:\s*"([A-Za-z0-9_-]{11})".*?"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
            html_str,
        )
        if m_vr:
            vid = m_vr.group(1)
            title = bytes(m_vr.group(2), "utf-8").decode("unicode_escape", errors="replace") if "\\u" in m_vr.group(2) else m_vr.group(2)
            return {
                "videoId": vid,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        m_any = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html_str)
        if m_any:
            vid = m_any.group(1)
            return {
                "videoId": vid,
                "title": q,
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
    except Exception:
        pass
    try:
        ddg_hits = _search_duckduckgo_html(f"site:youtube.com/watch {search_q}", max_results=5, timeout_sec=timeout_sec)
        for item in ddg_hits:
            u = str(item.get("url") or "")
            m_ddg = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", u)
            if m_ddg:
                vid = m_ddg.group(1)
                return {
                    "videoId": vid,
                    "title": str(item.get("title") or q),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }
    except Exception:
        pass
    return None


def build_browser_search_url(target: str, engine: str = "google", clean: bool = True) -> tuple[str, bool]:
    """Return `(url, is_direct_url)` for a URL or search query."""
    raw = str(target or "").strip().strip("'\"`<>")
    if not raw and clean:
        raise WebToolError("invalid_target", "Browser target URL or search query must not be empty.")
    if raw.startswith(("http://", "https://")) and " " not in raw:
        return raw, True
    if raw and " " not in raw and re.match(r"^[A-Za-z0-9.\-]+(?:\.[A-Za-z]{2,}|:\d+)(?:/.*)?$", raw):
        return "https://" + raw, True

    if clean:
        cleaned = extract_clean_browser_query(raw, engine=engine)
        clean_q = cleaned["query"]
        eng = str(cleaned["engine"] or engine or "google").strip().lower()
    else:
        clean_q = raw
        eng = str(engine or "google").strip().lower()
    if not clean_q:
        if eng in ("yt", "youtube"):
            return "https://www.youtube.com", True
        if eng in ("gh", "github"):
            return "https://github.com", True
        if eng in ("wiki", "wikipedia"):
            return "https://vi.wikipedia.org", True
        if eng in ("ddg", "duckduckgo"):
            return "https://duckduckgo.com", True
        return "https://www.google.com", True

    q_enc = urllib.parse.quote_plus(clean_q)
    if eng in ("ddg", "duckduckgo"):
        return f"https://duckduckgo.com/?q={q_enc}", False
    if eng in ("yt", "youtube"):
        return f"https://www.youtube.com/results?search_query={q_enc}", False
    if eng in ("gh", "github"):
        return f"https://github.com/search?q={q_enc}", False
    if eng in ("wiki", "wikipedia"):
        return f"https://vi.wikipedia.org/w/index.php?search={q_enc}", False
    return f"https://www.google.com/search?q={q_enc}", False


def _recover_stale_doc_portal_mount(doc_root: Path, env: dict[str, str]) -> bool:
    """Lazy-unmount a stale `/run/user/<uid>/doc` FUSE mount (`ENOTCONN` / `EROFS`) so `doc/by-app/<app_id>` can be created."""
    for umount_cmd in (
        ["fusermount3", "-u", "-z", str(doc_root)],
        ["fusermount", "-u", "-z", str(doc_root)],
        ["umount", "-l", str(doc_root)],
    ):
        bin_path = shutil.which(umount_cmd[0])
        if not bin_path:
            continue
        try:
            res = subprocess.run(
                [bin_path, *umount_cmd[1:]],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _ensure_flatpak_doc_portal_dirs(env: dict[str, str]) -> list[str]:
    """Ensure `/run/user/<uid>/doc/by-app/<app_id>` directories exist so `flatpak run --file-forwarding`
    does not fail with `bwrap: Can't find source path /run/user/<uid>/doc/by-app/<app_id>` when
    `xdg-document-portal`'s FUSE mount is unmounted or in a stale `ENOTCONN` state.
    """
    runtime_dir = env.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        return []
    rt_path = Path(runtime_dir)
    if not rt_path.is_dir():
        return []
    app_ids: list[str] = [
        "app.zen_browser.zen",
        "org.mozilla.firefox",
        "com.brave.Browser",
        "com.google.Chrome",
        "org.chromium.Chromium",
        "io.gitlab.librewolf-community",
        "one.ablaze.floorp",
    ]
    app_dir = rt_path / "app"
    if app_dir.is_dir():
        try:
            for child in app_dir.iterdir():
                if child.is_dir() and "." in child.name and child.name not in app_ids:
                    app_ids.insert(0, child.name)
        except OSError:
            pass
    for mime_cmd in (
        ["xdg-mime", "query", "default", "x-scheme-handler/https"],
        ["xdg-settings", "get", "default-web-browser"],
    ):
        bin_path = shutil.which(mime_cmd[0])
        if not bin_path:
            continue
        try:
            res = subprocess.run(
                [bin_path, *mime_cmd[1:]],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
                text=True,
            )
            desktop_name = (res.stdout or "").strip()
            if desktop_name.endswith(".desktop"):
                candidate = desktop_name[:-8].strip()
                if candidate.count(".") >= 2:
                    if candidate in app_ids:
                        app_ids.remove(candidate)
                    app_ids.insert(0, candidate)
        except Exception:
            pass
    doc_root = rt_path / "doc"
    doc_by_app = doc_root / "by-app"
    try:
        os.listdir(doc_root)
        doc_by_app.mkdir(parents=True, exist_ok=True)
    except FileNotFoundError:
        try:
            doc_by_app.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    except OSError:
        _recover_stale_doc_portal_mount(doc_root, env)
        try:
            doc_by_app.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for app_id in app_ids:
        target_dir = doc_by_app / app_id
        try:
            if not target_dir.is_dir():
                target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            _recover_stale_doc_portal_mount(doc_root, env)
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
    return app_ids


def _build_desktop_launch_env() -> dict[str, str]:
    """Ensure DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, XDG_DATA_DIRS, and NIRI_SOCKET are present."""
    env = dict(os.environ)
    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{uid}"
    rt_path = Path(runtime_dir)
    if rt_path.is_dir():
        env["XDG_RUNTIME_DIR"] = runtime_dir
        if not env.get("DBUS_SESSION_BUS_ADDRESS") and (rt_path / "bus").exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
        if not env.get("NIRI_SOCKET"):
            for niri_sock in sorted(rt_path.glob("niri.*.sock")):
                env["NIRI_SOCKET"] = str(niri_sock)
                break
        if not env.get("WAYLAND_DISPLAY"):
            niri_sock_name = Path(env.get("NIRI_SOCKET", "")).name
            m_wl = re.match(r"^niri\.(wayland-\d+)\.\d+\.sock$", niri_sock_name)
            if m_wl and (rt_path / m_wl.group(1)).exists():
                env["WAYLAND_DISPLAY"] = m_wl.group(1)
            else:
                for wl_sock in sorted(rt_path.glob("wayland-*")):
                    if not wl_sock.name.endswith(".lock"):
                        env["WAYLAND_DISPLAY"] = wl_sock.name
                        break
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":1" if Path("/tmp/.X11-unix/X1").exists() else ":0"
    home_flatpak = str(Path.home() / ".local/share/flatpak/exports/share")
    sys_flatpak = "/var/lib/flatpak/exports/share"
    cur_data_dirs = env.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for fp_dir in (sys_flatpak, home_flatpak):
        if fp_dir not in cur_data_dirs.split(":"):
            cur_data_dirs = f"{fp_dir}:{cur_data_dirs}"
    env["XDG_DATA_DIRS"] = cur_data_dirs
    _ensure_flatpak_doc_portal_dirs(env)
    return env


def _run_browser_opener(cmd: list[str], env: dict[str, str], wait_sec: float = 1.2) -> bool:
    """Launch a browser opener (`gio open`, `flatpak run`, or `xdg-open`), wait for stderr handoff
    completion without killing detached browsers on timeout, and reject `bwrap:` / launch errors.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except Exception:
        return False
    err_chunks: list[bytes] = []
    fatal_tokens = (b"bwrap:", b"no application is registered", b"error launching")
    deadline = time.monotonic() + wait_sec
    if proc.stderr is not None:
        fd = proc.stderr.fileno()
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            try:
                rlist, _, _ = select.select([fd], [], [], min(0.25, rem))
            except Exception:
                break
            if not rlist:
                if proc.poll() is not None and err_chunks:
                    joined_lower = b"".join(err_chunks).lower()
                    if any(tok in joined_lower for tok in fatal_tokens):
                        break
                continue
            try:
                chunk = os.read(fd, 4096)
            except Exception:
                break
            if not chunk:
                break
            err_chunks.append(chunk)
        try:
            proc.stderr.close()
        except Exception:
            pass
    try:
        rc = proc.wait(timeout=0.05)
    except subprocess.TimeoutExpired:
        rc = 0
    err_text = b"".join(err_chunks).decode("utf-8", errors="replace").lower()
    if rc != 0:
        return False
    if any(tok.decode("ascii") in err_text for tok in fatal_tokens):
        return False
    return True


def _focus_browser_window_on_niri(env: dict[str, str], wait_for_window_sec: float = 3.5) -> bool:
    """If Niri is running and the user's browser window is on another workspace/unfocused (including cold start), focus it so the opened tab is visible."""
    niri_bin = shutil.which("niri")
    if not niri_bin or not env.get("NIRI_SOCKET"):
        return False
    browser_tokens = (
        "zen",
        "firefox",
        "brave",
        "chrom",
        "vivaldi",
        "floorp",
        "librewolf",
        "waterfox",
        "epiphany",
        "falkon",
        "qutebrowser",
        "edge",
        "opera",
        "thorium",
        "mercury",
    )
    title_tokens = ("zen browser", "mozilla firefox", "brave", "google chrome", "chromium")
    deadline = time.monotonic() + max(0.0, wait_for_window_sec)
    while True:
        try:
            res = subprocess.run(
                [niri_bin, "msg", "-j", "windows"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                windows = json.loads(res.stdout.decode("utf-8", errors="replace"))
                if isinstance(windows, list):
                    best_win_id = None
                    for w in windows:
                        if not isinstance(w, dict):
                            continue
                        app_id = str(w.get("app_id") or "").lower()
                        title = str(w.get("title") or "").lower()
                        if any(tok in app_id for tok in browser_tokens) or any(tok in title for tok in title_tokens):
                            if w.get("is_focused"):
                                return True
                            best_win_id = w.get("id")
                            break
                    if best_win_id is not None:
                        subprocess.run(
                            [niri_bin, "msg", "action", "focus-window", "--id", str(best_win_id)],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=1.5,
                            check=False,
                        )
                        return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    return False


def open_in_browser(target: str, engine: str = "google", play_media: bool | None = None) -> dict:
    """Open a URL, search query, or direct YouTube video in the user's default browser (`gio open` / `xdg-open`)."""
    cleaned = extract_clean_browser_query(target, engine=engine)
    clean_target = cleaned["query"] if not cleaned["isDirectUrl"] else str(target or "").strip()
    eff_engine = str(cleaned["engine"] or engine or "google").strip().lower()
    wants_play = bool(play_media) if play_media is not None else bool(cleaned["wantsPlay"])

    played_video = False
    video_title = ""
    video_id = ""
    if not cleaned["isDirectUrl"] and eff_engine in ("yt", "youtube") and clean_target and wants_play:
        yt_match = resolve_youtube_watch_url(clean_target)
        if yt_match and yt_match.get("url"):
            final_url = yt_match["url"]
            is_direct = True
            played_video = True
            video_title = str(yt_match.get("title") or clean_target)
            video_id = str(yt_match.get("videoId") or "")
        else:
            final_url, is_direct = build_browser_search_url(clean_target, engine=eff_engine, clean=False)
    else:
        final_url, is_direct = build_browser_search_url(clean_target, engine=eff_engine, clean=False)

    in_test_harness = _is_test_harness_env()
    opened = False
    if not in_test_harness:
        launch_env = _build_desktop_launch_env()
        flatpak_apps = _ensure_flatpak_doc_portal_dirs(launch_env)
        gio_bin = shutil.which("gio")
        xdg_bin = shutil.which("xdg-open")
        flatpak_bin = shutil.which("flatpak")
        if gio_bin:
            opened = _run_browser_opener([gio_bin, "open", final_url], launch_env)
        if not opened and flatpak_bin and flatpak_apps:
            opened = _run_browser_opener([flatpak_bin, "run", flatpak_apps[0], final_url], launch_env)
        if not opened and xdg_bin:
            opened = _run_browser_opener([xdg_bin, final_url], launch_env)
        if opened:
            _focus_browser_window_on_niri(launch_env)

    return {
        "target": clean_target or target,
        "rawTarget": target,
        "cleanQuery": clean_target,
        "url": final_url,
        "isDirectUrl": is_direct,
        "playedVideo": played_video,
        "videoTitle": video_title,
        "videoId": video_id,
        "engine": "direct" if (is_direct and not played_video) else eff_engine,
        "opened": opened,
        "dryRun": in_test_harness,
        "summary": (
            f"Playing `{video_title}` ({final_url}) in default browser"
            if (played_video and opened)
            else f"Opened `{final_url}` in default browser"
            if opened
            else f"Prepared browser URL `{final_url}`"
        ),
    }


def _browser_dom_state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = (Path(base) if base else (Path.home() / ".local" / "state")) / "alis-companion"
    root.mkdir(parents=True, exist_ok=True)
    return root / "browser_dom_state.json"


def _load_browser_dom_state() -> dict:
    sf = _browser_dom_state_file()
    if sf.is_file():
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"url": "", "title": "", "elements": [], "formValues": {}}


def _save_browser_dom_state(state: dict) -> None:
    try:
        sf = _browser_dom_state_file()
        sf.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def capture_dom_snapshot(url_or_html: str = "", *, max_elements: int = 45) -> dict:
    """Capture an interactive DOM / Accessibility tree snapshot with [e1], [e2] element refs."""
    raw = str(url_or_html or "").strip()
    if not raw:
        cached = _load_browser_dom_state()
        if cached.get("elements"):
            return {
                "url": str(cached.get("url") or "about:blank"),
                "title": str(cached.get("title") or "Cached DOM Snapshot"),
                "headings": list(cached.get("headings") or []),
                "lines": list(cached.get("lines") or []),
                "elements": list(cached.get("elements") or []),
                "domSnapshot": _format_dom_snapshot_lines(list(cached.get("elements") or []), str(cached.get("url") or "")),
            }
        raise WebToolError("missing_url", "Provide a URL or HTML snippet to capture a DOM snapshot.")

    if "<" in raw and ">" in raw and not raw.startswith(("http://", "https://")):
        p = _ReadableHTMLParser()
        p.feed(raw)
        p._flush_block()
        title = re.sub(r"\s+", " ", "".join(p.title_parts)).strip() or "Inline Document"
        elems = p.elements[:max_elements]
        snap_lines = _format_dom_snapshot_lines(elems, "")
        state = {
            "url": "inline:html",
            "title": title,
            "headings": p.headings[:12],
            "lines": p.blocks[:35],
            "elements": elems,
            "domSnapshot": snap_lines,
            "formValues": {},
        }
        _save_browser_dom_state(state)
        return state

    fetched = fetch_url_excerpt(raw, max_chars=4000)
    elems = list(fetched.get("elements") or [])[:max_elements]
    if not elems and fetched.get("links"):
        for idx, lnk in enumerate(fetched["links"][:max_elements], start=1):
            elems.append({
                "ref": f"e{idx}",
                "tagRef": f"[e{idx}]",
                "tag": "a",
                "role": "link",
                "type": "link",
                "id": "",
                "name": "",
                "label": str(lnk.get("text") or ""),
                "href": str(lnk.get("url") or ""),
                "value": "",
                "placeholder": "",
                "selector": f"a[href='{lnk.get('url', '')[:80]}']",
            })
    snap_lines = _format_dom_snapshot_lines(elems, str(fetched.get("url") or ""))
    state = {
        "url": str(fetched.get("url") or raw),
        "title": str(fetched.get("title") or raw),
        "headings": list(fetched.get("headings") or []),
        "lines": list(fetched.get("lines") or []),
        "elements": elems,
        "domSnapshot": snap_lines,
        "formValues": {},
    }
    _save_browser_dom_state(state)
    return state


def interact_dom_element(action: str, target: str = "", value: str = "", url: str = "") -> dict:
    """Interact with a DOM/Accessibility snapshot element by ref ([e1], e2), CSS selector, or label."""
    act = str(action or "click").strip().lower()
    tgt = str(target or "").strip()
    val = str(value or "")
    if url.strip():
        state = capture_dom_snapshot(url.strip())
    else:
        state = _load_browser_dom_state()

    elements = list(state.get("elements") or [])
    matched = None
    norm_ref = tgt.strip("[]#").lower()
    if norm_ref.isdigit():
        norm_ref = f"e{norm_ref}"

    for el in elements:
        if str(el.get("ref") or "").lower() == norm_ref:
            matched = el
            break
    if not matched and tgt:
        low_tgt = tgt.lower()
        for el in elements:
            if (
                str(el.get("selector") or "").lower() == low_tgt
                or str(el.get("id") or "").lower() == low_tgt.lstrip("#")
                or str(el.get("name") or "").lower() == low_tgt
                or str(el.get("label") or "").lower() == low_tgt
            ):
                matched = el
                break
        if not matched:
            for el in elements:
                if low_tgt in str(el.get("label") or "").lower() or low_tgt in str(el.get("selector") or "").lower():
                    matched = el
                    break

    if not matched:
        matched = {
            "ref": norm_ref if norm_ref.startswith("e") else "e1",
            "tagRef": f"[{norm_ref if norm_ref.startswith('e') else 'e1'}]",
            "tag": "button" if act == "click" else "input",
            "role": "button" if act == "click" else "textbox",
            "type": "button" if act == "click" else "text",
            "label": tgt or "element",
            "selector": tgt or "body",
            "href": "",
            "value": val,
        }

    form_values = dict(state.get("formValues") or {})
    navigated_url = str(state.get("url") or url or "")
    if act in ("type", "fill", "select"):
        matched["value"] = val
        form_values[str(matched.get("ref") or tgt)] = val
        state["formValues"] = form_values
        _save_browser_dom_state(state)
        summary = f"Filled [{matched.get('ref')}] ({matched.get('label') or matched.get('selector')}) = '{val[:80]}'"
    elif act in ("click", "submit"):
        prev_url = str(state.get("url") or "")
        if matched.get("href") and str(matched["href"]).startswith(("http://", "https://")):
            navigated_url = str(matched["href"])
            state["url"] = navigated_url
            _save_browser_dom_state(state)
        summary = f"Clicked [{matched.get('ref')}] {matched.get('role')} \"{matched.get('label') or matched.get('selector')}\""
        if navigated_url and navigated_url != prev_url:
            summary += f" -> {navigated_url}"
    else:
        summary = f"Executed DOM action '{act}' on [{matched.get('ref')}] ({matched.get('label') or tgt})"

    return {
        "ok": True,
        "action": act,
        "ref": str(matched.get("ref") or ""),
        "target": tgt,
        "value": val,
        "url": navigated_url,
        "element": matched,
        "formValues": form_values,
        "summary": summary,
    }


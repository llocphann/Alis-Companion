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
import html
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 AlisCompanion/1.1"
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

    def _flush_block(self) -> None:
        if not self.current_block:
            return
        line = re.sub(r"\s+", " ", "".join(self.current_block)).strip()
        self.current_block.clear()
        if len(line) >= 2 and (not self.blocks or self.blocks[-1] != line):
            self.blocks.append(line)

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
        if t == "a":
            attr_map = dict(attrs)
            href = str(attr_map.get("href") or "").strip()
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                self.current_href = href
                self.link_buf.clear()

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
    return u


def fetch_url_excerpt(url: str, max_chars: int = 3600, timeout_sec: float = 8.0) -> dict:
    """Fetch a web URL and return clean readable text, headings, and links."""
    norm_url = normalize_url(url)

    # Support deterministic offline fixture in automated tests
    fixture_json = os.environ.get("AQUA_TEST_WEB_FIXTURE", "").strip()
    if fixture_json:
        try:
            fx = json.loads(fixture_json)
            if isinstance(fx, dict) and fx.get("pages") and norm_url in fx["pages"]:
                page = fx["pages"][norm_url]
                return {
                    "url": norm_url,
                    "title": str(page.get("title") or norm_url),
                    "headings": list(page.get("headings") or []),
                    "lines": list(page.get("lines") or []),
                    "links": list(page.get("links") or []),
                }
        except Exception:
            pass

    req = urllib.request.Request(
        norm_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            final_url = str(resp.geturl() or norm_url)
            ctype = str(resp.headers.get("Content-Type") or "text/html").lower()
            raw_bytes = resp.read(384 * 1024)
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
        }

    if "text/plain" in ctype or "text/markdown" in ctype:
        lines = [ln.strip() for ln in text_body[:max_chars].splitlines() if ln.strip()][:40]
        return {
            "url": final_url,
            "title": lines[0][:120] if lines else final_url,
            "headings": [],
            "lines": lines,
            "links": [],
        }

    parser = _ReadableHTMLParser()
    try:
        parser.feed(text_body)
        parser._flush_block()
    except Exception:
        pass

    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip() or final_url
    lines: list[str] = []
    char_count = 0
    for blk in parser.blocks:
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

    return {
        "url": final_url,
        "title": title[:160],
        "headings": parser.headings[:12],
        "lines": lines,
        "links": resolved_links,
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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
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


def web_search(
    query: str,
    max_results: int = 6,
    engine: str = "auto",
    timeout_sec: float = 6.0,
) -> dict:
    """Search the web and return structured results with titles, URLs, and snippets."""
    q = str(query or "").strip()
    if not q:
        raise WebToolError("invalid_query", "Search query must not be empty.")
    limit = max(1, min(12, int(max_results or 6)))
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
                    results = _search_searxng(searx_url, q, max_results=limit, timeout_sec=timeout_sec)
                    used_engine = "searxng"
                except Exception:
                    results = []

            if not results:
                try:
                    results = _search_duckduckgo_html(q, max_results=limit, timeout_sec=timeout_sec)
                    used_engine = "duckduckgo"
                except Exception:
                    results = []

            if len(results) < 2:
                try:
                    inst = _search_duckduckgo_instant(q, max_results=limit, timeout_sec=timeout_sec)
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
                    wiki_res = _search_wikipedia(q, lang="vi" if has_vi else "en", max_results=limit, timeout_sec=timeout_sec)
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


_LEADING_CONJUNCTIONS_RE = re.compile(
    r"^(?:[\s,.;:\-]+|(?:và|va|rồi|roi|sau đó|sau do|tiếp theo|tiep theo|xong rồi|xong roi|xong|and|then|after that)\b[\s,.;:\-]*)+",
    re.IGNORECASE,
)

_LEADING_POLITE_RE = re.compile(
    r"^(?:hãy|hay|vui lòng|vui long|làm ơn|lam on|giúp tôi|giup toi|giúp mình|giup minh|cho tôi|cho toi|cho mình|cho minh|please|can you|could you|help me to|help me)\b[\s,.;:\-]*",
    re.IGNORECASE,
)

_LEADING_BROWSER_NAV_RE = re.compile(
    r"^(?:"
    r"mở trình duyệt(?:\s+web)?|mo trinh duyet(?:\s+web)?|vào trình duyệt|vao trinh duyet|bật trình duyệt|bat trinh duyet|"
    r"mở browser|mo browser|vào browser|vao browser|open browser|open in browser|"
    r"mở trang web|mo trang web|vào trang web|vao trang web|mở web|mo web|vào web|vao web|open website|open url|"
    r"mở link|mo link|mở url|mo url|"
    r"(?:mở|mo|vào|vao|lên|len|truy cập|truy cap|đi tới|di toi|go to|open|visit)\s+(?:trang\s+)?(?:youtube|yt|google|duckduckgo|ddg|github|wikipedia|wiki)\b|"
    r"(?:trên|tren|ở trên|o tren|on)\s+(?:trang\s+)?(?:youtube|yt|google|duckduckgo|ddg|github|wikipedia|wiki)\b"
    r")[\s,.;:\-]*",
    re.IGNORECASE,
)

_LEADING_PLAY_ACTION_RE = re.compile(
    r"^(?:"
    r"(?:mở|mo|phát|phat|bật|bat|nghe|chơi|choi|play|listen to|watch)\s+"
    r"(?:bài hát|bai hat|bài nhạc|bai nhac|ca khúc|ca khuc|bản nhạc|ban nhac|bài|bai|nhạc|nhac|video|clip|mv|song|track|music)\b|"
    r"(?:mở|mo|phát|phat|bật|bat|nghe|xem|play|watch)\b"
    r")(?:\s+(?:bài hát|bai hat|bài nhạc|bai nhac|ca khúc|ca khuc|bài|bai|nhạc|nhac|video|clip|mv|song|track|music))?[\s,.;:\-]*",
    re.IGNORECASE,
)

_LEADING_SEARCH_ACTION_RE = re.compile(
    r"^(?:"
    r"(?:tìm kiếm|tim kiem|tìm|tim|tra cứu|tra cuu|search for|search|look up|find)\s*"
    r"(?:bài hát|bai hat|bài nhạc|bai nhac|ca khúc|ca khuc|bài|bai|nhạc|nhac|video|clip|mv|thông tin về|thong tin ve|về|ve|for|about)?"
    r")\b[\s,.;:\-]*",
    re.IGNORECASE,
)

_TRAILING_ENGINE_CLAUSE_RE = re.compile(
    r"\s+(?:ở\s+|o\s+)?(?:trên|tren|trong|bằng|bang|qua|với|voi|on|in|via)\s+(?:trang\s+|trình duyệt\s+|trinh duyet\s+)?"
    r"(?:youtube|yt|google|duckduckgo|ddg|github|wikipedia|wiki|trình duyệt|trinh duyet|browser|web)"
    r"(?:\s+(?:giúp tôi|giup toi|giúp mình|giup minh|nhé|nhe|nha|đi|di))?[.?!]*$",
    re.IGNORECASE,
)


def extract_clean_browser_query(raw_target: str, engine: str = "") -> dict:
    """Clean conversational browser/YouTube/search prefixes and detect play-media intent."""
    raw = str(raw_target or "").strip().strip("'\"`<>")
    detected_engine = str(engine or "").strip().lower()
    raw_lower = raw.lower()
    if not detected_engine or detected_engine == "auto":
        if "youtube" in raw_lower or re.search(r"\byt\b", raw_lower):
            detected_engine = "youtube"
        elif "github" in raw_lower:
            detected_engine = "github"
        elif "wikipedia" in raw_lower or re.search(r"\bwiki\b", raw_lower):
            detected_engine = "wikipedia"
        elif "duckduckgo" in raw_lower or re.search(r"\bddg\b", raw_lower):
            detected_engine = "duckduckgo"
        else:
            detected_engine = "google"

    if raw.startswith(("http://", "https://")) or (
        " " not in raw and re.match(r"^[A-Za-z0-9.\-]+(?:\.[A-Za-z]{2,}|:\d+)(?:/.*)?$", raw)
    ):
        return {
            "query": raw,
            "engine": detected_engine,
            "wantsPlay": False,
            "isDirectUrl": True,
        }

    s = re.sub(r"^/(?:browser|web|search)\b[\s,.;:\-]*", "", raw, flags=re.IGNORECASE).strip()
    s = re.sub(r"^(?:youtube|yt|google|duckduckgo|ddg|github|wikipedia|wiki)\b[\s,.;:\-]+", "", s, flags=re.IGNORECASE).strip()

    wants_play = bool(
        re.search(
            r"\b(?:mở bài|mo bai|phát bài|phat bai|bật bài|bat bai|nghe bài|nghe bai|mở nhạc|mo nhac|phát nhạc|phat nhac|bật nhạc|bat nhac|nghe nhạc|nghe nhac|mở ca khúc|mo ca khuc|phát ca khúc|phat ca khuc|mở video|mo video|phát video|phat video|xem video|play song|play music|play video|play track|listen to)\b",
            raw_lower,
        )
    )

    for _ in range(10):
        prev = s
        s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
        s = _LEADING_POLITE_RE.sub("", s).strip()
        s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
        m_nav = _LEADING_BROWSER_NAV_RE.match(s)
        if m_nav:
            nav_txt = m_nav.group(0).lower()
            if "youtube" in nav_txt or "yt" in nav_txt:
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
            s = s[m_search.end():].strip()
            s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
        m_play = _LEADING_PLAY_ACTION_RE.match(s)
        if m_play:
            wants_play = True
            s = s[m_play.end():].strip()
            s = _LEADING_CONJUNCTIONS_RE.sub("", s).strip()
        if s == prev:
            break

    m_trail = _TRAILING_ENGINE_CLAUSE_RE.search(s)
    if m_trail:
        trail_txt = m_trail.group(0).lower()
        if "youtube" in trail_txt or "yt" in trail_txt:
            detected_engine = "youtube"
        s = s[:m_trail.start()].strip()

    s = re.sub(r"\s+(?:giúp tôi|giup toi|giúp mình|giup minh|cho tôi|cho toi|nhé|nhe|nha)[.?!]*$", "", s, flags=re.IGNORECASE).strip()
    s = s.strip(" '\"`<>.,;:-")

    if s.lower() in ("youtube", "yt", "google", "duckduckgo", "ddg", "github", "wikipedia", "wiki", "trình duyệt", "trinh duyet", "browser"):
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

    search_q = re.sub(r"\bcủa\b", "", q, flags=re.IGNORECASE)
    search_q = re.sub(r"\s+", " ", search_q).strip() or q
    yt_results_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(search_q)
    try:
        raw_bytes, _ = _http_get(yt_results_url, timeout_sec=timeout_sec, max_bytes=512 * 1024)
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
    return None


def build_browser_search_url(target: str, engine: str = "google") -> tuple[str, bool]:
    """Return `(url, is_direct_url)` for a URL or search query."""
    raw = str(target or "").strip().strip("'\"`<>")
    if not raw:
        raise WebToolError("invalid_target", "Browser target URL or search query must not be empty.")
    if raw.startswith(("http://", "https://")):
        return raw, True
    if " " not in raw and re.match(r"^[A-Za-z0-9.\-]+(?:\.[A-Za-z]{2,}|:\d+)(?:/.*)?$", raw):
        return "https://" + raw, True

    cleaned = extract_clean_browser_query(raw, engine=engine)
    clean_q = cleaned["query"]
    eng = str(engine or cleaned["engine"] or "google").strip().lower()
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


def open_in_browser(target: str, engine: str = "google", play_media: bool | None = None) -> dict:
    """Open a URL, search query, or direct YouTube video in the user's default browser (`xdg-open`)."""
    cleaned = extract_clean_browser_query(target, engine=engine)
    clean_target = cleaned["query"] if not cleaned["isDirectUrl"] else str(target or "").strip()
    eff_engine = str(engine or cleaned["engine"] or "google").strip().lower()
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
            final_url, is_direct = build_browser_search_url(clean_target, engine=eff_engine)
    else:
        final_url, is_direct = build_browser_search_url(clean_target or target, engine=eff_engine)

    in_test_harness = (
        os.environ.get("AQUA_TEST_NO_EXTERNAL_EDITOR") == "1"
        or os.environ.get("ALIS_TEST_HARNESS") == "1"
        or bool(os.environ.get("AQUA_TEST_WEB_FIXTURE"))
    )
    opened = False
    if not in_test_harness and shutil.which("xdg-open"):
        try:
            subprocess.Popen(
                ["xdg-open", final_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            opened = True
        except Exception:
            opened = False

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

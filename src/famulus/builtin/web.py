"""Web search (SearXNG) and page fetch."""
import html
import html.parser
import ipaddress
import re
import socket
import urllib.parse

import httpx

from .. import config
from ..plugins.base import BasePlugin, spec

UA = "Mozilla/5.0 (compatible; famulus/1.0)"


def search(query: str, max_results: int = 6) -> list[dict]:
    r = httpx.get(
        f"{config.SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        headers={"User-Agent": UA},
        timeout=20,
    )
    r.raise_for_status()
    return [
        {
            "title": res.get("title", ""),
            "url": res.get("url", ""),
            "snippet": res.get("content", "")[:300],
        }
        for res in r.json().get("results", [])[: int(max_results)]
    ]


class _TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "header", "footer", "nav"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _is_private(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
        return any(
            ipaddress.ip_address(i[4][0]).is_private
            or ipaddress.ip_address(i[4][0]).is_loopback
            for i in infos
        )
    except (socket.gaierror, ValueError):
        return True


def fetch(url: str, max_chars: int = 6000) -> str:
    if not url.startswith(("http://", "https://")) or _is_private(url):
        return "error: URL not allowed"
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=25, follow_redirects=True)
    r.raise_for_status()
    if "html" not in r.headers.get("content-type", "html"):
        return r.text[: int(max_chars)]
    p = _TextExtractor()
    p.feed(r.text)
    text = html.unescape(re.sub(r"\s+", " ", " ".join(p.parts)))
    return text[: int(max_chars)]


class WebPlugin(BasePlugin):
    name = "web"
    tools = [
        spec("web_search", "Search the web. Returns titles, URLs and snippets.",
             {"query": {"type": "string"}, "max_results": {"type": "integer"}},
             ["query"]),
        spec("web_fetch",
             "Fetch a web page and return its text content. Use after "
             "web_search to read a result.",
             {"url": {"type": "string"}}, ["url"]),
    ]

    def execute(self, tool: str, args: dict) -> object:
        if tool == "web_search":
            return search(args["query"], int(args.get("max_results", 6)))
        if tool == "web_fetch":
            return fetch(args["url"])
        raise ValueError(f"unknown tool {tool}")

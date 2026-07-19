"""Shared HTTP fetch helpers enforcing the politeness rules (spec ground rule 3):
one request per URL per run, robots.txt honoured, identifying UA, conditional GET,
bounded timeout with a single retry.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

TIMEOUT_SECONDS = 20
RETRY_BACKOFF_SECONDS = 3


def build_user_agent(repo_url: str, maintainer_email: str) -> str:
    return f"las-venue-tracker/1.0 (+{repo_url}; mailto:{maintainer_email})"


class RobotsCache:
    """Fetches robots.txt at most once per domain per run."""

    def __init__(self, user_agent: str):
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._parsers:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{origin}/robots.txt")
            try:
                rp.read()
            except Exception:
                # Unreachable robots.txt: fail open, matching RobotFileParser's own
                # behaviour when a site has none (most volunteer sites don't).
                pass
            self._parsers[origin] = rp
        return self._parsers[origin].can_fetch(self._user_agent, url)


@dataclass
class FetchResult:
    status_code: int
    text: str | None
    etag: str | None
    last_modified: str | None
    not_modified: bool


class FetchError(Exception):
    pass


def fetch(
    url: str,
    *,
    user_agent: str,
    robots: RobotsCache,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    if not robots.allowed(url):
        raise FetchError(f"disallowed by robots.txt: {url}")

    headers = {"User-Agent": user_agent}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_exc: Exception | None = None
    for attempt in range(2):  # initial attempt + one retry
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
            if resp.status_code == 304:
                return FetchResult(304, None, etag, last_modified, True)
            resp.raise_for_status()
            return FetchResult(
                status_code=resp.status_code,
                text=resp.text,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                not_modified=False,
            )
        except Exception as exc:  # noqa: BLE001 - caller wraps per-source, see main.py
            last_exc = exc
            if attempt == 0:
                time.sleep(RETRY_BACKOFF_SECONDS)
    raise FetchError(f"{url}: {last_exc}") from last_exc

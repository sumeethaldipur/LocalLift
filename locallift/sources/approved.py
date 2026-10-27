"""Permitted page retrieval for venue/organizer verification (allow-listed hosts + robots.txt)."""
from __future__ import annotations

import hashlib
import html
import re
from urllib import robotparser
from urllib.parse import urlparse

from .. import config, db
from ..timeutil import iso, now_utc
from .http import SourceError, request

_robots: dict[str, robotparser.RobotFileParser] = {}
UA = "LocalLift"


def _robots_ok(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        try:
            r = request("approved", "GET", base + "/robots.txt", allowed_hosts={p.hostname.lower()}, retries=0, timeout=10)
            rp.parse(r.text.splitlines())
        except SourceError:
            rp.parse([])  # no robots.txt reachable: default allow
        _robots[base] = rp
    return rp.can_fetch(UA, url)


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fetch(url: str, max_chars: int = 6000) -> dict:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or host not in config.settings.approved_hosts:
        raise SourceError(f"{host or url} is not an approved source host", kind="not_allowed")
    if not _robots_ok(url):
        raise SourceError("robots.txt disallows automated retrieval of this page", kind="forbidden")
    r = request("approved", "GET", url, allowed_hosts={host}, timeout=20, retries=1,
                headers={"Accept": "text/html,application/xhtml+xml"})
    text = html_to_text(r.text)[:max_chars]
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text)
    sid = "page:" + hashlib.sha1(url.encode()).hexdigest()[:16]
    ts = iso(now_utc())
    db.ex("""INSERT INTO source_item(id,provider,kind,url,title,published_at,retrieved_at,content,access_status,license,provenance)
             VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET retrieved_at=excluded.retrieved_at,
             content=excluded.content,title=excluded.title""",
          (sid, host, "page", url, html.unescape(title_m.group(1).strip())[:200] if title_m else url, None, ts,
           db.j({"text": text}), "ok", "permitted retrieval (robots.txt checked); excerpted", "real_source"))
    return {"source_id": sid, "url": url, "title": title_m.group(1).strip()[:200] if title_m else url,
            "retrieved_at": ts, "text": text}

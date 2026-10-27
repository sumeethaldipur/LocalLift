"""Live integration verification. Calls each real provider once and writes
docs/LIVE_INTEGRATION_RESULTS.md (provider, request time, success/error, sample identity, access mode).
No credentials or full personal posts are written to the report.

    .venv/bin/python scripts/live_check.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from locallift import business, config  # noqa: E402
from locallift.sources import approved, news, nyc_official, nycparks, social, ticketmaster  # noqa: E402
from locallift.sources.http import SourceError  # noqa: E402
from locallift.timeutil import fmt_ny, iso, now_utc  # noqa: E402

rows = []


def check(category, provider, access, fn):
    t = iso(now_utc())
    t0 = time.monotonic()
    try:
        ok, sample = True, fn()
    except SourceError as e:
        ok, sample = False, f"{e.kind}: {e}"
    except Exception as e:  # report, don't hide
        ok, sample = False, f"{e.__class__.__name__}: {e}"
    rows.append((category, provider, t, ok, access, str(sample)[:220].replace("|", "/"), int((time.monotonic() - t0) * 1000)))
    print(("OK  " if ok else "FAIL"), provider, "-", str(sample)[:160])


def main():
    if config.settings.env != "live":
        sys.exit("Run in live mode.")
    p = business.get_profile()
    lat, lon = p["location"]["lat"], p["location"]["lon"]

    def tm():
        r = ticketmaster.discover(lat, lon, 2, 14)
        e = r["events"][0] if r["events"] else None
        return f"{len(r['events'])} events; first: {e['provider_id']} '{e['title']}' {fmt_ny(e['start_utc'])}" if e else "0 events"
    check("events", "Ticketmaster Discovery v2", "API key (free developer)" if config.settings.ticketmaster_key else "no key", tm)

    def parks():
        r = nycparks.discover(lat, lon, 2, 14)
        e = r["events"][0]
        return (f"{len(r['events'])} nearby of {r['citywide_rows']} rows; first guid {e['provider_id']} '{e['title']}' "
                f"{fmt_ny(e['start_utc'])}; dataset updated {r['dataset_updated_at']}")
    check("events", "NYC Parks Public Events (w3wp-dpdi)", "public SODA API, no key", parks)

    def goth():
        r = news.gothamist()
        i = r["items"][0]
        return f"{len(r['items'])} items; latest '{i['title']}' published {fmt_ny(i['published_at'])}"
    check("news", "Gothamist RSS", "publisher feed, no key", goth)

    def gd():
        r = news.gdelt('"Madison Square Garden"', timespan="7d", maxrecords=10)
        i = r["items"][0] if r["items"] else None
        return f"{len(r['items'])} articles; first '{i['title']}' ({i['domain']}, seen {fmt_ny(i['published_at'])})" if i else f"0 articles {r.get('note', '')}"
    check("news", "GDELT DOC 2.0", "public API, no key (1 req / 5 s)", gd)

    def permits():
        r = nyc_official.permits()
        i = r["items"][0] if r["items"] else None
        return f"{len(r['items'])} relevant permits; first {i['id']} '{i['title']}' effective {fmt_ny(i['effective_start'])}" if i else "0 relevant permits"
    check("news (official)", "NYC Permitted Event Information (tvpp-9vvx)", "public SODA API, no key", permits)

    def mta():
        r = nyc_official.mta_alerts()
        i = r["items"][0] if r["items"] else None
        return f"{len(r['items'])} alerts naming Penn/Herald Sq as affected" + (f"; first {i['id']} '{i['title']}'" if i else " (successful empty result)")
    check("news (official)", "MTA subway alerts (GTFS-RT JSON)", "public feed, no key", mta)

    def bsky():
        r = social.bluesky_search('"Madison Square Garden"')
        c = r["windows"]["current"]
        return (f"window {c['start']}→{c['end']}: {c['sample_size']} posts, {c['unique_posts']} unique, "
                f"{c['unique_authors']} authors, truncated={c['truncated']}; trend {r['trend']['label']}; "
                f"first post id {c['post_ids'][0] if c['post_ids'] else '-'}")
    check("social", "Bluesky app.bsky.feed.searchPosts", "app password (PDS)" if config.settings.bsky_handle else
          "unauthenticated via api.bsky.app", bsky)

    def bsky_public():
        from locallift.sources.http import request
        r = request("bluesky", "GET", social.BSKY_PUBLIC, params={"q": "test", "limit": 1}, retries=0)
        return f"HTTP {r.status_code}"
    check("social", "Bluesky public.api.bsky.app (searchPosts)", "unauthenticated public cache host", bsky_public)

    def x():
        st = social.x_status()
        if st["status"] != "connected":
            raise SourceError(f"{st['status']}: {st['detail']} NEXT: {st['next_step']}", kind="auth")
        r = social.x_search('"Madison Square Garden"')
        s = r.get("search") or {}
        return f"search {s.get('sample_size')} posts; counts {r.get('counts')}"
    check("social", "Twitter/X recent search + counts", "app-only bearer (pay-per-use)" if social.x_has_credentials() else "no credentials", x)

    def page(url):
        r = approved.fetch(url)
        return f"{r['source_id']} '{r['title'][:80]}' {len(r['text'])} chars"
    check("verification", "fetchApprovedSource (nycgovparks.org)", "allow-listed host, robots.txt checked",
          lambda: page("https://www.nycgovparks.org/parks/greeley-square-park"))
    first_article = news.gothamist()["items"][0]["url"]
    check("verification", "fetchApprovedSource (gothamist.com article)", "allow-listed host, robots.txt checked",
          lambda: page(first_article))

    out = ROOT / "docs" / "LIVE_INTEGRATION_RESULTS.md"
    lines = ["# Live integration results", "",
             f"Generated by `scripts/live_check.py` at {fmt_ny(now_utc())} (New York). No credentials are recorded.", "",
             "| Category | Provider | Request time (UTC) | Result | Access mode | Sample identity / error | ms |",
             "|---|---|---|---|---|---|---|"]
    for c, prov, t, ok, acc, s, ms in rows:
        lines.append(f"| {c} | {prov} | {t} | {'success' if ok else '**failed**'} | {acc} | {s} | {ms} |")
    out.write_text("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the two profile cards shown on the GitHub profile README.

Cards
-----
* card-activity-*   counts authored in the last twelve months
* card-languages-*  public code volume by language, in bytes

Both are emitted in a light and a dark variant, from one template, so the
committed SVGs never drift apart. Output is deterministic: identical input
produces byte-identical files, which lets the workflow skip empty commits.

Usage
-----
    GITHUB_TOKEN=... python3 scripts/profile_cards.py [--user NAME]

The token is optional but recommended: unauthenticated search is rate limited
to a few requests per minute.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import calendar
from datetime import date, datetime, timezone

API = "https://api.github.com"
ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"

H = 104
W_ACTIVITY, W_LANGUAGES = 300, 470

PALETTE = {
    "light": {
        "ink": "#1f2328",
        "muted": "#57606a",
        "faint": "#6e7681",
        "rule": "#d8dee4",
        "accent": "#3d4b8c",
        "ramp": ["#3d4b8c", "#55639f", "#6d7bb2", "#8593c5", "#9dabd8", "#b5c3eb", "#ccd4f2"],
    },
    "dark": {
        "ink": "#e6edf3",
        "muted": "#9198a1",
        "faint": "#8b949e",
        "rule": "#30363d",
        "accent": "#a7b2e8",
        "ramp": ["#a7b2e8", "#8d99d8", "#737fc8", "#5965b3", "#48539b", "#39417f", "#2c3366"],
    },
}

SERIF = "Iowan Old Style, Palatino Linotype, Palatino, Georgia, Songti SC, Noto Serif CJK SC, serif"
SANS = (
    "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, Helvetica Neue, "
    "Arial, PingFang SC, Noto Sans CJK SC, sans-serif"
)

TOP_LANGS = 5   # + "Other" = six legend entries, two rows of three


# ── GitHub API ──────────────────────────────────────────────────────────────
def _request(path: str, params: dict[str, str] | None = None) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "profile-cards",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _search_count(endpoint: str, query: str) -> int:
    payload = _request(f"/search/{endpoint}", {"q": query, "per_page": "1"})
    return int(payload["total_count"])


def _window() -> date:
    """Twelve calendar months back, as a single lower bound.

    A full rolling window is the honest reading of "past 12 months", and it
    makes the card reflect a burst of work immediately. Update noise is
    controlled instead by the schedule: the workflow runs weekly and only
    commits when the rendered SVG actually changes.
    """
    today = date.today()
    year, month = today.year - 1, today.month
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def collect(user: str) -> dict:
    start = _window().isoformat()

    counts = {
        "commits": _search_count("commits", f"author:{user} author-date:>={start}"),
        "prs": _search_count("issues", f"author:{user} type:pr created:>={start}"),
        "issues": _search_count("issues", f"author:{user} type:issue created:>={start}"),
    }

    bytes_by_lang: dict[str, int] = {}
    for repo in _request(f"/users/{user}/repos", {"per_page": "100", "type": "owner"}):
        if repo.get("fork") or repo["name"].lower() == user.lower():
            continue
        for lang, size in _request(f"/repos/{repo['full_name']}/languages").items():
            bytes_by_lang[lang] = bytes_by_lang.get(lang, 0) + int(size)

    total = sum(bytes_by_lang.values()) or 1
    ordered = sorted(bytes_by_lang.items(), key=lambda kv: (-kv[1], kv[0]))
    head, tail = ordered[:TOP_LANGS], ordered[TOP_LANGS:]
    if tail:
        head = head + [("Other", sum(v for _, v in tail))]

    return {
        "since": start,
        "counts": counts,
        "languages": [
            {"name": name, "pct": round(size / total * 100, 1)} for name, size in head
        ],
    }


# ── rendering ───────────────────────────────────────────────────────────────
def _frame(c: dict, label: str, width: int) -> str:
    return f"""  <line x1="0" y1="0.5" x2="{width}" y2="0.5" stroke="{c['rule']}" stroke-width="1"/>
  <line x1="0" y1="0.5" x2="36" y2="0.5" stroke="{c['accent']}" stroke-width="2"/>
  <text x="0" y="24" font-family="{SANS}" font-size="9.5" letter-spacing="1.9"
        fill="{c['muted']}">{label}</text>
"""


def _svg(c: dict, title: str, body: str, width: int) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {H}" width="{width}" height="{H}" role="img" aria-label="{html.escape(title)}">
  <title>{html.escape(title)}</title>
{body}</svg>
"""


def render_activity(c: dict, data: dict) -> str:
    cells = [
        (data["counts"]["commits"], "COMMITS"),
        (data["counts"]["prs"], "PULL REQUESTS"),
        (data["counts"]["issues"], "ISSUES"),
    ]
    body = _frame(c, "ACTIVITY  ·  PAST 12 MONTHS", W_ACTIVITY)
    for i, (value, label) in enumerate(cells):
        x = i * 100
        body += (
            f'  <text x="{x}" y="76" font-family="{SERIF}" font-size="34" '
            f'fill="{c["ink"]}">{value}</text>\n'
            f'  <text x="{x}" y="96" font-family="{SANS}" font-size="8.5" '
            f'letter-spacing="1.3" fill="{c["faint"]}">{label}</text>\n'
        )
    return _svg(c, f"Activity: {cells[0][0]} commits, {cells[1][0]} pull requests, "
                   f"{cells[2][0]} issues in the last 12 months", body, W_ACTIVITY)


def render_languages(c: dict, data: dict) -> str:
    body = _frame(c, "LANGUAGES  ·  BY BYTES", W_LANGUAGES)

    gap, bar_y, bar_h = 2.0, 36.0, 6.0
    langs = data["languages"]
    usable = W_LANGUAGES - gap * max(len(langs) - 1, 0)
    x = 0.0
    for i, lang in enumerate(langs):
        seg = usable * lang["pct"] / 100.0
        body += (f'  <rect x="{x:.2f}" y="{bar_y}" width="{max(seg, 1.2):.2f}" '
                 f'height="{bar_h}" fill="{c["ramp"][i]}"/>\n')
        x += seg + gap

    for i, lang in enumerate(langs):
        col, row = i % 3, i // 3
        cx, cy = col * 150, 66 + row * 26
        body += (
            f'  <rect x="{cx}" y="{cy - 7}" width="7" height="7" fill="{c["ramp"][i]}"/>\n'
            f'  <text x="{cx + 13}" y="{cy}" font-family="{SANS}" font-size="10" '
            f'fill="{c["muted"]}">{html.escape(lang["name"])}</text>\n'
            f'  <text x="{cx + 140}" y="{cy}" text-anchor="end" font-family="{SANS}" '
            f'font-size="10" fill="{c["ink"]}">{lang["pct"]:.1f}%</text>\n'
        )

    top = langs[0] if langs else {"name": "n/a", "pct": 0.0}
    return _svg(c, f"Languages by bytes; largest share: {top['name']} {top['pct']:.1f}%", body + "", W_LANGUAGES)


CARDS = {"activity": render_activity, "languages": render_languages}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("PROFILE_USER", "planetarium1001"))
    ap.add_argument("--out", default=str(ASSETS))
    args = ap.parse_args()

    try:
        data = collect(args.user)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as exc:
        print(f"::error::failed to collect metrics: {exc}", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, render in CARDS.items():
        for theme, colors in PALETTE.items():
            path = out / f"card-{name}-{theme}.svg"
            path.write_text(render(colors, data), encoding="utf-8")
            written.append(path.name)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"collected at {stamp} (window since {data['since']})")
    print(f"  counts     {data['counts']}")
    print(f"  languages  " + ", ".join(f"{l['name']} {l['pct']}%" for l in data["languages"]))
    print("  wrote      " + ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

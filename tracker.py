#!/usr/bin/env python3
"""AI usage-limit tracker (Pearlcity LLC). Stdlib only, no keys.

  python3 tracker.py collect-hn [--days 7]   # pull public HN comments/stories via Algolia API
  python3 tracker.py import-x inbox/x-*.json # import public X posts an agent saved (id,text,created_at,author)
  python3 tracker.py review                  # list auto-extracted rows awaiting review
  python3 tracker.py ok 3 5 / drop 4         # approve or reject rows by index (only "ok" rows publish)
  python3 tracker.py build                   # write docs/index.html, docs/feed.xml, docs/data.json

Only posts with a self-reported NUMBER (percent of a 5-hour window / week, or time-to-limit)
are kept. Every number links to its source. Vague complaints are dropped.
"""
import datetime as dt, glob, html, json, os, re, statistics, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "reports.json")
SITE_URL = os.environ.get("SITE_URL", "https://pearlcity-llc.github.io/pearlcity-ai-usage-limits/")
UA = "PearlcityUsageLimitTracker/0.1"

VENDORS = [
    ("Claude", r"\b(claude|anthropic|opus|sonnet)\b"),
    ("Codex", r"\b(codex|chatgpt|openai)\b"),
    ("Cursor", r"\bcursor\b"),
    ("Gemini", r"\b(gemini|antigravity|jules)\b"),
]
PLANS = [
    (r"\bmax\s*20x\b|\$\s?200\b|\b200\s?(usd|dollar)", "$200 tier"),
    (r"\bmax\s*5x\b|\$\s?100\b", "$100 tier"),
    (r"\b(ultra)\b|\$\s?250\b", "Ultra"),
    (r"\b(pro\+|pro plus)\b|\$\s?60\b", "$60 tier"),
    (r"\b(pro|plus)\b|\$\s?20\b", "$20 tier"),
    (r"\b(team|business|enterprise)\b", "Team/Business"),
    (r"\bfree (plan|tier)\b", "Free"),
]
PCT = r"(\d{1,3}(?:\.\d)?)\s?%"
METRICS = [
    # % of weekly used by one 5-hour window / one session
    ("pct_week_per_5h", re.compile(r"(?:one|a|1|single|full)\s+(?:5|five)[\s-]?h(?:ou)?r?s?\s+(?:window|session|limit|block)[^.]{0,40}?" + PCT + r"[^.]{0,25}?week", re.I)),
    ("pct_week_per_5h", re.compile(PCT + r"\s+of\s+(?:my\s+|the\s+)?week(?:ly)?[^.]{0,40}?(?:per|each|one|a|single)\s+(?:5|five)[\s-]?h", re.I)),
    # % of weekly used in a day / N sessions
    ("pct_week_per_day", re.compile(r"(?:in|within)\s+(?:one|a|1|single)\s+day[^.]{0,40}?" + PCT + r"[^.]{0,30}?week", re.I)),
    ("pct_week_per_day", re.compile(r"(?:one|1|single)\s+day\s+of\s+[^.]{0,40}?" + PCT + r"[^.]{0,30}?week", re.I)),
    ("pct_week_per_day", re.compile(PCT + r"\s+of\s+(?:my\s+|the\s+)?week(?:ly)?[^.]{0,30}?(?:in|within)\s+(?:one|a|1|single)\s+day", re.I)),
    ("pct_week_any", re.compile(r"(?:hit|used|burn(?:ed|t)?|at|consumed|took|ate)\s+(?:up\s+)?" + PCT + r"\s+of\s+(?:my\s+|the\s+)?week(?:ly)?", re.I)),
    ("pct_week_any", re.compile(PCT + r"\s+of\s+(?:my\s+|the\s+)?weekly\s+(?:limit|usage|quota|cap)", re.I)),
    # hours until 5h window hit
    ("hours_to_5h_limit", re.compile(r"(?:hit|reached|ran into|maxed)\s+(?:the\s+|my\s+)?(?:5|five)[\s-]?h(?:ou)?r?\s+(?:limit|window|cap)\s+(?:in|after)\s+(\d{1,3}(?:\.\d)?)\s*(?:min|minutes)", re.I)),
    ("days_to_week_limit", re.compile(r"(?:hit|reached|ran out of|burn(?:ed|t)? through|maxed)\s+(?:the\s+|my\s+)?week(?:ly)?\s+(?:limit|usage|quota|cap)?\s*(?:in|after|within)\s+(\d(?:\.\d)?)\s*days?", re.I)),
]
METRIC_LABEL = {
    "pct_week_per_5h": "% of weekly limit used by one full 5-hour window",
    "pct_week_per_day": "% of weekly limit used in one day",
    "pct_week_any": "% of weekly limit reported used (any period)",
    "hours_to_5h_limit": "Minutes to hit the 5-hour limit",
    "days_to_week_limit": "Days to hit the weekly limit",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def clean(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()


def extract(text):
    """Return list of (vendor, plan, metric, value, snippet)."""
    out = []
    low = text.lower()
    vendors = [v for v, rx in VENDORS if re.search(rx, low)]
    if len(vendors) != 1:  # ambiguous or none: drop (comparisons are hard to attribute)
        return out
    vendor = vendors[0]
    plan = next((p for rx, p in PLANS if re.search(rx, low)), "plan not stated")
    seen = set()
    for metric, rx in METRICS:
        for m in rx.finditer(text):
            val = float(m.group(1))
            if metric.startswith("pct") and not (0 < val <= 100):
                continue
            key = (metric if not metric.startswith("pct_week") else "pct", round(val, 1))
            if key in seen:
                continue
            seen.add(key)
            s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
            out.append((vendor, plan, metric, val, ("…" if s else "") + text[s:e].strip() + ("…" if e < len(text) else "")))
    return out


def load_db():
    return json.load(open(DB)) if os.path.exists(DB) else []


def save_db(rows):
    rows.sort(key=lambda r: r["date"], reverse=True)
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    json.dump(rows, open(DB, "w"), indent=1, ensure_ascii=False)


def add_rows(db, source, url, date, author, text):
    have = {(r["url"], r["metric"], r["value"]) for r in db}
    n = 0
    for vendor, plan, metric, val, snip in extract(text):
        if (url, metric, val) in have:
            continue
        db.append({"vendor": vendor, "plan": plan, "metric": metric, "value": val, "date": date[:10],
                   "source": source, "url": url, "author": author, "quote": snip[:280],
                   "status": "pending"})
        n += 1
    return n


def collect_hn(days=7):
    db = load_db()
    since = int(time.time() - days * 86400)
    queries = ["weekly limit", "usage limit", "5 hour limit", "5-hour window", "rate limit claude code",
               "codex limit", "cursor usage limit", "gemini limit", "weekly usage", "limits claude max"]
    added = scanned = 0
    for q in queries:
        for tag in ("comment", "story"):
            params = urllib.parse.urlencode({"query": q, "tags": tag, "hitsPerPage": 100,
                                             "numericFilters": f"created_at_i>{since}"})
            try:
                hits = get("https://hn.algolia.com/api/v1/search_by_date?" + params)["hits"]
            except Exception as e:
                print("HN query failed:", q, e, file=sys.stderr)
                continue
            for h in hits:
                scanned += 1
                text = clean((h.get("title") or "") + " " + (h.get("story_text") or "") + " " + (h.get("comment_text") or ""))
                url = f"https://news.ycombinator.com/item?id={h['objectID']}"
                added += add_rows(db, "Hacker News", url, h["created_at"], h.get("author", ""), text)
            time.sleep(0.5)
    save_db(db)
    print(f"HN: scanned {scanned} items, added {added} numeric reports, total {len(db)}")


def import_x(paths):
    db = load_db()
    added = scanned = 0
    for p in paths:
        for post in json.load(open(p)):
            scanned += 1
            url = post.get("url") or f"https://x.com/{post.get('author','i')}/status/{post['id']}"
            added += add_rows(db, "X", url, post.get("created_at", ""), post.get("author", ""), clean(post.get("text", "")))
    save_db(db)
    print(f"X: scanned {scanned} posts, added {added} numeric reports, total {len(db)}")


def esc(s):
    return html.escape(str(s), quote=True)


def build(window_days=28):
    db = load_db()
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=window_days)).isoformat()
    # Only human/agent-reviewed rows are published (status "ok"); auto-extraction is noisy.
    recent = [r for r in db if r["date"] >= cutoff and r.get("status") == "ok"]
    groups = {}
    for r in recent:
        groups.setdefault((r["vendor"], r["plan"], r["metric"]), []).append(r["value"])
    rows_html = []
    for (v, p, m), vals in sorted(groups.items()):
        med = statistics.median(vals)
        unit = "%" if m.startswith("pct") else (" min" if m == "hours_to_5h_limit" else " days")
        rows_html.append(f"<tr><td>{esc(v)}</td><td>{esc(p)}</td><td>{esc(METRIC_LABEL[m])}</td>"
                         f"<td><b>{med:g}{unit}</b></td><td>{min(vals):g}–{max(vals):g}{unit}</td><td>{len(vals)}</td></tr>")
    items_html = "".join(
        f"<li><span class=tag>{esc(r['vendor'])} · {esc(r['plan'])}</span> <b>{r['value']:g}"
        f"{'%' if r['metric'].startswith('pct') else ''}</b> {esc(METRIC_LABEL[r['metric']].replace('% of', 'of'))}"
        f"<br><q>{esc(r['quote'])}</q> <a href='{esc(r['url'])}' rel=nofollow>{esc(r['source'])}, {esc(r['date'])}</a></li>"
        for r in recent[:60])
    vendors_covered = sorted({r["vendor"] for r in recent})
    missing = [v for v, _ in VENDORS if v not in vendors_covered]
    page = f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Coding Usage-Limit Index: Claude, Codex, Cursor, Gemini</title>
<meta name=description content="Weekly, sourced, self-reported usage-limit numbers for paid AI coding plans.">
<link rel=alternate type=application/rss+xml title="Usage-Limit Index" href="feed.xml">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.4rem;text-align:left;font-size:.95rem}}
.tag{{background:#eef;border-radius:4px;padding:0 .3rem;font-size:.85rem}}li{{margin:.6rem 0}}q{{color:#555}}
.note{{background:#fff8e1;padding:.6rem 1rem;border-radius:6px}}footer{{margin-top:3rem;color:#777;font-size:.85rem}}</style>
<h1>How much AI coding do you actually get?</h1>
<p>Self-reported usage-limit numbers for paid <b>Claude, Codex, Cursor and Gemini</b> plans, gathered from public posts.
Every number links to its source. Updated weekly. <a href="feed.xml">RSS</a> · <a href="data.json">raw data</a></p>
<p class=note>Edition of <b>{today.isoformat()}</b>, covering the last {window_days} days: <b>{len(recent)}</b> numeric reports.
These are anecdotes, not measurements; small samples swing a lot.{' No reports yet for: ' + ', '.join(missing) + '.' if missing else ''}</p>
<h2>Medians by plan</h2>
<table><tr><th>Tool</th><th>Plan</th><th>Measure</th><th>Median</th><th>Range</th><th>Reports</th></tr>
{''.join(rows_html) or '<tr><td colspan=6>No numeric reports in this window yet.</td></tr>'}</table>
<h2>Latest reports</h2><ul>{items_html}</ul>
<h2>Method</h2><p>An agent searches public Hacker News (Algolia API) and public X posts. It keeps only posts that name one tool and state a number
(percent of a 5-hour window or week, or time to hit a limit), and drops vague or multi-tool posts. “Plan” is only what the poster said.
Found a wrong number or want yours counted? Open an issue on GitHub.</p>
<footer>A free project by Pearlcity LLC. Not affiliated with Anthropic, OpenAI, Anysphere or Google.</footer></html>"""
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    open(os.path.join(ROOT, "docs", "index.html"), "w").write(page)
    json.dump(recent, open(os.path.join(ROOT, "docs", "data.json"), "w"), indent=1, ensure_ascii=False)
    now = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    summary = "; ".join(f"{v} {p}: median {statistics.median(vals):g} ({len(vals)} reports, {METRIC_LABEL[m]})"
                        for (v, p, m), vals in sorted(groups.items())) or "No numeric reports this week."
    feed = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<title>AI Coding Usage-Limit Index</title><link>{SITE_URL}</link>
<description>Weekly self-reported usage limits for Claude, Codex, Cursor and Gemini plans.</description>
<item><title>Edition {today.isoformat()}: {len(recent)} reports</title><link>{SITE_URL}#{today.isoformat()}</link>
<guid isPermaLink="false">edition-{today.isoformat()}</guid><pubDate>{now}</pubDate>
<description>{esc(summary)}</description></item></channel></rss>"""
    open(os.path.join(ROOT, "docs", "feed.xml"), "w").write(feed)
    open(os.path.join(ROOT, "docs", ".nojekyll"), "w").write("")
    print(f"built: {len(recent)} reports in window, {len(groups)} groups, vendors={vendors_covered}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "collect-hn":
        collect_hn(int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--days" else 7)
    elif cmd == "import-x":
        files = [f for a in sys.argv[2:] for f in glob.glob(a)]
        import_x(files)
    elif cmd == "review":
        db = load_db()
        for i, r in enumerate(db):
            if r.get("status") == "pending":
                print(i, r["vendor"], r["plan"], r["metric"], r["value"], r["url"], "|", r["quote"])
    elif cmd in ("ok", "drop"):
        db = load_db()
        idx = {int(x) for x in sys.argv[2:]}
        for i, r in enumerate(db):
            if i in idx:
                r["status"] = cmd
        save_db(db)
        print(f"marked {len(idx)} as {cmd}")
    elif cmd == "build":
        build()
    else:
        print(__doc__)

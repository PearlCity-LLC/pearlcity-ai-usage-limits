# AI coding usage-limit index (Claude · Codex · Cursor · Gemini)

A weekly static page of **self-reported, sourced** usage-limit numbers for paid AI coding plans: how much of a weekly limit one task, day or 5-hour window really uses. Free, by Pearlcity LLC.

Page: `docs/index.html` · RSS: `docs/feed.xml` · Data: `docs/data.json` (published) and `data/reports.json` (all rows incl. pending/dropped).

## Weekly pipeline (agent-run, $0)
1. `python3 tracker.py collect-hn --days 7`: public Hacker News via the free Algolia API.
2. Agent saves public X search results (read-only X connector) to `inbox/x-YYYY-MM-DD.json`, then `python3 tracker.py import-x 'inbox/x-*.json'`.
3. `python3 tracker.py review`, then `ok <ids>` / `drop <ids>`. Only reviewed rows are published, because regex extraction catches hypotheticals and UI glitches.
4. `python3 tracker.py build`, then commit. `.github/workflows/pages.yml` deploys `docs/` to GitHub Pages.

Rules: only posts naming exactly one tool and stating a number; every number links to its source; plan = only what the poster said. No auto-replies on X.

## First edition (2026-10-19, local preview)
5 reviewed reports (Claude 2, Codex 1, Gemini 2, Cursor 0) out of 466 scanned posts. That's thin, so run 1–2 more collection passes (more X queries, plus asking people in the launch post to submit numbers) before launching. See `preview.png`.

Metrics: GitHub traffic API (views/uniques, 14-day window) + stars. Not affiliated with Anthropic, OpenAI, Anysphere or Google.

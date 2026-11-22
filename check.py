#!/usr/bin/env python3
"""Analyse which pages of a site OpenAI holds in its web search index and page cache.

Uses the OpenAI Responses API with the web_search tool in cache-only mode
(external_web_access: false). Nothing is fetched live by OpenAI, so the check
does not trigger any ChatGPT-User or OAI-SearchBot visit on your site.
You bring your own OpenAI API key (OPENAI_API_KEY).

Two checks per URL:
  cache  - ask the model to open the exact URL. If OpenAI holds a copy of the
           full page, the tool returns it (open_page) with a "Crawled: <age>" label.
  index  - search for the page title, restricted to your domain. If the page is
           in OpenAI's search index, it comes back as a search result with a
           snippet and a "Crawled: <age>" label.

A positive result is evidence. A negative result is only a hint: in our tests a
page present in a layer was missed in about 5-7% of single runs, so use
--passes 2 before concluding that a page is absent.

Usage:
  export OPENAI_API_KEY=...
  python3 check.py --sitemap https://www.example.com/sitemap.xml --sample 30
  python3 check.py --urls urls.txt --passes 2 --out results.csv --report report.md
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import csv
import datetime as dt
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import httpx
    from openai import OpenAI
except ImportError:  # pragma: no cover
    sys.exit("Install the OpenAI SDK first: pip install -r requirements.txt")

USER_AGENT = "openai-cache-index-audit/1.1"
TOKENS_PER_CALL = 8500  # median observed with gpt-6-luna, effort low
LABEL_RE = {
    "crawled": re.compile(r"\bCrawled:\s*([^;\n]+)", re.I),
    "lines": re.compile(r"\bTotal lines:\s*(\d+)", re.I),
}


# ------------------------------------------------------------------ inputs

def http_get(url: str, timeout: float = 20) -> bytes:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def urls_from_sitemap(url: str, seen: set[str] | None = None, max_sitemaps: int = 50) -> list[str]:
    """Read a sitemap or a sitemap index (nested indexes are followed)."""
    seen = seen if seen is not None else set()
    if url in seen or len(seen) >= max_sitemaps:
        return []
    seen.add(url)
    root = ET.fromstring(http_get(url))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if root.tag.endswith("sitemapindex"):
        out: list[str] = []
        for loc in root.findall("s:sitemap/s:loc", ns):
            out += urls_from_sitemap(loc.text.strip(), seen, max_sitemaps)
        return out
    return [loc.text.strip() for loc in root.findall("s:url/s:loc", ns) if loc.text]


def urls_from_file(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".csv"):
        rows = list(csv.DictReader(text.splitlines()))
        col = next((c for c in (rows[0].keys() if rows else []) if c.lower() in ("url", "urls", "loc")), None)
        if not col:
            sys.exit("CSV needs a 'url' column")
        return [r[col].strip() for r in rows if r[col].strip()]
    return [line.strip() for line in text.splitlines() if line.strip().startswith("http")]


def section_of(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.strip("/")
    return "/" + path.split("/")[0] if path else "/"


def stratified_sample(urls: list[str], n: int, seed: int) -> list[str]:
    """Pick n URLs spread across site sections (first path segment), homepage first."""
    if n <= 0 or n >= len(urls):
        return urls
    rng = random.Random(seed)
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for u in urls:
        groups[section_of(u)].append(u)
    for g in groups.values():
        rng.shuffle(g)
    picked = [u for u in urls if section_of(u) == "/"][:1]
    order = sorted(groups, key=lambda s: -len(groups[s]))
    while len(picked) < n:
        added = False
        for s in order:
            pool = [u for u in groups[s] if u not in picked]
            if pool and len(picked) < n:
                picked.append(pool[0])
                added = True
        if not added:
            break
    return picked


def page_title(url: str) -> str:
    """Read the page title (else H1) from your own site to build the index query."""
    try:
        raw = http_get(url).decode("utf-8", "replace")
    except Exception:
        return ""
    for pattern in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pattern, raw, re.I | re.S)
        if m:
            text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
            if text:
                return text[:150]
    return ""


# ------------------------------------------------------------------ matching

def norm(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    return f"{(p.hostname or '').lower()}{urllib.parse.unquote(p.path or '/').rstrip('/') or '/'}"


def domain_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def age_days(label: str) -> float | None:
    """Convert a relative 'Crawled:' label into approximate days."""
    if not label:
        return None
    fixed = {"today": 0, "yesterday": 1, "last week": 7, "last month": 30, "last year": 365}
    if label in fixed:
        return fixed[label]
    m = re.match(r"([\d.]+) (\w+)", label)
    if not m:
        return None
    unit = {"day": 1, "week": 7, "month": 30, "year": 365}.get(m.group(2).rstrip("s"))
    return float(m.group(1)) * unit if unit else None


# ------------------------------------------------------------------ API

def call(client: OpenAI, model: str, prompt: str, domain: str | None, effort: str) -> dict:
    tool: dict = {"type": "web_search", "external_web_access": False}
    if domain:
        tool["filters"] = {"allowed_domains": [domain]}
    resp = client.responses.create(
        model=model,
        tools=[tool],
        tool_choice="auto",
        include=["web_search_call.action.sources", "web_search_call.results"],
        reasoning={"effort": effort},
        input=prompt,
    )
    return resp.model_dump(mode="json")


def find_result(resp: dict, target: str, action_type: str) -> dict | None:
    """First structured tool result for `target` produced by an action of `action_type`."""
    rank = 0
    for item in resp.get("output", []):
        if item.get("type") != "web_search_call" or (item.get("action") or {}).get("type") != action_type:
            continue
        for result in item.get("results") or []:
            rank += 1
            if norm(str(result.get("url", ""))) == norm(target):
                snippet = str(result.get("snippet") or "")
                found = {k: (m.group(1).strip() if (m := rx.search(snippet)) else "") for k, rx in LABEL_RE.items()}
                found.update(rank=rank, title=str(result.get("title") or ""))
                return found
    return None


def check_cache(client, model, url, effort):
    prompt = f"Open this exact URL and summarize the page in one sentence. Do not run a search, only open the URL.\n{url}"
    resp = call(client, model, prompt, None, effort)
    return resp, find_result(resp, url, "open_page")


def check_index(client, model, url, query, effort):
    prompt = f'Search the web for: "{query}"\nRun exactly one search with this query. Do not open any page. List the URLs you found.'
    resp = call(client, model, prompt, domain_of(url), effort)
    return resp, find_result(resp, url, "search")


# ------------------------------------------------------------------ run

def run_one(client, args, url: str) -> dict:
    row = {"url": url, "section": section_of(url), "query": "", "in_cache": "", "cache_crawled": "",
           "cache_total_lines": "", "in_index": "", "index_crawled": "", "index_rank": "", "passes": 0, "error": ""}
    raw: dict = {}
    query = "" if args.no_index else page_title(url)
    try:
        for p in range(1, args.passes + 1):
            row["passes"] = p
            if row["in_cache"] != "yes":
                resp, hit = check_cache(client, args.model, url, args.effort)
                raw[f"cache_{p}"] = resp
                if hit:
                    row.update(in_cache="yes", cache_crawled=hit["crawled"], cache_total_lines=hit["lines"])
                    query = query or hit["title"]  # fallback: title of the cached copy
                else:
                    row["in_cache"] = "not found"
            if not args.no_index and row["in_index"] != "yes":
                if not query:
                    row["in_index"] = "no query"
                else:
                    resp, hit = check_index(client, args.model, url, query, args.effort)
                    raw[f"index_{p}"] = resp
                    if hit:
                        row.update(in_index="yes", index_crawled=hit["crawled"], index_rank=hit["rank"])
                    else:
                        row["in_index"] = "not found"
            if row["in_cache"] == "yes" and (args.no_index or row["in_index"] == "yes"):
                break
    except Exception as e:  # keep going on API errors
        row["error"] = f"{type(e).__name__}: {e}"[:300]
    row["query"] = query
    if args.raw_dir:
        name = re.sub(r"[^a-zA-Z0-9]+", "_", norm(url))[:120]
        Path(args.raw_dir, f"{name}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return row


# ------------------------------------------------------------------ report

def bucket(days: float | None) -> str:
    if days is None:
        return "unknown"
    if days <= 7:
        return "≤ 1 week"
    if days <= 31:
        return "≤ 1 month"
    if days <= 92:
        return "1–3 months"
    if days <= 365:
        return "3–12 months"
    return "> 1 year"


def diagnosis(r: dict) -> str:
    c, i = r["in_cache"] == "yes", r["in_index"] == "yes"
    if r["error"]:
        return "error"
    if c and i:
        ia = age_days(r["index_crawled"])
        return "indexed, old snippet (> 3 months)" if ia is not None and ia > 92 else "indexed and cached"
    if c and not i:
        return "cached, not found in index"
    if i and not c:
        return "indexed, not found in cache"
    return "not found in either"


def write_report(path: str, rows: list[dict], args, total_urls: int, source: str) -> None:
    n = len(rows)
    pct = lambda k: f"{k}/{n} ({100 * k / n:.0f}%)" if n else "0"
    cache = sum(r["in_cache"] == "yes" for r in rows)
    index = sum(r["in_index"] == "yes" for r in rows)
    diag = collections.Counter(diagnosis(r) for r in rows)
    lines = [
        f"# OpenAI cache & index report — {domain_of(rows[0]['url']) if rows else ''}",
        "",
        f"Run on {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · source: {source} · "
        f"{n} pages checked out of {total_urls} · model {args.model} · {args.passes} pass(es)",
        "",
        "## Summary",
        "",
        f"- In OpenAI's page cache: **{pct(cache)}**",
        f"- In OpenAI's search index: **{pct(index)}**" if not args.no_index else "- Index check skipped",
        "",
        "| Diagnosis | Pages |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in diag.most_common()],
        "",
        "## Age of the copies",
        "",
        "| Age | Cache | Index |",
        "|---|---:|---:|",
    ]
    order = ["≤ 1 week", "≤ 1 month", "1–3 months", "3–12 months", "> 1 year", "unknown"]
    cb = collections.Counter(bucket(age_days(r["cache_crawled"])) for r in rows if r["in_cache"] == "yes")
    ib = collections.Counter(bucket(age_days(r["index_crawled"])) for r in rows if r["in_index"] == "yes")
    lines += [f"| {b} | {cb.get(b, 0)} | {ib.get(b, 0)} |" for b in order if cb.get(b) or ib.get(b)]
    lines += ["", "## By section", "", "| Section | Pages | In cache | In index |", "|---|---:|---:|---:|"]
    secs = collections.defaultdict(list)
    for r in rows:
        secs[r["section"]].append(r)
    for s, rs in sorted(secs.items(), key=lambda x: -len(x[1])):
        lines.append(f"| `{s}` | {len(rs)} | {sum(r['in_cache'] == 'yes' for r in rs)} | {sum(r['in_index'] == 'yes' for r in rs)} |")

    def listing(title: str, pick, cols):
        sel = [r for r in rows if pick(r)]
        if not sel:
            return
        lines.extend(["", f"## {title} ({len(sel)})", ""])
        for r in sel[:50]:
            lines.append(f"- {r['url']}" + "".join(f" · {lab} {r[k]}" for lab, k in cols if r[k]))

    listing("Cached but not found in the index", lambda r: diagnosis(r) == "cached, not found in index",
            [("cache:", "cache_crawled")])
    listing("Indexed with a snippet older than 3 months", lambda r: diagnosis(r).startswith("indexed, old"),
            [("index:", "index_crawled"), ("cache:", "cache_crawled")])
    listing("Indexed but not found in the cache", lambda r: diagnosis(r) == "indexed, not found in cache",
            [("index:", "index_crawled")])
    listing("Not found in either layer", lambda r: diagnosis(r) == "not found in either", [])
    lines += [
        "",
        "## How to read this",
        "",
        "- **Cache** = the full copy of the page ChatGPT reads when it opens it. It is refreshed by ChatGPT-User, "
        "when a question needs the page and the copy is more than ~30 minutes old.",
        "- **Index** = the short snippet ChatGPT finds when it searches. It is refreshed by OAI-SearchBot only.",
        "- **Cached, not in index**: ChatGPT can read the page when someone gives the URL, but won't find it by searching.",
        "- **Old snippet**: ChatGPT may show outdated information from this page until OAI-SearchBot comes back.",
        "- **Not found** is a hint, not proof: single runs miss about 5–7% of present pages. Use `--passes 2` "
        "and cross-check with your server logs (OAI-SearchBot and ChatGPT-User, IPs verified against OpenAI's ranges).",
        "- `Crawled:` labels are undocumented by OpenAI and may change. Date your checks.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sitemap", help="Sitemap or sitemap index URL")
    src.add_argument("--urls", help="TXT (one URL per line) or CSV with a 'url' column")
    ap.add_argument("--sample", type=int, default=30, help="Number of pages to check, spread across sections (0 = all). Default 30")
    ap.add_argument("--seed", type=int, default=1, help="Random seed for the sample")
    ap.add_argument("--passes", type=int, default=1, choices=(1, 2, 3), help="Re-check pages not found (2 recommended)")
    ap.add_argument("--model", default="gpt-6-luna", help="Default gpt-6-luna, the model OpenAI says powers ChatGPT Free")
    ap.add_argument("--effort", default="low", choices=("none", "low", "medium", "high"))
    ap.add_argument("--workers", type=int, default=4, help="Parallel requests")
    ap.add_argument("--no-index", action="store_true", help="Only run the cache check")
    ap.add_argument("--out", default="openai-cache-index.csv", help="CSV output")
    ap.add_argument("--report", default="openai-cache-index-report.md", help="Markdown report")
    ap.add_argument("--raw-dir", help="Save raw API responses (JSON) in this folder")
    ap.add_argument("--yes", action="store_true", help="Do not ask for confirmation before calling the API")
    args = ap.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY first (your own OpenAI API key).")
    all_urls = list(dict.fromkeys(urls_from_sitemap(args.sitemap) if args.sitemap else urls_from_file(args.urls)))
    if not all_urls:
        sys.exit("No URL found.")
    urls = stratified_sample(all_urls, args.sample, args.seed)
    calls = len(urls) * (1 if args.no_index else 2)
    print(f"{len(all_urls)} URLs found, {len(urls)} to check, at least {calls} API calls "
          f"(~{calls * TOKENS_PER_CALL / 1e6:.2f}M tokens with {args.model}, more with --passes).", file=sys.stderr)
    if not args.yes and sys.stdin.isatty() and input("Continue? [y/N] ").strip().lower() != "y":
        return 1
    if args.raw_dir:
        Path(args.raw_dir).mkdir(parents=True, exist_ok=True)

    client = OpenAI()
    rows: list[dict] = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda u: run_one(client, args, u), urls), 1):
            rows.append(row)
            print(f"[{i}/{len(urls)}] cache={row['in_cache'] or '-':10} {row['cache_crawled']:14} "
                  f"index={row['in_index'] or '-':10} {row['index_crawled']:14} {row['url']}", file=sys.stderr)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + ["diagnosis"])
        w.writeheader()
        w.writerows({**r, "diagnosis": diagnosis(r)} for r in rows)
    write_report(args.report, rows, args, len(all_urls), args.sitemap or args.urls)
    print(f"\nDone in {time.time() - t0:.0f}s. CSV: {args.out} · report: {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

---
name: openai-cache-index
description: Check which pages of a website are in OpenAI's web search index and page cache (what ChatGPT can find and read), with the age of each copy, from a sitemap or a URL list, and explain what to fix. Use when the user asks whether their pages are indexed or cached by ChatGPT / OpenAI, why ChatGPT shows outdated information about a page, or how to read OAI-SearchBot and ChatGPT-User activity in their logs.
---

# OpenAI cache & index check

The script `check.py` (repo root) uses the OpenAI Responses API in cache-only mode (`external_web_access: false`). It triggers **no visit** on the user's site.

## What the two layers are

- **Index**: a short snippet (title + ~200 characters) that ChatGPT finds when it searches. Refreshed only by **OAI-SearchBot**.
- **Cache**: the full page ChatGPT reads when it opens a URL. Refreshed by **ChatGPT-User**, when a question needs the page and the copy is more than ~30 minutes old. The first answer after an update still uses the old copy.
- GPTBot (training) feeds neither.

## Steps

1. **Get the key.** The user brings their own OpenAI API key in `OPENAI_API_KEY`. Never print it, never write it to a file in the repo. If it is missing, ask the user to export it in their shell.
2. **Install**: `pip install -r requirements.txt` (only the `openai` SDK).
3. **Pick the input.** A sitemap URL (`--sitemap`) or a list (`--urls file.txt` or a CSV with a `url` column). If the user gives only a domain, try `https://<domain>/sitemap.xml`, then `robots.txt` for a `Sitemap:` line.
4. **Size the run and confirm the cost.** Default sample: 30 pages spread across sections (~0.5M tokens with `gpt-6-luna`). Tell the user the number of pages and calls before running. Use `--yes` only after they agree.
5. **Run**:
   ```bash
   python3 check.py --sitemap <url> --sample 30 --passes 2 --yes --out results.csv --report report.md
   ```
6. **Read `report.md` and explain it**, in this order:
   - share of pages in the index and in the cache;
   - pages **cached but not in the index**: ChatGPT can read them when given the URL but won't find them by searching;
   - pages with an **index snippet older than 3 months**: ChatGPT may show outdated facts until OAI-SearchBot returns;
   - pages **not found in either layer**;
   - sections where coverage is weak.
7. **Optional: cross with logs** if the user provides server logs or an export (Oncrawl, raw access logs):
   - keep only OpenAI bots whose IP is in OpenAI's published ranges (`https://openai.com/searchbot.json`, `https://openai.com/chatgpt-user.json`, `https://openai.com/gptbot.json`); a ChatGPT-User UA from another IP is often a scanner;
   - the index age should match the last OAI-SearchBot visit on the page; the cache age the last ChatGPT-User visit;
   - list pages never visited by OAI-SearchBot, and pages updated after its last visit (stale snippet).

## Rules when explaining

- **Found = evidence, not found = a hint.** A single run misses about 5–7% of present pages. Recommend `--passes 2` and the logs before concluding a page is absent.
- **A ChatGPT-User hit is a read, not a citation.** Most citations come from the index snippet and leave no hit; one URL is fetched at most about once per 30 minutes. Never present hit counts as visibility volume.
- **No promise of visibility.** Present what to check and test, not a guaranteed result.
- `Crawled:` labels are undocumented by OpenAI and may change: date every result.
- Things known not to help with OpenAI in the author's tests: IndexNow (no OpenAI visit). What makes OAI-SearchBot come back is not established; suggest internal links from frequently crawled pages and an up-to-date sitemap as tests, not fixes.

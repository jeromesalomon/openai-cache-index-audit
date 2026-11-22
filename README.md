# OpenAI cache & index audit

Find out which pages of your site OpenAI holds in its **web search index** and in its **page cache**, and how old each copy is. The check runs through the OpenAI API in cache-only mode, so it **does not trigger any visit** from ChatGPT-User or OAI-SearchBot on your site.

Companion resource to my article [ChatGPT Index and Cache: The New Stories Your Server Logs Tell](https://jerome-salomon.bcsbl.fr/ai-search-blog/chatgpt-index-cache/). Step-by-step method: [How to Audit Your Site in ChatGPT](https://jerome-salomon.bcsbl.fr/resources/chatgpt-index-cache-audit/).

## Why two checks?

OpenAI keeps two separate copies of your pages:

| | Search index | Page cache |
|---|---|---|
| What it holds | a short snippet (title + ~200 characters) | the full page |
| Filled by | OAI-SearchBot | ChatGPT-User (when a question needs to read the page) |
| Used when | ChatGPT searches | ChatGPT opens a page |
| Age shown by the API | last OAI-SearchBot visit | last ChatGPT-User fetch |

The same URL can be in one and not the other, or hold two different versions. In my tests, OAI-SearchBot never refreshed a cached copy, and ChatGPT-User never refreshed the index.

## How it works

For each URL, two calls to the Responses API with the `web_search` tool and `external_web_access: false`:

- **Cache**: the model is asked to open the exact URL. If OpenAI has a copy, the tool returns it (`open_page`) with a `Crawled: <age>` label and the page length (`Total lines`).
- **Index**: the script reads your page's `<title>` (or H1) from your site, then asks the model to search for it, restricted to your domain. If the page is indexed, it comes back as a search result with a `Crawled: <age>` label.

The script only counts **structured tool results** (`web_search_call.results`), never the model's text answer.

## Install

```bash
git clone https://github.com/jeromesalomon/openai-cache-index-audit.git
cd openai-cache-index-audit
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
```

## Usage

```bash
# Analyse a site from its sitemap (sitemap indexes supported): 30 pages spread across sections
python3 check.py --sitemap https://www.example.com/sitemap.xml

# Bigger sample, two passes, custom outputs
python3 check.py --sitemap https://www.example.com/sitemap.xml --sample 100 --passes 2 \
  --out results.csv --report report.md

# From a list of URLs (TXT, one per line, or CSV with a "url" column), every URL
python3 check.py --urls urls.txt --sample 0
```

The script shows the number of API calls and an estimated token count, then asks for confirmation (`--yes` skips it).

| Option | Default | |
|---|---|---|
| `--sample` | 30 | Pages to check, spread across site sections (first path segment), homepage first. `0` = all |
| `--passes` | 1 | Re-check pages not found (2 recommended before concluding a page is absent) |
| `--model` | `gpt-6-luna` | The model OpenAI says powers ChatGPT Free |
| `--effort` | `low` | Reasoning effort |
| `--workers` | 4 | Parallel requests |
| `--no-index` | off | Cache check only (half the calls) |
| `--out` / `--report` | `openai-cache-index.csv` / `openai-cache-index-report.md` | CSV per URL, Markdown analysis |
| `--raw-dir` | none | Save raw JSON responses |

## Output

**The report** (`--report`, Markdown): share of pages in cache and in the index, a diagnosis count, the age of the copies, a breakdown by section, and the lists that need action (cached but not indexed, old index snippets, indexed but not cached, not found).

**The CSV** (`--out`), one row per URL:

| Column | Meaning |
|---|---|
| `in_cache` | `yes` / `not found` |
| `cache_crawled` | Age of the cached copy (`today`, `3 weeks ago`, `1.4 years ago`…) |
| `cache_total_lines` | Length of the cached page |
| `in_index` | `yes` / `not found` / `no query` (no title found) |
| `index_crawled` | Age of the indexed snippet |
| `index_rank` | Position among the returned results |
| `query` | Title used for the index check (from your page, else from OpenAI's cached copy) |
| `diagnosis` | indexed and cached · cached, not found in index · indexed, not found in cache · indexed, old snippet · not found in either |
| `section`, `passes`, `error` | |

## How to read the results

- **Found = evidence. Not found = a hint.** In my tests, a page present in a layer was missed in about 5–7% of single runs. Use `--passes 2`, and cross-check with your server logs.
- **In cache, not in index**: ChatGPT can read the page when someone gives the URL, but it won't find it through search. This is the case of a site that ChatGPT-User has visited but OAI-SearchBot has not crawled.
- **In index, not in cache**: the page can be found and cited from its snippet; nobody has needed to read it in full yet (or not recently).
- **Old `index_crawled` on a page you updated**: ChatGPT shows the old snippet until OAI-SearchBot comes back. IndexNow did not bring OpenAI in my tests.
- **Old `cache_crawled`**: the copy is refreshed when a question needs the page and the copy is more than ~30 minutes old. The first answer after an update still uses the old copy.
- Cross with your logs: the index age should match the last OAI-SearchBot visit, the cache age the last ChatGPT-User visit (verify IPs against OpenAI's published ranges).

## Limits

- `Crawled:` and the other labels are **undocumented**. OpenAI can change or remove them at any time.
- Ages are relative and coarse (`last month`, `2 months ago`).
- The index check depends on your title being a reasonable query. Very generic titles shared by many pages of your site can push the target page out of the results.
- Results depend on the model and on OpenAI's systems at the time of the run. Date your checks.
- The API is not ChatGPT: same stores, but a different orchestration.

## Cost

Each call used about 8,500 tokens with `gpt-6-luna` in my tests, so about 17,000 tokens per URL with both checks (more with `--passes`). The default sample of 30 pages is about 0.5M tokens.

## Use it with Claude Code or Codex

The repo ships a Claude Code skill (`skills/openai-cache-index/SKILL.md`) and an `AGENTS.md` for Codex. Open the repo in your agent and ask, for example:

> Check which pages of https://www.example.com are in OpenAI's cache and index, from its sitemap, and explain what I should fix first.

The agent runs the script, reads the report and explains it. If you give it your server logs, it can cross the results with OAI-SearchBot and ChatGPT-User visits.

## No install? Use the Colab notebook

[`openai_cache_index_checker.ipynb`](openai_cache_index_checker.ipynb) runs the same analysis in Google Colab: add your OpenAI API key to Colab's Secrets (`OPENAI_API_KEY`), paste your sitemap, run the cells.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1hSb1UQegPVT67KpcQJX7-h3qSBOKZsa6)

The notebook embeds `check.py` unchanged. After editing the script, rebuild it with `python3 build_notebook.py`.

## Credits

The `external_web_access: false` approach comes from my November 2025 tests. The cache and index mechanics were investigated further with RESONEO (*What ChatGPT pulls, what it shows, what it cites*, July–August 2026).

Jérôme Salomon — [jerome-salomon.bcsbl.fr](https://jerome-salomon.bcsbl.fr/)

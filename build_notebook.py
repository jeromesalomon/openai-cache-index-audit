"""Build openai_cache_index_checker.ipynb (Google Colab) from check.py, so the notebook and the script share one logic.
usage: python3 build_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = (HERE / "check.py").read_text(encoding="utf-8")


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text, hidden=False):
    meta = {"cellView": "form"} if hidden else {}
    return {"cell_type": "code", "metadata": meta, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


cells = [
    md("""
# OpenAI cache & index checker

Find out which pages of your site OpenAI holds in its **search index** and in its **page cache**, and how old each copy is.

- The **index** holds your URL, title and ~200 characters. OAI-SearchBot refreshes it. ChatGPT uses it to *find* pages.
- The **cache** holds the full page. ChatGPT-User refreshes it, at most about once every 30 minutes. ChatGPT uses it to *read* pages.

The check runs through the OpenAI API in **cache-only mode** (`external_web_access: false`): OpenAI doesn't go to the web, so **it triggers no visit on your site**.

You need your own OpenAI API key. Each page costs about 17,000 tokens (two checks), more with a second pass.

Companion to the article [ChatGPT Index and Cache: The New Stories Your Server Logs Tell](https://jerome-salomon.bcsbl.fr/ai-search-blog/chatgpt-index-cache/) by Jérôme Salomon.

**How to use it:** run the cells in order (▶ or `Ctrl/Cmd + Enter`). Step 1 asks for your key, step 2 for your sitemap.
"""),
    md("""
## 0. Install
"""),
    code("""
#@title Install the libraries
!pip install -q openai httpx pandas
"""),
    md("""
## 1. Your OpenAI API key

**Recommended:** add it to Colab's **Secrets** (🔑 icon in the left sidebar), name it `OPENAI_API_KEY`, and allow this notebook to access it. The key then never appears in the notebook.

Otherwise, the cell asks for it in a hidden field. **Never paste your key into a code cell**: it would stay in the notebook if you share or save it.
"""),
    code("""
#@title Load the API key
import os, getpass
key = None
try:
    from google.colab import userdata
    key = userdata.get("OPENAI_API_KEY")
except Exception:
    pass
if not key:
    key = getpass.getpass("OpenAI API key (hidden): ")
os.environ["OPENAI_API_KEY"] = key.strip()
del key
print("API key loaded." if os.environ["OPENAI_API_KEY"] else "No key.")
""", hidden=True),
    md("""
## 2. What to check

Paste your sitemap URL (a sitemap index works too), **or** a list of URLs, one per line.

- **Sample**: how many pages to check, spread across the sections of your site, homepage first. `0` = all of them.
- **Passes**: `2` re-checks the pages not found the first time. A single run misses about 5–7% of pages that are there.
"""),
    code("""
#@title Settings
SITEMAP_URL = "https://www.example.com/sitemap.xml"  #@param {type:"string"}
URL_LIST = ""  #@param {type:"string"}
SAMPLE = 30  #@param {type:"integer"}
PASSES = 2  #@param [1, 2, 3] {type:"raw"}
MODEL = "gpt-6-luna"  #@param {type:"string"}
EFFORT = "low"  #@param ["none", "low", "medium", "high"]
WORKERS = 4  #@param {type:"integer"}
CACHE_ONLY = False  #@param {type:"boolean"}
""", hidden=True),
    md("""
## 3. The checker

This cell writes the same `check.py` as the open-source script, so the notebook and the script give the same results. You don't need to read it.
"""),
    code("%%writefile check.py\n" + CHECK, hidden=False),
    md("""
## 4. Find the pages and estimate the cost
"""),
    code("""
#@title List and sample the pages
import importlib, re, argparse
import check
importlib.reload(check)

if URL_LIST.strip():
    all_urls = [u.strip() for u in re.split(r"[\\s,]+", URL_LIST) if u.strip().startswith("http")]
    source = "URL list"
else:
    all_urls = check.urls_from_sitemap(SITEMAP_URL)
    source = SITEMAP_URL
all_urls = list(dict.fromkeys(all_urls))
urls = check.stratified_sample(all_urls, SAMPLE, seed=1)
calls = len(urls) * (1 if CACHE_ONLY else 2)
print(f"{len(all_urls)} URLs found, {len(urls)} to check.")
print(f"At least {calls} API calls, about {calls * check.TOKENS_PER_CALL / 1e6:.2f}M tokens with {MODEL} "
      f"(more with {PASSES} passes on pages not found).")
""", hidden=True),
    md("""
## 5. Run the check

This is the step that calls the API and costs tokens. It takes about 10–20 seconds per page with 4 parallel requests.
"""),
    code("""
#@title Run
import concurrent.futures as cf, time
from openai import OpenAI
import pandas as pd

args = argparse.Namespace(model=MODEL, effort=EFFORT, passes=int(PASSES), no_index=CACHE_ONLY, raw_dir=None)
client = OpenAI()
rows, t0 = [], time.time()
with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
    for i, row in enumerate(pool.map(lambda u: check.run_one(client, args, u), urls), 1):
        rows.append(row)
        print(f"[{i}/{len(urls)}] cache={row['in_cache'] or '-':10} {row['cache_crawled']:14} "
              f"index={row['in_index'] or '-':10} {row['index_crawled']:14} {row['url']}")
for r in rows:
    r["diagnosis"] = check.diagnosis(r)
df = pd.DataFrame(rows)
print(f"\\nDone in {time.time() - t0:.0f}s.")
df[["url", "in_cache", "cache_crawled", "in_index", "index_crawled", "diagnosis", "error"]]
""", hidden=True),
    md("""
## 6. Report
"""),
    code("""
#@title Show the report
from IPython.display import Markdown, display
check.write_report("openai-cache-index-report.md", rows, args, len(all_urls), source)
display(Markdown(open("openai-cache-index-report.md", encoding="utf-8").read()))
""", hidden=True),
    code("""
#@title Download the CSV and the report
df.to_csv("openai-cache-index.csv", index=False)
try:
    from google.colab import files
    files.download("openai-cache-index.csv")
    files.download("openai-cache-index-report.md")
except Exception:
    print("Files saved next to the notebook: openai-cache-index.csv, openai-cache-index-report.md")
""", hidden=True),
    md("""
## How to read the results

- **Found = evidence. Not found = a hint.** Use 2 passes, and cross-check with your server logs.
- **Cached, not in the index:** ChatGPT can read the page when someone gives the URL, but it won't find it by searching.
- **Indexed, not in the cache:** the page can be found and cited from its snippet. Nobody has needed to read it in full recently.
- **Old index snippet on a page you updated:** ChatGPT shows the old version until OAI-SearchBot comes back. IndexNow doesn't bring OpenAI.
- **Cross with your logs:** the index age should match the last OAI-SearchBot visit, the cache age the last ChatGPT-User visit (check bot IPs against OpenAI's published ranges).

**Limits:** the `Crawled:` labels are undocumented by OpenAI and may change at any time. Ages are coarse (`last month`). The API isn't ChatGPT: same stores, different orchestration. Date your checks.

The step-by-step method, with prompts to check a page directly in ChatGPT and how to read your logs: [How to Audit Your Site in ChatGPT](https://jerome-salomon.bcsbl.fr/resources/chatgpt-index-cache-audit/).

Credits: cache-only API approach from my tests since November 2025; index and cache mechanics investigated with [RESONEO](https://think.resoneo.com/chatgpt-retrieval/) (July–August 2026). MIT licence.
"""),
]

nb = {
    "nbformat": 4, "nbformat_minor": 0,
    "metadata": {"colab": {"provenance": [], "name": "openai_cache_index_checker.ipynb"},
                 "kernelspec": {"name": "python3", "display_name": "Python 3"},
                 "language_info": {"name": "python"}},
    "cells": cells,
}
out = HERE / "openai_cache_index_checker.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {out} ({len(cells)} cells)")

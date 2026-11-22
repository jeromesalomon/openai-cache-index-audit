# AGENTS.md

This repo checks which pages of a website are in OpenAI's web search **index** and **page cache**, with the age of each copy. Follow `skills/openai-cache-index/SKILL.md`: it is the full procedure (key handling, cost confirmation, how to run `check.py`, how to explain the report, how to cross with server logs).

Quick start:

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...   # the user's own key; never print or commit it
python3 check.py --sitemap https://www.example.com/sitemap.xml --sample 30 --passes 2 --yes --report report.md
```

Rules:
- Confirm the number of pages and the estimated cost with the user before running.
- A positive result is evidence; "not found" is only a hint.
- ChatGPT-User hits are page reads, not citations; never present them as visibility volume.
- Do not commit results, raw responses or keys (`.gitignore` covers `*.csv` and `raw/`; add `report*.md` outputs to it if you write them in the repo).

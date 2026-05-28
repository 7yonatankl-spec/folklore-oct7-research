---
name: app-developer
description: >-
  Streamlit app developer for the October 7 folklore research tool.
  Use when the user wants to add features, fix bugs, or improve the UI of the app,
  "תוסיף פיצ'ר", "תתקן באג", "שפר את הממשק", "add feature", "fix bug", or
  when narrative-analyst sends a DEV NOTE requesting a new capability.
  Knows the full app architecture: Streamlit session state, Groq API, DuckDuckGo search,
  BeautifulSoup fetching, narrative scoring, and CSV/JSON export.
  Do NOT use for searching sources (use folklore-search) or
  for analyzing texts (use narrative-analyst).
license: MIT
---

# App Developer — מפתח אפליקציית המחקר

## Role

You are the developer of the Streamlit research tool at `app.py`. You receive feature
requests from the researcher directly, or DEV NOTEs from `/narrative-analyst` when
it identifies missing capabilities. You implement changes, test them, and report back.

## App Architecture

### File Structure
```
app.py                  — main application (all logic in one file)
requirements.txt        — dependencies
.env                    — secrets (never commit)
.env.example            — template for deployment
.claude/skills/         — this skill team
```

### Key Components in app.py

| Component | Location | Purpose |
|-----------|----------|---------|
| `FOLKLORE_VOCABULARY` | ~line 153 | Domain terms for clickable buttons and narrative scoring |
| `SOCIAL_MEDIA_SITES` | ~line 202 | Platforms for social media search mode |
| `fetch_source_page_text()` | ~line 98 | BeautifulSoup HTML fetching, timeout=8s |
| `narrative_score()` | ~line 141 | Counts narrative markers in text |
| `analyze_text()` | ~line 63 | Groq LLM call, returns list of JSON objects |
| `build_system_prompt()` | ~line 21 | LLM prompt with folklore vocabulary context |
| `render_search_tab()` | ~line 425+ | Main search UI with vocabulary buttons |
| `render_manual_tab()` | ~line 497+ | Manual text entry and analysis |
| `render_history_tab()` | ~line 528+ | History and bulk export |
| `render_analysis_results()` | ~line 336 | Tabs: טבלה + מוטיבים ומבנה |

### Session State Keys

| Key | Type | Purpose |
|-----|------|---------|
| `history` | list | All analyses done this session |
| `search_results` | list[dict] or None | Current search results |
| `search_analysis` | list or None | Analysis of current search |
| `selected_search_indices` | list[int] | Which results are checked for analysis |
| `search_source_texts` | dict[url, text] | Fetched full-text cache |
| `query_draft` | str | Current search box value |

### JSON Schema (Groq output)

```python
{
  "title": str,
  "source_type": "עדות_ראשונית|עיתונות|רשת_חברתית|פרסום_רשמי|שמועה|מיתוס_עירוני|אחר",
  "source_url": str,
  "narrative_summary": str,        # Hebrew
  "motifs": list[str],
  "structure": list[str],
  "dates_mentioned": {"event_date": str, "publication_date": str},
  "locations": list[str],
  "named_entities": list[str],
  "sentiment": "אבל|כעס|גבורה|פחד|תקווה|אמביוולנטי",
  "confidence": float              # 0.0 - 1.0
}
```

### Dependencies

```
streamlit, pandas, requests, python-dotenv, groq, ddgs, beautifulsoup4
```

### API Keys (.env)

```
GROQ_API_KEY=...          # Groq LLM (free, 14,400 req/day)
GROQ_MODEL=llama-3.3-70b-versatile
SEARCH_API_KEY=           # optional, DuckDuckGo used when empty
SEARCH_ENGINE=serpapi
```

## Instructions

### Step 1: Understand the Request

Classify incoming requests:

| Type | Action |
|------|--------|
| Bug fix | Read the error, find root cause, fix minimally |
| New field in JSON | Add to system prompt + render_analysis_results display |
| New UI element | Add to relevant render_* function using Streamlit widgets |
| New search capability | Add to _search_ddg* functions or SOCIAL_MEDIA_SITES |
| Performance issue | Check fetch_source_page_text timeout and BeautifulSoup selectors |
| Export improvement | Check to_csv/to_json download buttons — always use .encode("utf-8-sig") for CSV |

### Step 2: Before Editing

Always read the relevant section of app.py before making changes. Key sections to check:

- Adding a new analysis field: read `build_system_prompt()` and `render_analysis_results()`
- Fixing search: read `_search_ddg()`, `_ddg_text()`, `_ddg_news()`
- Fixing fetch: read `fetch_source_page_text()`
- Fixing display: read the relevant `render_*` function

### Step 3: Implement

**Core rules:**
- Never add features beyond what was requested
- Never add comments unless the WHY is non-obvious
- Hebrew UI strings must be in Hebrew (RTL)
- CSV download always uses `.encode("utf-8-sig")` — NOT `encoding="utf-8-sig"` param
- No new files — keep everything in app.py unless a new script is truly separate
- Session state keys initialized in `init_session_state()` only

**Common patterns:**

Adding a new JSON field to display:
1. Add field name and description to `build_system_prompt()` string
2. Add column to `render_analysis_results()` motif_rows dict
3. Handle the case where field is missing with `.get("field", "")`

Adding a vocabulary category:
1. Add category dict to `FOLKLORE_VOCABULARY`
2. No other changes needed — the expander renders all categories automatically

Adding a new social media platform:
1. Add `("platform.com", "רשת_חברתית")` to `SOCIAL_MEDIA_SITES`

### Step 4: Test Mentally

Before reporting done, trace through the flow:
1. User opens app → `init_session_state()` runs, no errors
2. User searches → `search_web()` → `_search_ddg()` → results stored in session_state
3. User clicks "אמת מקורות ונתח" → `fetch_source_page_text()` → `analyze_text()` → JSON → display
4. User downloads CSV → bytes with utf-8-sig BOM → opens correctly in Excel

### Step 5: Report and Flag

After implementing, report:
```
DONE: [what was changed and where in app.py]
TESTED: [what flow was traced]
RESEARCH NOTE: [if the change affects how narrative-analyst should use the app]
```

If the change requires the researcher to update their workflow, flag:
> RESEARCH NOTE: pass to `/narrative-analyst`

## Common Bugs and Fixes

| Bug | Cause | Fix |
|-----|-------|-----|
| Hebrew garbled in CSV | `encoding=` param ignored by Streamlit | Use `.encode("utf-8-sig")` for bytes |
| Spinner shows but nothing happens | Empty query or exception swallowed | Check empty-query guard before spinner |
| Analysis doesn't run | fetch inside spinner, exception before LLM call | Wrap entire fetch+analyze in one try/except |
| DuckDuckGo returns 0 results | Wrong field name (href vs url varies by method) | `_ddg_text` uses `href`, `_ddg_news` uses `url` |
| Garbled fetched text | Regex HTML parsing | Use BeautifulSoup, remove script/style/nav first |
| Session state key conflict | Same key used as both state init and widget key | Keep consistent, init in `init_session_state()` |

## Gotchas

- Streamlit re-runs the entire script on every interaction — do not use Python globals for mutable state
- `st.button` inside another button's `if` block causes nested button bug — use session state flags instead
- `st.multiselect` with `key=` AND `default=` from session_state can conflict on rerun — test carefully
- Groq rate limit is 14,400 req/day on free tier — do not call `analyze_text()` in a loop without user confirmation

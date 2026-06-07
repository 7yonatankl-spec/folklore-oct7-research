import collections
import os
import re
import json
import time
import streamlit as st
import pandas as pd
import requests
from groq import Groq
from dotenv import load_dotenv
from ddgs import DDGS
from bs4 import BeautifulSoup

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    val = os.getenv(key, "")
    if not val:
        try:
            val = st.secrets.get(key, default)
        except Exception:
            val = default
    return val


GROQ_API_KEY = _get_secret("GROQ_API_KEY")
GROQ_MODEL = _get_secret("GROQ_MODEL") or "llama-3.1-8b-instant"
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "serpapi")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def build_system_prompt() -> str:
    vocab_lines = ", ".join(
        term for terms in FOLKLORE_VOCABULARY.values() for term in terms
    )
    return (
        "מומחה פולקלור — 7 באוקטובר 2023. נתח טקסט עברי והחזר JSON בלבד, ללא טקסט נוסף. "
        "שיטות: פרופ (1928), Thompson Motif Index, Von Sydow, Herman (1992), Ong.\n"
        f"מילון: {vocab_lines}\n\n"
        "כל אובייקט JSON חייב לכלול:\n"
        "title, source_type(עדות_ראשונית|עיתונות|רשת_חברתית|פרסום_רשמי|שמועה|מיתוס_עירוני|אחר), "
        "source_url, narrative_summary, motifs[], structure[], "
        "dates_mentioned{event_date,publication_date}, locations[], named_entities[], "
        "sentiment(אבל|כעס|גבורה|פחד|תקווה|אמביוולנטי), confidence(0-1), "
        "propp_functions[](ABSENTATION|INTERDICTION|VIOLATION|RECONNAISSANCE|DELIVERY|TRICKERY|"
        "COMPLICITY|VILLAINY|MEDIATION|COUNTERACTION|DEPARTURE|FIRST_FUNCTION_DONOR|HERO_REACTION|"
        "RECEIPT_MAGICAL_AGENT|GUIDANCE|STRUGGLE|BRANDING|VICTORY|LIQUIDATION|RETURN|PURSUIT|"
        "RESCUE|DIFFICULT_TASK|SOLUTION|RECOGNITION|PUNISHMENT|WEDDING), "
        "atu_motifs[], "
        "oral_register(ראשוני|משני|שמיעתי|כתוב_מקורי|נוצר_מחדש|ויראלי|בלתי_ידוע), "
        "narrative_genre(ממולה|עדות_טראומה|סיפור_גבורה|מיתוס_מייסד|אגדה_עירונית|קינה|סיפור_נס|כרוניקה|ספד_אישי|אחר), "
        "intertextual_echoes[], "
        "narrator_perspective(גוף_ראשון_יחיד|גוף_ראשון_רבים|גוף_שלישי|עד_ראייה_ישיר|מדווח_עקיף|קולקטיבי|בלתי_ידוע), "
        "temporal_distance(מיידי|קצר_טווח|בינוני|ארוך_טווח|בלתי_ידוע), "
        "source_reliability_score(1-5), "
        "legend_stage(memorat|fabulate|proto-legend|legend|myth), "
        "memory_community[]."
    )


def build_user_prompt(text: str, title=None, source_url=None, source_type=None) -> str:
    parts = []
    if title:
        parts.append(f"כותרת מקור: {title}")
    if source_type:
        parts.append(f"סוג מקור: {source_type}")
    if source_url:
        parts.append(f"כתובת מקור: {source_url}")
    parts.append("טקסט לניתוח:")
    parts.append(text)
    parts.append("השב רק JSON תקין בלבד, ללא הסברים וללא טקסט נוסף.")
    return "\n".join(parts)


def analyze_text(text: str, title=None, source_url=None, source_type=None) -> list:
    if not client:
        raise RuntimeError("חסר GROQ_API_KEY. הוסף אותו לקובץ .env (חינמי בgroq.com).")
    # Keep input under ~6000 chars to stay within token budget
    text = text[:6000]
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0,
        max_tokens=3000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(text, title=title, source_url=source_url, source_type=source_type)},
        ],
    )
    content = response.choices[0].message.content.strip()
    parsed = _parse_json_robust(content)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def _parse_json_robust(content: str):
    # 1. Direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 2. Strip markdown fences and retry
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # 3. Extract first [...] or {...} block
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = stripped.find(start_char)
        end = stripped.rfind(end_char) + 1
        if start != -1 and end > 0:
            try:
                return json.loads(stripped[start:end])
            except json.JSONDecodeError:
                pass
    # 4. Multiple concatenated objects: collect all with raw_decode
    decoder = json.JSONDecoder()
    results, idx = [], 0
    while idx < len(stripped):
        while idx < len(stripped) and stripped[idx] in " \t\n\r,":
            idx += 1
        if idx >= len(stripped):
            break
        try:
            obj, end = decoder.raw_decode(stripped, idx)
            results.append(obj)
            idx = end
        except json.JSONDecodeError:
            idx += 1
    if results:
        return results if len(results) > 1 else results[0]
    raise ValueError("לא נמצא JSON תקין בתשובת המודל")


def get_youtube_transcript(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
        vid_match = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})", url)
        if not vid_match:
            return ""
        vid_id = vid_match.group(1)
        try:
            segments = YouTubeTranscriptApi.get_transcript(vid_id, languages=["he", "iw"])
        except (NoTranscriptFound, Exception):
            segments = YouTubeTranscriptApi.get_transcript(vid_id)
        return " ".join(s["text"] for s in segments)[:4000]
    except Exception:
        return ""


def fetch_source_page_text(source_url: str, timeout: int = 8) -> str:
    if not source_url:
        return ""
    if "youtube.com" in source_url or "youtu.be" in source_url:
        transcript = get_youtube_transcript(source_url)
        if transcript:
            return f"[תמלול וידאו YouTube]\n{transcript}"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "he,en;q=0.9",
        }
        r = requests.get(source_url, timeout=timeout, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
            tag.decompose()

        domain = re.sub(r"https?://(www\.)?", "", source_url).split("/")[0]

        # edut710.org category pages (/locations/, /people/) list testimonies —
        # drill into the first individual testimony link instead
        if "edut710" in domain and any(seg in source_url for seg in ["/locations/", "/people/", "/events/"]):
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = href if href.startswith("http") else f"https://{domain}{href}"
                if "/testimonies/" in full or "/testimony/" in full:
                    return fetch_source_page_text(full, timeout=timeout)

        if "edut710" in domain:
            main_content = (
                soup.find(class_=re.compile(r"testimony|edut|content|story", re.I))
                or soup.find("body") or soup
            )
        elif "kan.org" in domain:
            main_content = (
                soup.find(class_=re.compile(r"article|content|story|kan", re.I))
                or soup.find("body") or soup
            )
        else:
            main_content = (
                soup.find("article")
                or soup.find("main")
                or soup.find(id=re.compile(r"content|article|main", re.I))
                or soup.find(class_=re.compile(r"content|article|main|post|entry", re.I))
                or soup.find("body") or soup
            )

        lines = [
            el.get_text(separator=" ", strip=True)
            for el in main_content.find_all(["p", "h1", "h2", "h3", "li", "blockquote"])
            if len(el.get_text(strip=True)) > 25
        ]
        result = "\n".join(lines)
        if len(result) < 200:
            result = re.sub(r"\n{3,}", "\n\n", main_content.get_text(separator="\n", strip=True))
        return result[:4000].strip()
    except Exception:
        return ""


def narrative_score(text: str) -> int:
    markers = [
        "אני ", "אנחנו ", "הייתי ", "היינו ", "ראיתי ", "שמעתי ", "הרגשתי ",
        "ברחתי ", "הסתתרתי ", "ניצלתי ", "חבאתי ", "לקחתי ",
        "סיפרה לי", "סיפר לי", "לא אשכח", "עד היום", "זוכר", "זוכרת",
        "מעיד", "מעידה", "מפי", "בפי",
        "עדות", "סיפור", "הצלה", "בריחה", 'ז"ל', 'הי"ד', "נרצח", "ספר",
        "נרצחה", "חטוף", "חטופה", "ניצול", "ניצולה", "מסתור",
        "נהרג", "נהרגה", "נשרף", "נשרפה", "גופה",
        "נחטף", "נחטפה", "שבוי", "שבויה",
        "הציל", "הצילה", "גיבור", "כיתת כוננות",
        "פיצוץ", "ירייה", "יריות", "רימון", "מחבלים", "טרור",
        "אזעקה", "צבע אדום",
        "שמחת תורה", "פסטיבל", "נובה", "7 באוקטובר", "שביעי באוקטובר",
        "בארי", "כפר עזה", "ניר עוז", "רעים", "נחל עוז",
    ]
    return sum(1 for m in markers if m in text)


FOLKLORE_VOCABULARY = {
    "קיבוצים ויישובים": [
        "בארי", "כפר עזה", "ניר עוז", "רעים", "נחל עוז", "הולית", "מגן",
        "נתיב העשרה", "כיסופים", "מפלסים", "ניר יצחק", "סופה", "אורים",
        "תלמי אליהו", "שדי אברהם", "זיקים", "ברור חיל", "יד מורדכי",
    ],
    "אירועים ומקומות": [
        "פסטיבל נובה", "כביש 232", "שמחת תורה", "עוטף עזה",
        "גדר הגבול", "בסיס רעים", "עין השלושה", "מחסום",
    ],
    "אבל וזכר": [
        'ז"ל', 'הי"ד', "נרצח", "נרצחה", "חלל", "נפל", "לזכרו", "לזכרה",
        "יהי זכרו ברוך", "נשמתו תנוח בשלום",
    ],
    "ניצולים ועדויות": [
        "ניצול", "ניצולה", "עדות", "הצלה", "בריחה", "מסתור",
        'ממ"ד', "שריד", "חטוף", "חטופה", "חטופים", "מוחזק בעזה",
    ],
    "כוחות וארגונים": [
        "כיתת כוננות", "חמאס", "נוחבה", "מחבל", "לוחם",
        "צוות קרבי", "כוחות הביטחון", "גדוד", "חטיבה",
    ],
    "כלי נשק": [
        "רימון יד", "RPG", "נשק", "אקדח", "מטען",
        "רקטה", "יריות", "ירי", "כלי ירייה",
    ],
    "נרטיב פולקלורי": [
        "עדות ראשונית", "סיפור הצלה", "סיפור גבורה", "נס", "קורבן",
        "גיבור", "שמועה", "אגדה עירונית", "ספד", "הנצחה",
    ],
}

SOCIAL_MEDIA_SITES = [
    ("twitter.com OR x.com", "רשת_חברתית"),
    ("facebook.com", "רשת_חברתית"),
    ("youtube.com", "רשת_חברתית"),
    ("tiktok.com", "רשת_חברתית"),
    ("t.me", "רשת_חברתית"),
    ("instagram.com", "רשת_חברתית"),
    ("reddit.com", "רשת_חברתית"),
]

TESTIMONY_ARCHIVE_SITES = [
    ("edut710.org", "עדות_ראשונית"),
    ("710360.kan.org.il", "עדות_ראשונית"),
    ("ynet.co.il", "עיתונות"),
    ("haaretz.co.il", "עיתונות"),
    ("mako.co.il", "עיתונות"),
    ("walla.co.il", "עיתונות"),
    ("zman.co.il", "עיתונות"),
]

QUERY_TEMPLATES = {
    "בחר תבנית...": "",
    "עדויות ניצולים — קיבוץ בארי": "עדות ניצול קיבוץ בארי 7 באוקטובר",
    "עדויות ניצולים — פסטיבל נובה": "עדות ניצול פסטיבל נובה 7 באוקטובר",
    "עדויות ניצולים — כפר עזה": "עדות ניצול כפר עזה 7 באוקטובר",
    "עדויות ניצולים — ניר עוז": "עדות ניצול ניר עוז 7 באוקטובר",
    "חטופים — עדויות שחרור": "עדות חטוף שוחרר עזה 7 באוקטובר",
    'כיתות כוננות — סיפורי גבורה': "כיתת כוננות הגנה 7 באוקטובר עדות גבורה",
    'מסתורים וממ"ד': 'התחבאתי ממד 7 באוקטובר עדות',
    "עדויות — ארכיון עדות 710": "site:edut710.org עדות",
    "עדויות — כאן 710": "site:710360.kan.org.il עדות ניצול",
}

ALL_PROPP_FUNCTIONS = [
    ("ABSENTATION", "היעדרות"), ("INTERDICTION", "איסור"), ("VIOLATION", "הפרת האיסור"),
    ("RECONNAISSANCE", "סיור"), ("DELIVERY", "מידע לנבל"), ("TRICKERY", "תחבולה"),
    ("COMPLICITY", "שיתוף פעולה"), ("VILLAINY", "פשע/נזק"), ("MEDIATION", "תיווך"),
    ("COUNTERACTION", "פעולת נגד"), ("DEPARTURE", "יציאה לדרך"),
    ("FIRST_FUNCTION_DONOR", "מתן מבחן"), ("HERO_REACTION", "תגובת הגיבור"),
    ("RECEIPT_MAGICAL_AGENT", "קבלת סיוע"), ("GUIDANCE", "הנחיה"),
    ("STRUGGLE", "מאבק"), ("BRANDING", "סימון"), ("VICTORY", "ניצחון"),
    ("LIQUIDATION", "חיסול המחסור"), ("RETURN", "חזרה"), ("PURSUIT", "מרדף"),
    ("RESCUE", "הצלה"), ("DIFFICULT_TASK", "משימה קשה"), ("SOLUTION", "פתרון"),
    ("RECOGNITION", "זיהוי"), ("PUNISHMENT", "עונש"), ("WEDDING", "תגמול"),
]


_EXCLUDED_DOMAINS = {"wikipedia.org", "he.wikipedia.org", "en.wikipedia.org", "wikidata.org", "britannica.com"}

def _is_excluded(url: str) -> bool:
    domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0]
    return any(excl in domain for excl in _EXCLUDED_DOMAINS)


def _ddg_text(query: str, max_results: int) -> list:
    with DDGS() as ddgs:
        return list(ddgs.text(query, region="wt-wt", max_results=max_results))


def _ddg_news(query: str, max_results: int) -> list:
    with DDGS() as ddgs:
        return list(ddgs.news(query, region="wt-wt", max_results=max_results))


_OCT7_CONTEXT_PHRASES = ['"7 באוקטובר"', '"חרבות ברזל"', '"שמחת תורה 2023"', '"שבעה באוקטובר"']

_HEBREW_STOPWORDS = {
    "של", "עם", "על", "אל", "כל", "גם", "רק", "את", "או", "אבל", "כי",
    "מה", "לא", "זה", "זו", "יש", "אין", "אני", "הוא", "היא", "הם",
}


def _is_anchored(query: str) -> bool:
    return any(p.strip('"') in query for p in _OCT7_CONTEXT_PHRASES)


def _anchor_query(query: str, phrase_idx: int = 0) -> str:
    """Anchor a free-text query to the Oct 7 / Iron Swords context with ONE quoted
    phrase (DuckDuckGo handles quoted phrases reliably; OR/parentheses groups are not)."""
    if _is_anchored(query):
        return query
    phrase = _OCT7_CONTEXT_PHRASES[phrase_idx % len(_OCT7_CONTEXT_PHRASES)]
    return f"{query} {phrase}"


def _query_terms(query: str) -> list:
    """Extract the meaningful words of the user's original query (for post-filtering),
    ignoring stopwords, boolean operators, and quote characters."""
    words = [w.strip('"().,?!') for w in query.split()]
    return [w for w in words if len(w) > 1 and w.upper() not in ("OR", "AND") and w not in _HEBREW_STOPWORDS]


def _mentions_terms(item: dict, terms: list) -> bool:
    if not terms:
        return True
    text = item.get("title", "") + " " + item.get("snippet", "")
    return any(t in text for t in terms)


def _filter_by_query(items: list, terms: list) -> list:
    """Keep only results that actually mention the user's search terms — otherwise
    a generic word like "ציצית" can return Oct-7-related pages that never say it."""
    filtered = [it for it in items if _mentions_terms(it, terms)]
    return filtered if filtered else items


def _search_ddg_social(query: str, max_results: int) -> list:
    terms = _query_terms(query)
    query = _anchor_query(query)
    all_results = []
    per_site = max(2, (max_results // len(SOCIAL_MEDIA_SITES)) + 1)
    for site, source_type in SOCIAL_MEDIA_SITES:
        try:
            for r in _ddg_text(f"site:{site} {query}", per_site):
                all_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source_url": r.get("href", ""),
                    "source_type": source_type,
                })
        except Exception:
            continue
    return _filter_by_query(all_results, terms)[:max_results]


def _search_ddg_testimony_archives(query: str, max_results: int) -> list:
    terms = _query_terms(query)
    query = _anchor_query(query)
    all_results = []
    per_site = max(3, (max_results // len(TESTIMONY_ARCHIVE_SITES)) + 1)
    for site, source_type in TESTIMONY_ARCHIVE_SITES:
        try:
            for r in _ddg_text(f"site:{site} {query}", per_site):
                all_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source_url": r.get("href", ""),
                    "source_type": source_type,
                })
        except Exception:
            continue
    return _filter_by_query(all_results, terms)[:max_results]


_STORY_TERMS = "עדות OR סיפר OR מספרת OR ניצול OR בריחה"

def _to_items_text(raw: list, url_key: str) -> list:
    return [
        {"title": r.get("title", ""), "snippet": r.get("body", r.get("excerpt", "")),
         "source_url": r.get(url_key, ""), "source_type": "עיתונות"}
        for r in raw if not _is_excluded(r.get(url_key, ""))
    ]

def _to_items_news(raw: list) -> list:
    return [
        {"title": r.get("title", ""), "snippet": r.get("body", r.get("excerpt", "")),
         "source_url": r.get("url", ""), "source_type": "עיתונות"}
        for r in raw if not _is_excluded(r.get("url", ""))
    ]

def _search_ddg(query: str, max_results: int, search_mode: str) -> list:
    if search_mode == "social":
        return _search_ddg_social(query, max_results)
    if search_mode == "testimony":
        return _search_ddg_testimony_archives(query, max_results)

    terms = _query_terms(query)
    already_anchored = _is_anchored(query)
    n_phrases = 1 if already_anchored else len(_OCT7_CONTEXT_PHRASES)

    def _collect(query_variants, to_items_fn, ddg_fn):
        """Run every query variant (deep search — does not stop at the first hit),
        merge & dedupe by URL, then rank: results that mention the user's search
        terms come first, sorted by narrative score so genuine testimonies surface
        above generic mentions of the same word."""
        seen_urls, matched, unmatched = set(), [], []
        for q in query_variants:
            try:
                items = to_items_fn(ddg_fn(q))
            except Exception:
                continue
            for it in items:
                url = it.get("source_url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                (matched if _mentions_terms(it, terms) else unmatched).append(it)
        rank_key = lambda it: narrative_score(it.get("title", "") + " " + it.get("snippet", ""))
        matched.sort(key=rank_key, reverse=True)
        unmatched.sort(key=rank_key, reverse=True)
        return (matched + unmatched) if matched else unmatched

    deep_fetch = max(max_results * 2, 15)

    if search_mode == "news":
        variants = [
            (query if already_anchored else _anchor_query(query, idx))
            for idx in range(n_phrases)
        ]
        results = _collect(
            variants,
            _to_items_news,
            lambda q: _ddg_news(q, deep_fetch),
        )
        return results[:max_results]

    # web/story mode: try every context phrase (+ narrative terms), merge & dedupe,
    # keep results that actually mention the user's search words, post-filter encyclopedias
    variants = []
    for idx in range(n_phrases):
        anchored = query if already_anchored else _anchor_query(query, idx)
        variants.append(f"{anchored} {_STORY_TERMS}")
        variants.append(anchored)
    results = _collect(
        variants,
        lambda raw: _to_items_text(raw, "href"),
        lambda q: _ddg_text(q, deep_fetch),
    )
    if results:
        return results[:max_results]

    anchored = query if already_anchored else _anchor_query(query, 0)
    try:
        return _to_items_news(_ddg_news(anchored, max_results))
    except Exception:
        pass
    return []


def search_web(query: str, max_results: int = 5, search_mode: str = "web") -> list:
    if not SEARCH_API_KEY:
        return _search_ddg(query, max_results, search_mode)
    if SEARCH_ENGINE == "serpapi":
        params = {"q": query, "api_key": SEARCH_API_KEY, "engine": "google", "num": max_results, "hl": "he"}
        if search_mode == "news":
            params["tbm"] = "nws"
        r = requests.get("https://serpapi.com/search", params=params, timeout=15)
        r.raise_for_status()
        return [{"title": i.get("title", ""), "snippet": i.get("snippet", ""), "source_url": i.get("link", i.get("displayed_link", "")), "source_type": "עיתונות"} for i in r.json().get("organic_results", [])]
    raise ValueError(f"מנוע חיפוש לא מוכר: {SEARCH_ENGINE}")


def json_to_dataframe(payload) -> pd.DataFrame:
    if isinstance(payload, dict):
        payload = [payload]
    return pd.json_normalize(payload)


def init_session_state():
    defaults = {
        "history": [],
        "search_results": None,
        "search_analysis": None,
        "selected_search_indices": [],
        "search_source_texts": {},
        "search_source_titles": {},
        "query_draft": "",
        "manual_analysis": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _parse_retry_seconds(msg: str) -> int:
    m = re.search(r"try again in (\d+)m([\d.]+)s", msg)
    if m:
        return int(m.group(1)) * 60 + int(float(m.group(2)))
    m = re.search(r"try again in ([\d.]+)s", msg)
    if m:
        return int(float(m.group(1)))
    return 0


def add_to_history(source: str, text_preview: str, analysis: list):
    st.session_state.history.append({
        "source": source,
        "text_preview": text_preview[:120] + "..." if len(text_preview) > 120 else text_preview,
        "analysis": analysis,
    })


def _render_read_aloud(text: str):
    """Accessibility: read text aloud via the browser's built-in speech synthesis
    (no API key, works offline, supports Hebrew on modern browsers)."""
    if not text:
        return
    import streamlit.components.v1 as components
    payload = json.dumps(text)
    components.html(f"""
    <div style="direction:rtl; font-family:'Segoe UI',Arial,sans-serif; display:flex; gap:8px;">
      <button id="read-btn" style="background:#2563eb;color:white;border:none;border-radius:8px;
        padding:6px 16px;cursor:pointer;font-weight:600;font-size:0.85rem;">🔊 הקרא בקול</button>
      <button id="stop-btn" style="background:#64748b;color:white;border:none;border-radius:8px;
        padding:6px 16px;cursor:pointer;font-weight:600;font-size:0.85rem;">⏹ עצור</button>
    </div>
    <script>
      const _text = {payload};
      document.getElementById('read-btn').onclick = function() {{
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(_text);
        u.lang = 'he-IL';
        u.rate = 0.95;
        window.speechSynthesis.speak(u);
      }};
      document.getElementById('stop-btn').onclick = function() {{
        window.speechSynthesis.cancel();
      }};
    </script>
    """, height=44)


def _render_item_card(item: dict):
    with st.container(border=True):
        st.markdown(f"### {item.get('title', 'ללא כותרת')}")
        c1, c2, c3, c4 = st.columns(4)
        confidence = item.get("confidence", 0)
        reliability = item.get("source_reliability_score", "—")
        with c1:
            st.metric("ז'אנר", item.get("narrative_genre", "—"))
        with c2:
            st.metric("רגש", item.get("sentiment", "—"))
        with c3:
            st.metric("אמינות מקור", f"{reliability}/5" if isinstance(reliability, (int, float)) else "—")
        with c4:
            conf_pct = f"{confidence * 100:.0f}%" if isinstance(confidence, (int, float)) else "—"
            st.metric("ביטחון", conf_pct)

        summary = item.get("narrative_summary", "")
        if summary:
            st.write(summary)
            _render_read_aloud(summary)

        motifs = item.get("motifs", [])
        if isinstance(motifs, list) and motifs:
            st.markdown("**מוטיבים:** " + " · ".join(motifs))

        echoes = item.get("intertextual_echoes", [])
        if isinstance(echoes, list) and echoes:
            st.markdown("**הדהודים בין-טקסטואליים:** " + " · ".join(echoes))

        meta_parts = []
        for label, key in [("שלב אגדה", "legend_stage"), ("רישום", "oral_register"), ("נרטור", "narrator_perspective"), ("מרחק זמני", "temporal_distance")]:
            val = item.get(key, "")
            if val:
                meta_parts.append(f"{label}: {val}")
        if meta_parts:
            st.caption(" | ".join(meta_parts))

        url = item.get("source_url", "")
        if url:
            st.link_button("פתח מקור", url)

        with st.expander("טבלת פרטים מלאה"):
            def _join(val):
                if isinstance(val, list):
                    return ", ".join(str(v) for v in val) if val else "—"
                return str(val) if val else "—"

            dates = item.get("dates_mentioned", {})
            rows = [
                ("📅 תאריך אירוע",    dates.get("event_date", "—") if isinstance(dates, dict) else "—"),
                ("📅 תאריך פרסום",    dates.get("publication_date", "—") if isinstance(dates, dict) else "—"),
                ("⏱ מרחק זמני",       item.get("temporal_distance", "—") or "—"),
                ("📍 מקומות",          _join(item.get("locations", []))),
                ("👤 דמויות וארגונים", _join(item.get("named_entities", []))),
                ("🎭 נרטור",           item.get("narrator_perspective", "—") or "—"),
                ("🎙 רישום אוראלי",    item.get("oral_register", "—") or "—"),
                ("📖 שלב אגדה",        item.get("legend_stage", "—") or "—"),
                ("🏘 קהילת זיכרון",    _join(item.get("memory_community", []))),
                ("🔗 הדהודים",         _join(item.get("intertextual_echoes", []))),
                ("⚙ פונקציות פרופ",    _join(item.get("propp_functions", []))),
                ("🔖 קודי ATU",        _join(item.get("atu_motifs", []))),
                ("🧩 מוטיבים",         _join(item.get("motifs", []))),
                ("🏗 מבנה נרטיבי",     _join(item.get("structure", []))),
            ]
            st.dataframe(
                pd.DataFrame(rows, columns=["קטגוריה", "ערכים"]),
                use_container_width=True,
                hide_index=True,
            )


def render_analysis_results(analysis: list, export_filename: str):
    analysis = [item for item in analysis if isinstance(item, dict)]
    if not analysis:
        st.warning("הניתוח לא החזיר נתונים. נסה טקסט ארוך יותר או מקור שונה.")
        return

    st.subheader("תוצאת הניתוח")
    tab_summary, tab_table, tab_propp = st.tabs(["סיכום קריאה", "טבלה", "מורפולוגיית פרופ"])

    with tab_summary:
        for item in analysis:
            _render_item_card(item)

    with tab_table:
        df = json_to_dataframe(analysis)
        nested_cols = [c for c in df.columns if "." in c or c in (
            "propp_functions", "atu_motifs", "motifs", "structure",
            "locations", "named_entities", "intertextual_echoes", "memory_community"
        )]
        show_cols = [c for c in df.columns if c not in nested_cols]
        rename_map = {
            "title": "כותרת", "source_type": "סוג מקור", "narrative_summary": "תמצית",
            "sentiment": "רגש", "confidence": "ביטחון", "oral_register": "רישום",
            "narrative_genre": "ז'אנר", "narrator_perspective": "נרטור",
            "temporal_distance": "מרחק זמני", "legend_stage": "שלב אגדה",
            "source_reliability_score": "אמינות", "source_url": "מקור",
        }
        st.dataframe(df[show_cols].rename(columns=rename_map), use_container_width=True)

    with tab_propp:
        for item in analysis:
            st.markdown(f"**{item.get('title', 'ללא כותרת')}**")
            found = set(item.get("propp_functions", []))
            cols = st.columns(5)
            for i, (code, he_name) in enumerate(ALL_PROPP_FUNCTIONS):
                with cols[i % 5]:
                    if code in found:
                        st.markdown(f"✅ {he_name}")
                    else:
                        st.markdown(f"<span style='color:#bbb'>⬜ {he_name}</span>", unsafe_allow_html=True)
            atu = item.get("atu_motifs", [])
            if isinstance(atu, list) and atu:
                st.caption("קודי ATU/Thompson: " + ", ".join(atu))
            st.divider()

    col_csv, col_json_dl = st.columns(2)
    with col_csv:
        st.download_button(
            label="הורד כ-CSV",
            data=json_to_dataframe(analysis).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{export_filename}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_json_dl:
        st.download_button(
            label="הורד כ-JSON",
            data=json.dumps(analysis, ensure_ascii=False, indent=2),
            file_name=f"{export_filename}.json",
            mime="application/json",
            use_container_width=True,
        )


def render_history_summary(all_analyses: list):
    all_analyses = [item for item in all_analyses if isinstance(item, dict)]
    if not all_analyses:
        return
    with st.expander("סיכום מצטבר של כל הניתוחים", expanded=True):
        col1, col2, col3 = st.columns(3)
        all_motifs, all_sentiments, all_genres = [], [], []
        for item in all_analyses:
            m = item.get("motifs", [])
            if isinstance(m, list):
                all_motifs.extend(m)
            s = item.get("sentiment", "")
            if s:
                all_sentiments.append(s)
            g = item.get("narrative_genre", "")
            if g:
                all_genres.append(g)
        with col1:
            st.metric("ניתוחים שבוצעו", len(all_analyses))
        if all_sentiments:
            with col2:
                st.metric("רגש שולט", collections.Counter(all_sentiments).most_common(1)[0][0])
        if all_genres:
            with col3:
                st.metric("ז'אנר נפוץ", collections.Counter(all_genres).most_common(1)[0][0])
        if all_motifs:
            top = collections.Counter(all_motifs).most_common(8)
            df_m = pd.DataFrame(top, columns=["מוטיב", "תדירות"]).set_index("מוטיב")
            st.markdown("**מוטיבים נפוצים ביותר**")
            st.bar_chart(df_m)
        if all_sentiments:
            df_s = pd.DataFrame(collections.Counter(all_sentiments).items(), columns=["רגש", "כמות"]).set_index("רגש")
            st.markdown("**התפלגות רגש**")
            st.bar_chart(df_s)


def render_search_tab():
    st.header("סוכן חיפוש וסריקה אוטומטית")

    template = st.selectbox(
        "תבניות חיפוש מוכנות",
        options=list(QUERY_TEMPLATES.keys()),
        index=0,
    )
    if template != "בחר תבנית..." and QUERY_TEMPLATES[template]:
        if st.session_state.get("query_draft") != QUERY_TEMPLATES[template]:
            st.session_state["query_draft"] = QUERY_TEMPLATES[template]
            st.rerun()

    query = st.text_input("מילת חיפוש", key="query_draft")
    col_max, col_mode = st.columns([1, 2])
    with col_max:
        max_results = st.slider("כמות תוצאות", min_value=1, max_value=20, value=7)
    with col_mode:
        search_mode = st.radio(
            "איפה לחפש",
            ["web", "news", "social", "testimony"],
            index=0,
            horizontal=True,
            format_func=lambda c: {
                "web": "🌐 כל הרשת (סיפורים)",
                "news": "📰 אתרי חדשות",
                "social": "📱 רשתות חברתיות",
                "testimony": "🗂 ארכיוני עדויות",
            }[c],
        )

    with st.expander("הוסף מונח לשאילתה מהמילון הפולקלורי", expanded=False):
        st.caption("לחץ על מונח להוספתו לשאילתת החיפוש")
        for category, terms in FOLKLORE_VOCABULARY.items():
            st.markdown(f"**{category}**")
            cols = st.columns(min(len(terms), 6))
            for i, term in enumerate(terms):
                with cols[i % 6]:
                    if st.button(term, key=f"term_{category}_{term}"):
                        current = st.session_state.get("query_draft", "")
                        st.session_state["query_draft"] = (current + " " + term).strip()
                        st.rerun()

    if st.button("הפעל סוכן חיפוש", use_container_width=True, type="primary"):
        if not query.strip():
            st.warning("הזן מילת חיפוש לפני הפעלת הסוכן.")
        else:
            try:
                with st.spinner("מבצע חיפוש ומביא תוצאות..."):
                    results = search_web(query.strip(), max_results=max_results, search_mode=search_mode)
                if not results:
                    st.warning("לא נמצאו תוצאות לשאילתה זו. נסה מילות חיפוש אחרות.")
                else:
                    st.session_state.search_results = results
                    st.session_state.search_analysis = None
                    st.session_state.search_source_texts = {}
                    st.session_state.search_source_titles = {}
                    high_score = [i for i, item in enumerate(results) if narrative_score(item.get("title", "") + " " + item.get("snippet", "")) >= 3]
                    st.session_state.selected_search_indices = high_score if high_score else list(range(len(results)))
                    st.success(f"נמצאו {len(results)} תוצאות.")
            except Exception as exc:
                st.error(f"שגיאה בחיפוש: {exc}")

    st.divider()
    st.subheader("הוספה ישירה ממקור URL")
    direct_url = st.text_input("הדבק כתובת URL של עדות לניתוח ישיר", key="direct_url_input")
    if st.button("שלוף ונתח מה-URL", use_container_width=True):
        if not direct_url.strip():
            st.warning("הכנס כתובת URL.")
        else:
            try:
                with st.spinner("שולף תוכן מהמקור ומריץ ניתוח..."):
                    source_text = fetch_source_page_text(direct_url.strip())
                    if not source_text:
                        st.error("לא ניתן לשלוף תוכן מהכתובת הזו.")
                    else:
                        analysis = analyze_text(source_text, source_url=direct_url.strip(), source_type="עדות_ראשונית")
                        add_to_history("URL ישיר: " + direct_url, source_text, analysis)
                        st.session_state.search_analysis = analysis
                        st.success("הניתוח הושלם.")
            except Exception as exc:
                st.error(f"שגיאה: {exc}")

    if st.session_state.search_results:
        st.divider()
        scored = []
        for item in st.session_state.search_results:
            score = narrative_score(item.get("title", "") + " " + item.get("snippet", ""))
            domain = re.sub(r"https?://(www\.)?", "", item.get("source_url", "")).split("/")[0]
            scored.append({**item, "ציון_נרטיב": score, "דומיין": domain})
        scored.sort(key=lambda x: x["ציון_נרטיב"], reverse=True)

        max_score = scored[0]["ציון_נרטיב"] if scored else 0
        col_filter, col_info = st.columns([2, 1])
        with col_filter:
            min_score = st.slider(
                "הצג רק תוצאות עם ציון נרטיב מינימלי",
                min_value=0,
                max_value=max(max_score, 1),
                value=0,
                help="הזזה ימינה מסננת תוצאות פחות נרטיביות",
            )
        with col_info:
            visible = sum(1 for r in scored if r["ציון_נרטיב"] >= min_score)
            st.metric("תוצאות מוצגות", f"{visible}/{len(scored)}")

        visible_scored = [r for r in scored if r["ציון_נרטיב"] >= min_score]

        st.markdown("### תוצאות החיפוש")
        if not visible_scored:
            st.info("אין תוצאות מעל הציון המינימלי. הזז את המחוון שמאלה.")
        for i, row in enumerate(visible_scored):
            score = row.get("ציון_נרטיב", 0)
            stars = "★" * min(score, 5) + "☆" * max(0, 5 - score)
            label = "גבוהה" if score >= 5 else ("בינונית" if score >= 2 else "נמוכה")
            # Highlight top-scoring results
            prefix = "🏆 " if score == max_score and max_score > 0 else ""
            with st.expander(f"{prefix}{stars}  {i + 1}. {row.get('title', '')}  |  רלוונטיות: {label}"):
                st.write(row.get("snippet", ""))
                st.caption(f"מקור: {row.get('דומיין', '')} | סוג: {row.get('source_type', '')} | ציון: {score}")
                if row.get("source_url"):
                    st.link_button("פתח מקור", row["source_url"])

        # Auto-select top-scored from visible results for analysis
        if visible_scored:
            top_score = visible_scored[0]["ציון_נרטיב"]
            auto_selected = [
                st.session_state.search_results.index(r)
                for r in visible_scored
                if r["ציון_נרטיב"] >= max(top_score, 3)
                and r in st.session_state.search_results
            ]
            if auto_selected and st.session_state.selected_search_indices != auto_selected:
                st.session_state.selected_search_indices = auto_selected

        st.divider()
        st.subheader("שלב 2: בחר מקורות לניתוח פולקלוריסטי")
        result_labels = [f"{i + 1}. {item.get('title', '')}" for i, item in enumerate(st.session_state.search_results)]
        selected_indices = st.multiselect(
            "בחר תוצאות לניתוח",
            options=list(range(len(result_labels))),
            format_func=lambda idx: result_labels[idx],
            default=st.session_state.selected_search_indices,
            key="selected_search_indices",
        )

        if st.button("נתח מקורות נבחרים", use_container_width=True, type="primary"):
            if not selected_indices:
                st.warning("בחר לפחות תוצאה אחת לניתוח.")
            else:
                chosen_items = [st.session_state.search_results[i] for i in selected_indices]
                all_results = []
                progress_bar = st.progress(0, text="מתחיל ניתוח...")
                for step, item in enumerate(chosen_items):
                    item_title = item.get("title", "")
                    progress_bar.progress(step / len(chosen_items), text=f"מנתח {step + 1}/{len(chosen_items)}: {item_title[:45]}...")
                    url = item.get("source_url", "")
                    snippet = item.get("snippet", "")
                    source_text = fetch_source_page_text(url) if url else snippet
                    if not source_text:
                        source_text = snippet
                    st.session_state.search_source_texts[url] = source_text
                    st.session_state.search_source_titles[url] = item_title
                    text_for_analysis = f"{item_title}\n{snippet}\n{source_text[:1200]}"
                    try:
                        results = analyze_text(
                            text_for_analysis,
                            title=item_title,
                            source_url=url,
                            source_type=item.get("source_type", "עיתונות"),
                        )
                        all_results.extend(results)
                    except Exception as item_exc:
                        msg = str(item_exc)
                        if "429" in msg or "Rate limit" in msg:
                            wait = _parse_retry_seconds(msg)
                            wait_str = f"{wait // 60} דקות ו-{wait % 60} שניות" if wait >= 60 else f"{wait} שניות"
                            st.error(f"הגעת לגבול הטוקנים היומי של Groq. המתן {wait_str} ונסה שוב." if wait else "הגעת לגבול Groq. נסה שוב מאוחר יותר.")
                            break
                        elif "413" in msg or "too large" in msg.lower():
                            st.warning(f"מקור '{item_title[:40]}' ארוך מדי — דולג.")
                        else:
                            st.warning(f"שגיאה במקור '{item_title[:40]}': {item_exc}")
                    else:
                        if step < len(chosen_items) - 1:
                            time.sleep(1.5)
                progress_bar.progress(1.0, text="הניתוח הושלם!")
                if all_results:
                    st.session_state.search_analysis = all_results
                    add_to_history("חיפוש: " + query, "; ".join(i.get("title", "") for i in chosen_items), all_results)
                    st.success(f"נותחו {len(all_results)} מקורות בהצלחה.")
                else:
                    st.error("לא ניתן לנתח אף מקור. נסה שאילתה אחרת.")

        if st.session_state.search_source_texts:
            st.subheader("סיכומי מקורות שנבדקו")
            for url, text_preview in st.session_state.search_source_texts.items():
                if not url:
                    continue
                title_label = st.session_state.search_source_titles.get(url, url)
                with st.expander(f"מקור: {title_label}"):
                    st.write(text_preview[:3000] if text_preview else "לא ניתן למשוך תוכן מהמקור.")

    if st.session_state.search_analysis:
        render_analysis_results(
            st.session_state.search_analysis,
            "חיפוש_" + query.replace(" ", "_"),
        )


def render_manual_tab():
    st.header("הזנה וניתוח ידני")
    title = st.text_input("כותרת מקור (אופציונלי)", value="")
    source_url = st.text_input("כתובת מקור (URL, אופציונלי)", value="")
    source_type = st.selectbox(
        "סוג מקור",
        ["אחר", "עדות_ראשונית", "עיתונות", "רשת_חברתית", "פרסום_רשמי", "שמועה", "מיתוס_עירוני"],
        index=0,
    )
    text = st.text_area(
        "הדבק כאן טקסט עברי חופשי לניתוח פולקלוריסטי",
        height=280,
        placeholder="הדבק כאן עדות, כתבה, פוסט ברשת חברתית, קטע מספר, או כל טקסט אחר...",
    )
    if st.button("נתח טקסט", use_container_width=True, type="primary"):
        if not text.strip():
            st.warning("הזן טקסט בעברית לפני ניתוח.")
            return
        try:
            with st.spinner("מריץ את המודל ומייצר JSON מובנה..."):
                analysis = analyze_text(text, title=title or None, source_url=source_url or None, source_type=source_type)
            add_to_history("הזנה ידנית", text, analysis)
            st.session_state.manual_analysis = analysis
            st.success("הניתוח הושלם — ראה תוצאות למטה.")
        except Exception as exc:
            st.error(f"שגיאה: {exc}")

    if st.session_state.get("manual_analysis"):
        render_analysis_results(st.session_state.manual_analysis, "ניתוח_ידני")


def render_history_tab():
    st.header("היסטוריית ניתוחים")
    if not st.session_state.history:
        st.info("עדיין לא בוצעו ניתוחים בפגישה זו.")
        return

    all_analyses = [a for entry in st.session_state.history for a in entry["analysis"]]
    render_history_summary(all_analyses)

    st.divider()
    for i, entry in enumerate(reversed(st.session_state.history)):
        idx = len(st.session_state.history) - i
        with st.expander(f"#{idx} — {entry['source']}"):
            st.caption(entry["text_preview"])
            render_analysis_results(entry["analysis"], f"ניתוח_{idx}")

    st.divider()
    st.subheader("ייצוא כל הניתוחים")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="הורד הכל כ-CSV",
            data=json_to_dataframe(all_analyses).to_csv(index=False).encode("utf-8-sig"),
            file_name="כל_הניתוחים.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            label="הורד הכל כ-JSON",
            data=json.dumps(all_analyses, ensure_ascii=False, indent=2),
            file_name="כל_הניתוחים.json",
            mime="application/json",
            use_container_width=True,
        )
    st.divider()
    if st.button("נקה היסטוריה", type="secondary"):
        st.session_state.history = []
        st.session_state.search_results = None
        st.session_state.search_analysis = None
        st.session_state.manual_analysis = None
        st.rerun()


def _inject_css():
    st.markdown("""
    <style>
    /* ── RTL global ── */
    .stApp { direction: rtl; }

    /* Headings — Streamlit wraps them in these containers */
    h1, h2, h3, h4, h5, h6 {
        text-align: right !important;
        direction: rtl !important;
    }
    [data-testid="stHeadingWithActionElements"],
    [data-testid="stHeading"] {
        text-align: right !important;
        direction: rtl !important;
    }
    .stMarkdown p, .stMarkdown li,
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        text-align: right !important;
        direction: rtl !important;
    }
    [data-testid="stMarkdownContainer"] {
        text-align: right !important;
        direction: rtl !important;
    }

    .stTextInput input, .stTextArea textarea { direction: rtl; text-align: right; }
    [data-baseweb="select"] * { direction: rtl; }
    [data-baseweb="menu"] { direction: rtl; text-align: right; }
    .stRadio > div { direction: rtl; }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] { text-align: right; direction: rtl; }
    .stCheckbox > label { direction: rtl; }
    .stMultiSelect [data-baseweb="tag"] { direction: rtl; }
    label { direction: rtl; text-align: right; }

    /* keep URLs left-to-right */
    a { direction: ltr; unicode-bidi: embed; }

    /* ── Typography ── */
    html, body, .stApp {
        font-family: 'Segoe UI', 'Arial Hebrew', Arial, sans-serif;
    }

    /* ── Page header ── */
    h1 {
        color: #1b2a4a;
        font-size: 1.85rem;
        border-bottom: 3px solid #3b82f6;
        padding-bottom: 0.35rem;
        margin-bottom: 0.15rem;
    }
    h2 { color: #1e3a5f; }
    h3 { color: #2c4f7c; font-size: 1.05rem; }

    /* ── Top navigation tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background: #f0f4fa;
        border-radius: 10px;
        padding: 4px;
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        font-weight: 600;
        color: #4a5568;
        padding: 6px 18px;
    }
    .stTabs [aria-selected="true"] {
        background: white;
        color: #2563eb;
        box-shadow: 0 1px 4px rgba(0,0,0,0.12);
    }

    /* ── Analysis cards ── */
    [data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid #dbe8f8 !important;
        border-radius: 14px !important;
        background: #fafcff;
        transition: box-shadow 0.2s;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:hover {
        box-shadow: 0 4px 18px rgba(59,130,246,0.12);
    }

    /* ── Metric boxes ── */
    [data-testid="metric-container"] {
        background: #f0f6ff;
        border-radius: 10px;
        padding: 6px 10px;
    }

    /* ── Primary button ── */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2563eb, #3b82f6);
        border: none;
        border-radius: 8px;
        font-weight: 700;
        font-size: 1rem;
        transition: all 0.18s;
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #1d4ed8, #2563eb);
        box-shadow: 0 4px 14px rgba(37,99,235,0.32);
        transform: translateY(-1px);
    }
    .stButton > button[kind="secondary"] {
        border-radius: 8px;
        font-weight: 600;
    }

    /* ── Expander ── */
    [data-testid="stExpander"] summary {
        font-weight: 600;
        color: #1e3a5f;
        direction: rtl;
    }

    /* ── Progress bar ── */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #3b82f6, #10b981);
    }

    /* ── Caption ── */
    .stCaptionContainer p { color: #64748b; font-size: 0.82rem; }

    /* ── Divider ── */
    hr { border-color: #e2e8f0; margin: 1rem 0; }

    /* ── Slider — keep LTR to prevent value overflowing screen ── */
    [data-testid="stSlider"] { direction: ltr; }
    [data-testid="stSlider"] [data-baseweb="slider"] { direction: ltr; }
    [data-testid="stSliderThumbValue"] { direction: ltr; }

    /* ── Accessibility: visible keyboard-focus outline (Israeli accessibility regs) ── */
    *:focus-visible {
        outline: 3px solid #2563eb !important;
        outline-offset: 2px;
    }
    </style>
    """, unsafe_allow_html=True)


def _render_accessibility_statement():
    with st.expander("♿ הצהרת נגישות"):
        st.markdown(
            "אתר זה פועל למתן שירות נגיש ושוויוני לכלל הגולשים, בהתאם לתקנות שוויון "
            "זכויות לאנשים עם מוגבלות (התאמות נגישות לשירות), תשע\"ג-2013, ועקרונות "
            "התקן הישראלי 5568 ברמת התאמה AA (המבוסס על WCAG 2.0).\n\n"
            "**התאמות הנגישות הקיימות באתר:**\n"
            "- ממשק מלא בכיווניות עברית (RTL) עם מבנה כותרות היררכי לניווט בעזרת קוראי מסך\n"
            "- ניגודיות צבעים גבוהה בין טקסט לרקע\n"
            "- מתאר מיקוד (focus) ברור לניווט מקלדת בכל הרכיבים\n"
            "- כפתור הקראה קולית (Text-to-Speech) לתקצירי ניתוח, מבוסס על מנוע ההקראה המובנה בדפדפן\n\n"
            "**נתקלת בבעיית נגישות?** ניתן לפנות למפעיל האתר בכתובת המייל המופיעה בפרטי הקשר של הפרויקט, "
            "ואנו נפעל לתיקון בהקדם האפשרי."
        )


def _render_legal_disclaimer():
    with st.expander("⚖️ הצהרה משפטית ושימוש הוגן"):
        st.markdown(
            "**מטרת הכלי:** כלי זה משמש למחקר אקדמי בתחום הפולקלור והנרטיבים העממיים "
            "מה-7 באוקטובר 2023, בהתאם לעקרון \"שימוש הוגן\" הקבוע בסעיף 19 לחוק זכות "
            "יוצרים, התשס\"ח-2007, המתיר שימוש ביצירות מוגנות למטרות מחקר, ביקורת, "
            "סקירה והוראה.\n\n"
            "**עקרונות הפעולה:**\n"
            "- הכלי מציג קטעי טקסט מצוטטים לצורכי ניתוח וקישור למקור המקורי בלבד — אינו מעתיק או מפיץ יצירות במלואן\n"
            "- כל תוצאה כוללת קישור ישיר למקור (\"פתח מקור\") לעיון בהקשרו המלא\n"
            "- הכלי אינו טוען לבעלות על תוכן צד שלישי המוצג בו, ואינו מהווה תחליף לעיון במקור\n\n"
            "**אחריות המשתמש:** על המשתמש לוודא כי כל שימוש נוסף בתכנים המוצגים (פרסום, ציטוט "
            "במחקר, הפצה) נעשה בהתאם לדין, לכללי האזכור האקדמי המקובלים ותוך מתן קרדיט למקור המקורי."
        )


def main():
    st.set_page_config(
        page_title="מחקר פולקלור — 7 באוקטובר 2023",
        layout="wide",
        page_icon="📖",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    init_session_state()

    st.title("כלי מחקר דיגיטלי לפולקלור ומלחמת חרבות ברזל")
    st.markdown("כלי לאיתור, ניתוח וסיווג נרטיבים עממיים מה-7 באוקטובר 2023 — מוטיבים, מבנה פרופ, ז'אנר ועוד.")
    st.caption(f"סשן נוכחי: {len(st.session_state.history)} ניתוחים | מודל: {GROQ_MODEL}")

    tabs = st.tabs([
        "חיפוש אוטומטי",
        "הזנה ידנית",
        f"היסטוריה ({len(st.session_state.history)})",
    ])
    with tabs[0]:
        render_search_tab()
    with tabs[1]:
        render_manual_tab()
    with tabs[2]:
        render_history_tab()

    st.divider()
    col_a11y, col_legal = st.columns(2)
    with col_a11y:
        _render_accessibility_statement()
    with col_legal:
        _render_legal_disclaimer()


if __name__ == "__main__":
    main()

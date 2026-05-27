import os
import re
import json
import streamlit as st
import pandas as pd
import requests
from groq import Groq
from dotenv import load_dotenv
from ddgs import DDGS
from bs4 import BeautifulSoup

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "serpapi")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def build_system_prompt() -> str:
    vocab_lines = "\n".join(
        f"  {cat}: {', '.join(terms)}"
        for cat, terms in FOLKLORE_VOCABULARY.items()
    )
    return (
        "אתה מומחה לחקר פולקלור, ספרות עממית ומדעי הרוח הדיגיטליים, המתמחה באירועי 7 באוקטובר 2023 ומלחמת חרבות ברזל. "
        "המשימה שלך היא לנתח טקסט עברי חופשי או תמצית מקורות רשת, ולייצא את התוצאה בלבד בפורמט JSON תקין. "
        "אל תכלול הסברים, הערות, או טקסט חופשי נוסף מעבר ל-JSON בלבד. "
        "ה-json יכול להיות אובייקט בודד או רשימה של אובייקטים. "
        "\n\nאוצר מילים דומיין-ספציפי שעליך להכיר ולזהות בטקסטים:\n"
        + vocab_lines +
        "\n\nכל אובייקט חייב להכיל את השדות הבאים: title, source_type, source_url, narrative_summary, motifs, structure, dates_mentioned, locations, named_entities, sentiment, confidence. "
        "source_type צריך להיות אחד מהערכים הבאים: עדות_ראשונית, עיתונות, רשת_חברתית, פרסום_רשמי, שמועה, מיתוס_עירוני, אחר. "
        "source_url חייב להיות כתובת URL מלאה או מחרוזת ריקה אם לא ידוע. "
        "narrative_summary חייב להיות תמצית קצרה בעברית של התוכן. "
        "motifs ו-structure חייבים להיות רשימות של מחרוזות קצרות — השתמש במונחים מאוצר המילים לעיל כשרלוונטי. "
        "dates_mentioned חייב להיות אובייקט עם event_date ו-publication_date בפורמט ISO 8601 (YYYY-MM-DD) או מחרוזת ריקה. "
        "locations היא רשימה של שמות מקום — כולל קיבוצים ויישובי עוטף עזה כשרלוונטי. "
        "named_entities היא רשימה של שמות אנשים, ארגונים או גופים. "
        "sentiment הוא רגש דומיננטי: אבל, כעס, גבורה, פחד, תקווה, אמביוולנטי. "
        "confidence הוא מספר בין 0 ל-1. "
        "אם אין מידע מסוים, השתמש בערך ריק או ברשימה ריקה, אך שמור על JSON תקין. "
    )


def build_user_prompt(text: str, title: str | None = None, source_url: str | None = None, source_type: str | None = None) -> str:
    prompt_parts = []
    if title:
        prompt_parts.append(f"כותרת מקור: {title}")
    if source_type:
        prompt_parts.append(f"סוג מקור: {source_type}")
    if source_url:
        prompt_parts.append(f"כתובת מקור: {source_url}")
    prompt_parts.append("טקסט לניתוח:")
    prompt_parts.append(text)
    prompt_parts.append(
        "השב רק JSON תקין בלבד, ללא הסברים, ללא טקסט נוסף וללא סימנים אחרים לפני/אחרי ה-JSON."
    )
    return "\n".join(prompt_parts)


def analyze_text(text: str, title: str | None = None, source_url: str | None = None, source_type: str | None = None) -> list:
    if not client:
        raise RuntimeError("חסר GROQ_API_KEY. הוסף אותו לקובץ .env (חינמי בgroq.com).")

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(text, title=title, source_url=source_url, source_type=source_type)},
        ],
    )
    content = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = json.loads(_clean_json_response(content))

    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def _clean_json_response(raw: str) -> str:
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = raw.find(start_char)
        end = raw.rfind(end_char) + 1
        if start != -1 and end > 0:
            return raw[start:end]
    raise ValueError("לא נמצא JSON תקין בתשובת המודל")


def fetch_source_page_text(source_url: str, timeout: int = 8) -> str:
    if not source_url:
        return ""
    try:
        r = requests.get(
            source_url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "he,en;q=0.9",
            },
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
            tag.decompose()

        main_content = (
            soup.find("article")
            or soup.find("main")
            or soup.find(id=re.compile(r"content|article|main", re.I))
            or soup.find(class_=re.compile(r"content|article|main|post|entry", re.I))
            or soup.find("body")
            or soup
        )

        lines = []
        for element in main_content.find_all(["p", "h1", "h2", "h3", "li", "blockquote"]):
            text = element.get_text(separator=" ", strip=True)
            if len(text) > 25:
                lines.append(text)

        result = "\n".join(lines)
        if len(result) < 200:
            result = main_content.get_text(separator="\n", strip=True)
            result = re.sub(r"\n{3,}", "\n\n", result)

        return result[:4000].strip()
    except Exception:
        return ""


def narrative_score(text: str) -> int:
    markers = [
        "אני ", "אנחנו ", "הייתי ", "היינו ", "ראיתי ", "שמעתי ", "הרגשתי ",
        "ברחתי ", "הסתתרתי ", "ניצלתי ", "חבאתי ", "לקחתי ",
        "עדות", "סיפור", "הצלה", "בריחה", 'ז"ל', 'הי"ד', "נרצח", "ספר",
        "נרצחה", "חטוף", "חטופה", "ניצול", "ניצולה", "מסתור",
        "פיצוץ", "ירייה", "יריות", "רימון", "מחבלים", "טרור",
        "שמחת תורה", "פסטיבל", "נובה", "7 באוקטובר", "שביעי באוקטובר",
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


def _ddg_text(query: str, max_results: int) -> list[dict]:
    with DDGS() as ddgs:
        results = list(ddgs.text(query, region="wt-wt", max_results=max_results))
    return results


def _ddg_news(query: str, max_results: int) -> list[dict]:
    with DDGS() as ddgs:
        results = list(ddgs.news(query, region="wt-wt", max_results=max_results))
    return results


def _search_ddg_social(query: str, max_results: int) -> list[dict]:
    all_results = []
    per_site = max(2, (max_results // len(SOCIAL_MEDIA_SITES)) + 1)
    for site, source_type in SOCIAL_MEDIA_SITES:
        try:
            raw = _ddg_text(f"site:{site} {query}", per_site)
            for r in raw:
                all_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source_url": r.get("href", ""),
                    "source_type": source_type,
                })
        except Exception:
            continue
    return all_results[:max_results]


def _search_ddg(query: str, max_results: int, search_mode: str) -> list[dict]:
    if search_mode == "social":
        return _search_ddg_social(query, max_results)

    if search_mode == "news":
        try:
            raw = _ddg_news(query, max_results)
            if raw:
                return [{"title": r.get("title",""), "snippet": r.get("body",""), "source_url": r.get("url",""), "source_type": "עיתונות"} for r in raw]
        except Exception:
            pass
        return []

    # web: try text, fallback to news
    try:
        raw = _ddg_text(query, max_results)
        if raw:
            return [{"title": r.get("title",""), "snippet": r.get("body",""), "source_url": r.get("href",""), "source_type": "עיתונות"} for r in raw]
    except Exception:
        pass
    # fallback: news
    try:
        raw = _ddg_news(query, max_results)
        if raw:
            return [{"title": r.get("title",""), "snippet": r.get("body",""), "source_url": r.get("url",""), "source_type": "עיתונות"} for r in raw]
    except Exception:
        pass
    return []


def search_web(query: str, max_results: int = 5, search_mode: str = "web") -> list[dict]:
    if not SEARCH_API_KEY:
        return _search_ddg(query, max_results, search_mode)

    if SEARCH_ENGINE == "serpapi":
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": SEARCH_API_KEY,
            "engine": "google",
            "num": max_results,
            "hl": "he",
        }
        if search_mode == "news":
            params["tbm"] = "nws"
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "source_url": item.get("link", item.get("displayed_link", "")),
                "source_type": "עיתונות",
            }
            for item in data.get("organic_results", [])
        ]

    elif SEARCH_ENGINE == "bing":
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": SEARCH_API_KEY}
        params = {"q": query, "count": max_results, "mkt": "he-IL"}
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [
            {
                "title": item.get("name", ""),
                "snippet": item.get("snippet", ""),
                "source_url": item.get("url", ""),
                "source_type": "עיתונות",
            }
            for item in data.get("webPages", {}).get("value", [])
        ]

    raise ValueError(f"מנוע חיפוש לא מוכר: {SEARCH_ENGINE}")


def json_to_dataframe(payload) -> pd.DataFrame:
    if isinstance(payload, dict):
        payload = [payload]
    if isinstance(payload, list):
        return pd.json_normalize(payload)
    raise ValueError("Payload אינו רשימה או מילון.")


def init_session_state():
    if "history" not in st.session_state:
        st.session_state.history = []
    if "search_results" not in st.session_state:
        st.session_state.search_results = None
    if "search_analysis" not in st.session_state:
        st.session_state.search_analysis = None
    if "selected_search_indices" not in st.session_state:
        st.session_state.selected_search_indices = []
    if "search_source_texts" not in st.session_state:
        st.session_state.search_source_texts = {}
    if "query_draft" not in st.session_state:
        st.session_state.query_draft = ""


def add_to_history(source: str, text_preview: str, analysis: list):
    st.session_state.history.append({
        "source": source,
        "text_preview": text_preview[:120] + "..." if len(text_preview) > 120 else text_preview,
        "analysis": analysis,
    })


def render_analysis_results(analysis: list, export_filename: str):
    st.subheader("תוצאת הניתוח")

    tab_table, tab_motifs, tab_json = st.tabs(["טבלה", "מוטיבים ומבנה", "JSON גולמי"])
    with tab_table:
        df = json_to_dataframe(analysis)
        st.dataframe(df, use_container_width=True)
    with tab_motifs:
        motif_rows = []
        for item in analysis:
            motif_rows.append({
                "כותרת": item.get("title", ""),
                "מוטיבים": ", ".join(item.get("motifs", [])) if isinstance(item.get("motifs"), list) else item.get("motifs", ""),
                "מבנה": ", ".join(item.get("structure", [])) if isinstance(item.get("structure"), list) else item.get("structure", ""),
                "מקומות": ", ".join(item.get("locations", [])) if isinstance(item.get("locations"), list) else item.get("locations", ""),
                "ישויות": ", ".join(item.get("named_entities", [])) if isinstance(item.get("named_entities"), list) else item.get("named_entities", ""),
                "רגש": item.get("sentiment", ""),
                "ביטחון": item.get("confidence", ""),
            })
        st.dataframe(pd.DataFrame(motif_rows), use_container_width=True)
    with tab_json:
        st.code(json.dumps(analysis, ensure_ascii=False, indent=2), language="json")

    col_csv, col_json_dl = st.columns(2)
    with col_csv:
        df_export = json_to_dataframe(analysis)
        st.download_button(
            label="הורד כ-CSV",
            data=df_export.to_csv(index=False, encoding="utf-8-sig"),
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


def render_search_tab():
    st.header("סוכן חיפוש וסריקה אוטומטית")
    st.write("הזן מילת חיפוש או לחץ על מונח מהרשימה להוספתו לשאילתה.")

    if "query_draft" not in st.session_state:
        st.session_state["query_draft"] = ""

    with st.expander("מילון מונחים — לחץ להוספה לשאילתה", expanded=False):
        for category, terms in FOLKLORE_VOCABULARY.items():
            st.markdown(f"**{category}**")
            cols = st.columns(min(len(terms), 6))
            for i, term in enumerate(terms):
                with cols[i % 6]:
                    if st.button(term, key=f"term_{category}_{term}"):
                        current = st.session_state.get("query_draft", "")
                        st.session_state["query_draft"] = (current + " " + term).strip()
                        st.rerun()

    query = st.text_input("מילת חיפוש לעיון ברשת", key="query_draft")
    max_results = st.slider("כמות תוצאות לחיפוש", min_value=1, max_value=10, value=5)

    search_mode = st.selectbox(
        "סוג חיפוש",
        ["web", "news", "social"],
        index=0,
        format_func=lambda c: {"web": "כללי", "news": "חדשות", "social": "רשתות חברתיות"}[c],
    )

    if st.button("הפעל סוכן חיפוש", use_container_width=True):
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
                    st.session_state.selected_search_indices = list(range(len(results)))
                    st.success(f"נמצאו {len(results)} תוצאות.")
            except Exception as exc:
                st.error(f"שגיאה בחיפוש: {exc}")

    if st.session_state.search_results:
        scored = []
        for item in st.session_state.search_results:
            score = narrative_score(item.get("title", "") + " " + item.get("snippet", ""))
            scored.append({**item, "ציון_נרטיב": score})
        df_all = pd.DataFrame(scored)
        display_cols = ["title", "snippet", "source_url", "source_type", "ציון_נרטיב"]
        df_display = df_all[[c for c in display_cols if c in df_all.columns]]
        st.dataframe(df_display, use_container_width=True)

        result_labels = [f"{i+1}. {item.get('title', '')}" for i, item in enumerate(st.session_state.search_results)]
        selected_indices = st.multiselect(
            "בחר תוצאות לניתוח",
            options=list(range(len(result_labels))),
            format_func=lambda index: result_labels[index],
            default=st.session_state.selected_search_indices,
            key="selected_search_indices",
        )

        if st.button("אמת מקורות ונתח", use_container_width=True):
            if not selected_indices:
                st.warning("בחר לפחות תוצאה אחת לניתוח.")
            else:
                chosen_items = [st.session_state.search_results[i] for i in selected_indices]
                try:
                    with st.spinner(f"מביא תוכן מ-{len(chosen_items)} מקורות ומריץ ניתוח פולקלוריסטי..."):
                        preview_texts = []
                        for item in chosen_items:
                            url = item.get("source_url", "")
                            snippet = item.get("snippet", "")
                            if url:
                                source_text = fetch_source_page_text(url)
                                if not source_text:
                                    source_text = snippet
                            else:
                                source_text = snippet
                            st.session_state.search_source_texts[url] = source_text
                            preview_texts.append(
                                f"{item.get('title', '')}\n{snippet}\n{url}\n{source_text[:2000]}"
                            )
                        combined_text = "\n\n---\n\n".join(preview_texts)
                        st.session_state.search_analysis = analyze_text(
                            combined_text,
                            title=query,
                            source_url=chosen_items[0].get("source_url", ""),
                            source_type=chosen_items[0].get("source_type", "עיתונות"),
                        )
                    add_to_history("חיפוש: " + query, combined_text, st.session_state.search_analysis)
                except Exception as exc:
                    st.error(f"שגיאה בניתוח: {exc}")

        if st.session_state.search_source_texts:
            st.subheader("סיכומי מקורות שנבדקו")
            for url, text_preview in st.session_state.search_source_texts.items():
                if not url:
                    continue
                with st.expander(url):
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
    if st.button("נתח טקסט", use_container_width=True):
        if not text.strip():
            st.warning("הזן טקסט בעברית לפני ניתוח.")
            return
        try:
            with st.spinner("מריץ את המודל ומייצר JSON מובנה..."):
                analysis = analyze_text(
                    text,
                    title=title or None,
                    source_url=source_url or None,
                    source_type=source_type,
                )
            add_to_history("הזנה ידנית", text, analysis)
            render_analysis_results(analysis, "ניתוח_ידני")
        except Exception as exc:
            st.error(f"שגיאה: {exc}")


def render_history_tab():
    st.header("היסטוריית ניתוחים")
    if not st.session_state.history:
        st.info("עדיין לא בוצעו ניתוחים בפגישה זו.")
        return

    all_analyses = []
    for i, entry in enumerate(reversed(st.session_state.history)):
        idx = len(st.session_state.history) - i
        with st.expander(f"#{idx} — {entry['source']}"):
            st.caption(entry["text_preview"])
            render_analysis_results(entry["analysis"], f"ניתוח_{idx}")
        all_analyses.extend(entry["analysis"])

    st.divider()
    st.subheader("ייצוא כל הניתוחים")
    col1, col2 = st.columns(2)
    with col1:
        df_all = json_to_dataframe(all_analyses)
        st.download_button(
            label="הורד הכל כ-CSV",
            data=df_all.to_csv(index=False, encoding="utf-8-sig"),
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
        st.rerun()


def main():
    st.set_page_config(
        page_title="כלי מחקר DH — פולקלור ו-7 באוקטובר",
        layout="wide",
        page_icon="📖",
    )
    init_session_state()

    st.title("כלי מחקר דיגיטלי לפולקלור ומלחמת חרבות ברזל")
    st.markdown(
        "כלי לבדיקת נרטיבים מה-7 באוקטובר, איתור מקורות ברשת וניתוח מבני של סיפור, מוטיבים ותאריכים."
    )

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

    with st.sidebar:
        st.header("קונפיגורציה")
        st.markdown("**סטטוס מפתחות API:**")
        for key, value in [("GROQ_API_KEY", GROQ_API_KEY), ("SEARCH_API_KEY", SEARCH_API_KEY)]:
            icon = "✅" if value else "❌"
            st.markdown(f"{icon} `{key}`")
        st.divider()
        st.markdown(f"**מודל:** `{GROQ_MODEL}`")
        active_engine = SEARCH_ENGINE if SEARCH_API_KEY else "duckduckgo (חינמי)"
        st.markdown(f"**מנוע חיפוש:** `{active_engine}`")
        st.divider()
        st.markdown("הגדר מפתחות ב-`.env` לפי `.env.example`.")


if __name__ == "__main__":
    main()

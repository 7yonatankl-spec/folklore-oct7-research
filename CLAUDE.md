# פרויקט מחקר פולקלור — 7 באוקטובר

כלי דיגיטלי לחקירת נרטיבים עממיים ממלחמת חרבות ברזל.

## Skill Team

Three skills work together as a research pipeline:

| Skill | Invoke | Role |
|-------|--------|------|
| `/folklore-search` | When finding new source material | Searches Hebrew web, news, social media for Oct 7 testimonies. Scores by narrative relevance. Passes findings to narrative-analyst. |
| `/narrative-analyst` | When analyzing a text | Academic folklore analysis: Propp morphology, motif identification, sentiment, intertextual echoes. Outputs JSON for app + Hebrew research note. Flags missing features to app-developer. |
| `/app-developer` | When improving the tool | Implements features, fixes bugs. Receives DEV NOTEs from narrative-analyst. Knows the full app architecture. |

## Pipeline Flow

```
/folklore-search  -->  finds sources
       |
       v
/narrative-analyst  -->  analyzes + flags DEV NOTEs
       |
       v
/app-developer  -->  implements improvements
```

## App Entry Point

`app.py` — Streamlit app, run with:
```
streamlit run app.py
```

## Secrets

Never commit `.env`. Keys go in `.env` locally and in Streamlit Cloud secrets for deployment.

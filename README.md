# כלי מחקר דיגיטלי לפולקלור ומלחמת חרבות ברזל

פרויקט Streamlit פשוט לחוקר פולקלור שעובד עם נרטיבים, אגדה, יציאות אסון ומודלים של שפה.

## התקנה מהירה

1. פתח את הטרמינל ב-VS Code.
2. עבור לתיקיית הפרויקט:
   ```powershell
   cd "c:\Users\7yona\OneDrive\Desktop\פרויקט ספרות עממית 7 באוקטובר"
   ```
3. צור והפעל סביבה וירטואלית:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
4. התקן את התלויות:
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
5. העתק `.env.example` ל-`.env` וערוך את המפתחות:
   ```powershell
   copy .env.example .env
   ```

## הפעלת האפליקציה

```powershell
streamlit run app.py
```

פתח את הדפדפן בכתובת `http://localhost:8501`.

## תנאים מקדימים

- `OPENAI_API_KEY` כדי לקרוא למודל השפה.
- `SEARCH_API_KEY` כדי לחפש תוצאות רשת באמצעות SerpAPI או Bing.
- `SEARCH_ENGINE` קבע ל-`serpapi` או `bing`.

## מה יש כאן

- `app.py` - ממשק Streamlit עם שני טאבים: חיפוש אוטומטי וניתוח ידני.
- `requirements.txt` - רשימת חבילות.
- `.env.example` - דוגמת קונפיגורציה.

## המשך

כדי לחבר את הסוכן לחיפוש אמיתי ולשפר את פרומפט המערכת, אפשר להוסיף את `SEARCH_API_KEY` ואת פרומפט המערכת העדכני ב-`app.py`.

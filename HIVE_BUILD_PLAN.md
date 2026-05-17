# HIVE — תוכנית הקמה מלאה

מסמך זה הוא ה-source of truth היחיד לבניית HIVE. הוא מבוסס על מחקר מקיף של עשרות מערכות קיימות (Claude Code subagents, claude-swarm, Claude Flow/Ruflo, opencode, Opcode/Claudia, Vibe Kanban, LangGraph, OpenHands, AutoGen, CrewAI, ועוד). כל החלטה ארכיטקטונית נשענת על ממצא או pattern שעבד במערכת קיימת.

המסמך מיועד לסוכן AI (Claude Code) שיבנה את המערכת. הוא קורא אותו לפני התחלת עבודה, שואל שאלות הכנה, ואז עובד phase by phase.

---

## חזון

HIVE היא מערכת ניהול כוורת של סוכני AI, רצה מקומית על מחשב המשתמש, שיושבת מעל Claude Code CLI. המטרה: ממשק נוח ושליטה מלאה על מספר סוכנים שעובדים במקביל, עם תמיכה בריבוי פרויקטים, אוטומציות מתמשכות, ושליטה מרחוק.

המשתמש מתחבר ל-HIVE דרך מנוי Claude Max שלו (OAuth, לא API key). כל הסוכנים שהמערכת מפעילה משתמשים באותו מנוי.

---

## עקרונות יסוד (Invariants)

עקרונות אלו נשמרים בכל החלטה ארכיטקטונית לאורך כל הפיתוח:

1. **Worker abstraction** — קוד ה-orchestrator אינו מכיר ב-`claude` CLI ישירות. הוא עובד עם `Worker` interface. יש שלוש implementations:
   - `ClaudeCLIWorker` (ברירת מחדל, OAuth דרך מנוי Max)
   - `ClaudeAPIWorker` (API key, fallback אם ToS משתנה)
   - `OllamaWorker` (LLM מקומי, חינמי בעלות טוקנים)
   
   ה-Orchestrator (Claude) מחליט לכל סוכן באיזה backend להשתמש. דוגמה: orchestrator + reviewer ב-Claude Opus (חכמים), builders ב-Claude Sonnet, translator/editor/summarizer ב-Ollama Llama מקומי. החלטה דינמית פר משימה.

2. **Event sourcing** — כל שינוי מצב נכתב כ-event ב-SQLite (append-only). העץ, הכרטיסים, ה-logs — כולם projections של event log. recovery הוא replay של events מנקודה ידועה.

3. **Git Worktree per agent** — כל סוכן שמבצע עבודה על קבצים רץ ב-git worktree משלו. אין שני סוכנים שחולקים תיקיית עבודה. מיזוג לענף הראשי קורה דרך ה-Reviewer.

4. **NDJSON pipeline אחיד** — כל פלט מסוכן עובר דרך אותו parser של `stream-json`. אין shortcuts של "קרא פלט סופי". תמיד buffer chunks, split על `\n`, parse כל שורה כ-JSON נפרד.

5. **Approval correlation IDs** — כל בקשת אישור נושאת correlation ID שורד restart של ה-backend. ID זה ניתן לניתוב ל-UI, טלגרם, או CLI.

6. **Rate-limit signals are first-class** — `system/api_retry` מ-`claude` CLI הוא לא retry שקט. הוא event שמעדכן UI, משעה סוכנים לא קריטיים, ושולח התראה.

7. **Cost discipline** — Opus רק ל-Orchestrator וRe-viewer. Sonnet ל-90% מעבודת ה-workers. Haiku למשימות חוזרות וקלות. מולטיפליקציית טוקנים ב-multi-agent היא 4-7x; בלי משמעת זה יחנוק את המנוי.

---

## ארכיטקטורה ברמת על

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (React)                         │
│        Dashboard / Tree Viz / Tabs / Approval UI             │
└────────────────────────┬────────────────────────────────────┘
                         │ WebSocket (state diffs + events)
┌────────────────────────┴────────────────────────────────────┐
│                    Backend (FastAPI)                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              LangGraph Orchestrator                   │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │  │
│  │  │ Planner  │→ │ Spawner  │→ │ Worker Pool      │   │  │
│  │  │ (Opus)   │  │          │  │ ⊕ Reviewer (Opus)│   │  │
│  │  └──────────┘  └──────────┘  └────────┬─────────┘   │  │
│  │                                       │              │  │
│  │                                       ↓              │  │
│  │                              ┌────────────────┐      │  │
│  │                              │ interrupt() →  │      │  │
│  │                              │ Approval gate  │      │  │
│  │                              └────────────────┘      │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────┴───────────────────────────────┐  │
│  │           Worker Interface (abstract)                 │  │
│  │  ClaudeCLIWorker (OAuth)  |  ClaudeAPIWorker (key)   │  │
│  └──────────────────────┬───────────────────────────────┘  │
│                         │ subprocess + stream-json          │
│  ┌──────────────────────┴───────────────────────────────┐  │
│  │  claude CLI processes (each in own git worktree)      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  APScheduler  |  aiogram Bot  |  Skills Registry      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  SQLite: events, sessions, agents, skills, pipelines  │  │
│  │          + LangGraph SqliteSaver checkpoints          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack — נעול

| רכיב | בחירה | סיבה |
|------|--------|------|
| Backend language | Python 3.11+ | asyncio בוגר, LangGraph נטיב, integration קל עם CLI |
| Web framework | FastAPI + uvicorn | async-first, WebSocket built-in, type hints |
| Orchestration | LangGraph 1.0+ | היחיד עם checkpointing + interrupt() + conditional edges + Send/Pregel parallelism |
| State store | SQLite (single file) | zero-ops, LangGraph SqliteSaver compatible, מספיק לעומס personal |
| Scheduler | APScheduler 3.x (AsyncIOScheduler) | in-process, cron + interval, יושב באותו event loop |
| Telegram bot | aiogram v3 | async-native, FSM נקי לזרימות אישור |
| Frontend | React 18 + Vite + TypeScript | אקוסיסטם בוגר, @xyflow/react מצוין |
| Tree viz | @xyflow/react (react-flow) v12 | purpose-built לעצי סוכנים, MIT, layout דרך dagre |
| Styling | TailwindCSS | מהיר ויפה |
| WebSocket | FastAPI WebSocket + native browser API | פשטות |
| Agent runtime | `claude` CLI subprocess | חובה לעבוד עם מנוי Max דרך OAuth |
| MCP integration | `claude mcp add` per session | סטנדרט נתמך רשמית |
| Tests | pytest + pytest-asyncio | סטנדרט |
| Process management | asyncio.subprocess + process groups | recovery נכון של זומבים |

מה לא בחרנו ולמה:
- **CrewAI** — אינטגרציה אווקרדית עם CLI subprocess, חסר checkpoint built-in.
- **AutoGen** — Microsoft הפסיקה פיתוח פעיל (maintenance mode).
- **Claudia/Opcode כ-base** — AGPL, מגביל הפצה עתידית.
- **Claude Flow / Ruflo** — מורכבות עצומה, טענות מנופחות, ארכיטקטורה אטומה.
- **Svelte/Svelvet** — חלופה לגיטימית אבל React + react-flow בוגר יותר ל-use case זה.

---

## הסוכנים — מודל דינמי

**עיקרון:** רק שני סוכנים קבועים. השאר נבחרים ע"י ה-Orchestrator בתחילת כל משימה.

### קבועים

**Orchestrator (מנכ"ל)** — תמיד Opus 4.7.
- מקבל משימות מהמשתמש
- מנתח ובוחר team composition
- מקצה roles, מודלים, ו-skills
- מתאם בין סוכנים
- היחיד עם שיחה רציפה עם המשתמש

**Reviewer (מנהל עבודה)** — תמיד Opus.
- רץ ברציפות, לא מחכה לקריאה
- בודק שכל סוכן הבין את המשימה ובכיוון נכון
- סורק תוצרים לאיכות ותקלות
- מדווח ל-Orchestrator
- ב-pipeline בוצע: גם merge קוד מ-worktrees

### Role Library (~/.hive/roles/)

כל role הוא קובץ YAML שמגדיר: שם, תיאור, system prompt template, default model, default skills, allowed tools.

Roles מובנים שיוטמעו ב-Phase 4:
- **Thinker** — תכנון ארכיטקטורה. Default: Opus.
- **Builder** — כתיבת קוד. Default: Sonnet. אפשר מספר במקביל.
- **Tester** — כתיבה והרצה של טסטים. Default: Sonnet.
- **Debugger** — passive, מופעל כשTester מחזיר failure. Default: Sonnet.
- **Researcher** — חיפוש מידע, השוואות. Default: Sonnet.
- **Writer** — יצירת תוכן. Default: Sonnet.
- **Editor** — עריכה. Default: Haiku.
- **Data Analyst** — ניתוח נתונים. Default: Sonnet.
- **Refactorer** — שיפור קוד קיים. Default: Sonnet.
- **Security Auditor** — סקירת אבטחה. Default: Opus.
- **Translator** — תרגום. Default: Haiku.
- **Doc Reader** — קריאת מסמכים ארוכים. Default: Haiku.

### בחירת מודל לפי קושי ועלות

ה-Orchestrator מקצה backend ומודל לכל סוכן לפי שלושה גורמים: קושי המשימה, יכולת המודל, ועלות טוקנים.

**Tier 1 — מודלים מקומיים (Ollama) — עלות 0₪**
- שימוש: משימות "פשוטות-בינוניות" שלא דורשות הסקה מורכבת.
- מתאים ל: translator, editor, summarizer, doc reader, simple formatter, classifier.
- ברירת מחדל: Llama 3.1 8B, Qwen 2.5 7B, או מה שזמין מקומית.
- יתרון: אפס עלות, אפס rate limit, אפס latency של רשת.
- חיסרון: איכות פחותה מ-Claude, מהירות תלויה ב-GPU של המשתמש.

**Tier 2 — Claude Haiku — עלות נמוכה**
- שימוש: משימות סטנדרטיות שדורשות יכולת טובה אבל לא הסקה עמוקה.
- מתאים ל: simple builder, basic tester, researcher של נושאים פשוטים.
- כשאין מודל מקומי זמין, זה ה-fallback של Tier 1.

**Tier 3 — Claude Sonnet — עלות בינונית**
- שימוש: רוב עבודת הליבה.
- מתאים ל: Builder, Tester, complex Researcher, Data Analyst, Refactorer.

**Tier 4 — Claude Opus — עלות גבוהה, יכולת מקסימלית**
- שימוש: רק תכנון אסטרטגי וביקורת איכות.
- מתאים ל: Orchestrator (תמיד), Reviewer (תמיד), Security Auditor, Thinker למשימות מורכבות.

### בחירת backend לפי זמינות

המערכת בודקת אילו backends זמינים בעת startup:
- בדיקת Ollama: `curl http://localhost:11434/api/tags` → אם מחזיר רשימת מודלים, Ollama זמין.
- בדיקת Claude CLI: `claude --version` → אם עובד, CLI זמין.
- בדיקת Claude API: בוחנים אם `ANTHROPIC_API_KEY` מוגדר.

**מצבים אפשריים:**

| מצב | מה זמין | התנהגות ברירת מחדל |
|-----|---------|---------------------|
| מלא | Ollama + Claude CLI | Ollama ל-Tier 1, Claude לכל השאר. החיסכון המקסימלי. |
| Cloud only | רק Claude CLI/API | כל הסוכנים על Claude, Tier 1 נופל ל-Haiku. |
| חיסכון מקסימלי | רק Ollama | רק משימות שמודל מקומי יכול לבצע. Orchestrator יזהיר אם המשימה דורשת יכולת מעבר. |

המשתמש יכול לכפות backend ספציפי לתפקיד מסוים ב-settings:
```yaml
backend_overrides:
  translator: ollama:llama3.1
  editor: ollama:qwen2.5
  reviewer: claude:opus  # תמיד opus
```

### Team composition examples

**"בנה auth מלא עם JWT"** (Cloud + Local hybrid) →
```json
{
  "team": [
    {"role": "Thinker", "backend": "claude:opus", "count": 1},
    {"role": "Builder", "backend": "claude:sonnet", "count": 2},
    {"role": "Tester", "backend": "claude:sonnet", "count": 1},
    {"role": "Security Auditor", "backend": "claude:opus", "count": 1},
    {"role": "Debugger", "backend": "claude:sonnet", "count": 1, "passive": true}
  ]
}
```

**"תרגם את התיעוד הזה לעברית"** (כמעט הכל מקומי) →
```json
{
  "team": [
    {"role": "Translator", "backend": "ollama:qwen2.5", "count": 1},
    {"role": "Editor", "backend": "ollama:llama3.1", "count": 1},
    {"role": "Reviewer", "backend": "claude:opus", "count": 1}
  ]
}
```
*עלות: רק ה-Reviewer צורך טוקנים מהמנוי. השאר 0₪.*

**"נתח את ה-CSV הזה"** →
```json
{
  "team": [
    {"role": "Data Analyst", "backend": "claude:sonnet", "count": 1},
    {"role": "Writer", "backend": "ollama:llama3.1", "count": 1}
  ]
}
```

**"סכם את כל הקבצים בתיקייה הזו"** (100% מקומי) →
```json
{
  "team": [
    {"role": "Doc Reader", "backend": "ollama:qwen2.5", "count": 3},
    {"role": "Summarizer", "backend": "ollama:llama3.1", "count": 1}
  ]
}
```
*עלות: 0₪. רק הצגת התוצאה ב-UI.*

### תקרת מקביליות

מבוסס על מחקר אמפירי — לא ידרוש מהמשתמש לקבוע ידנית, אבל המערכת תאכוף תקרה חכמה:
- ברירת מחדל: 3 סוכנים פעילים בו זמנית.
- ניתן לעקוף ב-settings עד 7.
- מעבר ל-3 מצריך אישור משתמש (אזהרת rate limits ו-RAM).
- ה-Orchestrator מתור משימות לסוכנים אם הצוות גדול מהתקרה.

---

## זרימת עבודה (Workflow) — One-shot

### שלב 1 — קבלת משימה
המשתמש שולח משימה ל-Orchestrator בטאב פעיל.

### שלב 2 — Team composition + Approval mode
ה-Orchestrator (Opus) מנתח ומחזיר:
1. **Team composition מוצע** — JSON של roles, counts, ומודלים. המשתמש יכול לערוך לפני אישור.
2. **בחירת approval mode**:
   - **Full Auto** — אל תשאל על כלום, רק על פעולות הרסניות (מחיקת קבצים, force push, drop table).
   - **Checkpoints** — עצור בנקודות ביקורת מוגדרות מראש (אחרי plan, אחרי build, אחרי tests).
   - **Per Action** — שאל לפני כל פעולת כתיבה / הרצה.

המשתמש מאשר → ה-Orchestrator שומר ל-session.

### שלב 3 — תכנון
ה-Orchestrator שולח ל-Thinker (אם נכלל בצוות).
Thinker מחזיר:
- ארכיטקטורה
- רשימת קבצים שייכתבו/יתעדכנו
- תלויות בין משימות
- **confidence score (0-1)** על ה-plan
- אומדן טוקנים גס

### שלב 4 — Confidence escalation
אם `confidence < 0.6`:
1. נכנסים ל-discussion בין Thinker ל-Orchestrator (loop של עד 3 turns).
2. אם אחרי discussion עדיין `< 0.6` — ה-Orchestrator שולח שאלה למשתמש דרך ה-UI (או טלגרם).
3. אם `>= 0.6` — ממשיכים בלי לטרוח את המשתמש.

### שלב 5 — הקמת Worktrees
לכל worker שייכתב/יערוך קבצים, ה-Orchestrator יוצר git worktree משלו תחת `~/.hive/worktrees/<session-id>/<worker-id>/`.

### שלב 6 — ביצוע במקביל
Workers רצים במקביל (עד תקרת מקביליות). Reviewer רץ ברציפות ברקע.

לכל פעולת כתיבת קובץ:
- Worker יוצר diff preview ומחזיר ל-Orchestrator
- **Full Auto** → Orchestrator מאשר אוטומטית
- **Checkpoints** → רק בנקודת ביקורת מבקש מהמשתמש
- **Per Action** → תמיד מבקש מהמשתמש
- timeout 30 שניות (Full Auto) → ממשיך

### שלב 7 — Reviewer Loop
Reviewer מציץ ב-events של כל worker ב-real time וסורק:
1. האם הסוכן הבין את ה-plan?
2. האם הקוד תואם לתוכנית?
3. בעיות אבטחה, איכות, anti-patterns?

מדווח ל-Orchestrator דרך state shared. Orchestrator מחליט אם להתערב (שולח הוראת תיקון, עוצר סוכן, או מבקש אישור משתמש).

### שלב 8 — Tests + Debugger
Tester מריץ את הטסטים. אם כשל — Debugger (שהיה passive) מתעורר עם stack trace ו-context רלוונטי, ומציע תיקון.

### שלב 9 — Merge ל-main
Reviewer ממזג את ה-worktrees לענף הראשי. קונפליקטים? הוא פותר אותם (או מבקש אישור משתמש אם לא בטוח).

### שלב 10 — סיכום
ה-Orchestrator מחזיר למשתמש סיכום: מה נעשה, זמן, טוקנים, קבצים, רשימת sessions.

---

## ריבוי פרויקטים (Multi-session via Tabs)

### שני סוגי פרויקטים
1. **One-shot** — פרויקט עם מטרה מוגדרת שמסתיים. עובר ל-archive בסיום.
2. **Persistent / Scheduled** — אוטומציה שרצה לנצח לפי טריגרים (cron, webhook, event, manual).

### Tabs UI
- Tab bar בראש. כל טאב = session.
- כפתור "+" לטאב חדש.
- אינדיקטור על הטאב:
  - 🟢 נקודה ירוקה = פעיל
  - 🟡 צהוב מהבהב = דורש אישור
  - 🔴 אדום = שגיאה
  - ⚪ אפור = מושהה
- מספר על הטאב = מספר התראות שמחכות.
- Ctrl+Tab למעבר, או קליק.

### State per tab
- cwd משלו
- כוורת עצמאית
- היסטוריית שיחה
- approval mode משלו
- token counter משלו

### משאבים משותפים
- **Skills** — משותפים בין כל הטאבים
- **Roles** — משותפים
- **Memory + Checkpoints** — נפרדים לחלוטין

---

## פרויקטים Persistent (Automations)

### שני שלבי חיים

**Build phase (חד-פעמי):**
משתמש מתאר את האוטומציה. Orchestrator עם צוות מלא בונה:
- pipeline (רצף פעולות)
- סוכנים קבועים שיריצו אותו
- הגדרות (schedule, triggers, notifications)
- skills נדרשות
- בדיקה שכל הכלים החיצוניים זמינים

בסיום: "להפעיל את האוטומציה?"

**Run phase (מתמשך):**
- **Scheduler** קל יושב ברקע (חלק מ-APScheduler באותו תהליך FastAPI), לא צורך טוקנים.
- **Pipeline** מוגדר ב-LangGraph spec serialized ב-SQLite.
- כשטריגר נדלק → Scheduler טוען את ה-pipeline → מפעיל את ה-Orchestrator → רץ.

### Trigger types
- **Schedule** — cron expressions ("0 17 * * *" = כל יום 17:00)
- **Webhook** — URL ייעודי (`/webhooks/<pipeline-id>/<secret>`)
- **Event** — file watcher, mail listener, telegram message
- **Manual** — "Run now" מה-UI או מטלגרם

### דוגמה מלאה — אוטומציית סרטון יומי

**Build:**
משתמש: "כל יום ב-17:00, סרטון אנכי 30 שניות בנושא טכנולוגיה, אישור בטלגרם, העלאה לטיקטוק ואינסטגרם."

Orchestrator בונה:
- **Researcher** (Sonnet) — מוצא נושא טרנדי יומי
- **Scriptwriter** (Sonnet) — סקריפט 30 שניות
- **Voice Generator** (Haiku + TTS MCP) — voiceover
- **Video Generator** (Haiku + ComfyUI MCP) — סרטון מהמודל המקומי
- **Editor** (Sonnet) — עורך, מוסיף כתוביות
- **Uploader** (Haiku + Social MCP) — מעלה לרשתות

בודק שיש: TTS מקומי, ComfyUI, חשבון טלגרם, חשבונות רשתות. חסר משהו? מבקש מהמשתמש להגדיר. אישור סופי → "האוטומציה תרוץ כל יום 17:00. להפעיל?"

**Run (כל יום):**
17:00 — Scheduler מעיר → Orchestrator מפעיל את ה-pipeline:
1. Researcher → נושא
2. Scriptwriter → סקריפט
3. Voice Generator → MP3
4. Video Generator → MP4 גולמי
5. Editor → MP4 סופי עם כתוביות

**Approval async דרך טלגרם:**
- שולח את הסרטון לבוט
- כפתורים inline: ✓ אשר והעלה / ✗ דחה / 🔄 צור גרסה חדשה
- אישור → Uploader → התראת סיום
- דחייה → אופציה לבקש שיפור או לבטל

---

## תפעול מרחוק (Telegram Bot)

### היקף שליטה
- **רואה** — כל הפעילות
- **מאשר/דוחה** — בקשות אישור
- **שואל** — שאלות חופשיות ל-Orchestrator
- **מתערב** — תיקון, עצירה, broadcast
- **מפעיל** — Run now ידני
- **משהה/ממשיך** — pause/resume

### מה אסור מרחוק (security)
- פעולות הרסניות בסיווג high-risk (drop table, rm -rf, force push)
- שינוי הגדרות מערכת
- יצירת פרויקט חדש
- פרויקטים המסומנים "local approval only"

### Commands
**Status:** `/status`, `/projects`, `/project <name>`, `/agents <project>`, `/log <project>`, `/files <project>`

**Action:** `/pause <project>`, `/resume <project>`, `/stop_urgent <project>`, `/run <project>`, `/say <project> <message>`, `/switch <project>`

**Conversational:** הודעה רגילה → ל-Orchestrator של הפרויקט הפעיל (האחרון שדיברת איתו).

### Inline buttons
כל בקשת אישור כוללת כפתורים: ✓ אשר / ✗ דחה / 📝 הוראת תיקון / 👁 ראה diff.

### הגדרות
- **Bot token אישי**
- **Allowed chat IDs** — הגנה מ-token leak
- **Notification preferences** — אילו events לשלוח
- **Quiet hours**
- **Auto-approve allowlist** — פרויקטים שלא צריך לאשר ידנית בטלגרם

---

## מסך הבית (Dashboard)

לפני כניסה לפרויקט ספציפי.

### רכיבים
- **Top bar** — לוגו HIVE, כפתור "+ פרויקט חדש", settings, skills.
- **Summary stats** — פרויקטים פעילים, דורשים תשומת לב, סוכנים רצים, טוקנים היום.
- **Projects grid** — כרטיס לכל פרויקט.
- **Recent projects** — רשימה של פרויקטים שהסתיימו.

### כרטיס פרויקט
- שם + path
- פס סטטוס (ירוק/צהוב/אדום/אפור)
- badge "דורש אישור" אם רלוונטי
- משימה נוכחית (שורה)
- Progress bar (one-shot) או "הריצה הבאה: ..." (persistent)
- אייקוני סוכנים שעובדים
- Meta: זמן רץ, טוקנים, קבצים/insights/runs

### הבחנה ויזואלית
- **One-shot** — אייקון מטרה, progress bar רגיל
- **Persistent** — אייקון 🔁, schedule + run history

---

## עץ הסוכנים (Tree Canvas)

**זה ה-UX differentiator המרכזי של HIVE.** אין לזה equivalent בשום פתרון קיים.

- **Orchestrator** בראש כעיגול עם טבעת מסתובבת.
- **סוכנים-משנה** מתחתיו כ-hexagons עם אייקון לפי role.
- קווי חיבור מקווקווים, סוכן פעיל = קו מודגש.
- כל hex מציג: שם, פעילות נוכחית בשורה, badge סטטוס.
- לחיצה על סוכן פותחת אותו ב-sidebar (LIVE / SKILLS / FILES / PLAN).

### אינטראקציה
- **Async chat** — לחיצה על סוכן פותחת שיחה ב-sidebar. השיחה לא קוטעת את עבודתו; הוא עונה כשיש לו רגע.
- **Urgency modes** — 4 כפתורי מעל input:
  - 💬 שאלה — async, לא קוטע
  - ✏️ תיקון — עדיפות גבוהה, מסיים פעולה נוכחית ומיישם
  - ⛔ עצירה דחופה — קוטע מיד
  - ⚡ Broadcast — לכל הסוכנים
- **Target dropdown** — בחירת יעד: orchestrator, סוכן ספציפי, או "all agents".

---

## Skills Registry + Injection חכם

### Storage
`~/.hive/skills/<skill-name>/SKILL.md` — פורמט תואם ל-Anthropic Skills (YAML frontmatter + markdown).

### Registry
SQLite table: `skills(id, name, description, embedding, tool_spec_json, path)`.

### Injection
לפני שסוכן מתחיל משימה:
1. ה-Orchestrator קורא את תיאור המשימה
2. embedding שלה דרך `sentence-transformers/all-MiniLM-L6-v2` (מקומי, מהיר)
3. cosine search ב-skill embeddings — top K
4. הזרקה רק של ה-K הרלוונטיות לאותו סוכן

ככה אין שליחת 50 skills × 5 סוכנים. בממוצע 3-5 skills לסוכן.

### ניהול
- `hive skills list` — רשימה
- `hive skills import <path-or-url>` — ייבוא
- `hive skills create <name>` — wizard ליצירה
- `hive skills test <name> <task>` — בדיקה אילו skills היו נבחרות למשימה

---

## State & Persistence

### SQLite schema
```sql
-- Event log (append-only, source of truth)
events (id, ts, session_id, agent_id, type, payload_json)

-- Sessions (projects)
sessions (id, name, path, type, status, approval_mode, created_at, last_active)

-- Agents per session
agents (id, session_id, role, model, status, worktree_path, started_at, ended_at)

-- Skills
skills (id, name, description, embedding_blob, tool_spec_json, path)

-- Persistent pipelines
pipelines (id, session_id, langgraph_spec_json, schedule_cron, trigger_config_json)
pipeline_runs (id, pipeline_id, started_at, ended_at, status, tokens, cost_estimate)

-- Approval requests (correlation IDs)
approvals (id, session_id, agent_id, action, payload_json, status, created_at, resolved_at, channel)

-- Cost tracking
cost_log (session_id, agent_id, ts, input_tokens, output_tokens, cost_usd_estimate)

-- Telegram
telegram_users (chat_id, name, allowed, preferences_json)
```

### LangGraph SqliteSaver
שמירת state של כל LangGraph super-step. recovery אוטומטי בעת אתחול.

### Memory לסוכן
כל סוכן שומר memory תמציתי משלו: `~/.hive/sessions/<id>/agents/<agent-id>/memory.md`.
זה לא ה-context המלא — תקציר של "מה עשיתי, מה למדתי, השלב הבא". אם סוכן נהרג ומופעל מחדש — קורא רק את ה-memory הזה. חיסכון משמעותי בטוקנים.

### Checkpoints אוטומטיים
כל 60 שניות (configurable), ה-Orchestrator מבקש מ-LangGraph לשמור snapshot. recovery: `hive resume <session-id>`.

---

## Streaming & Process Management

### `claude` CLI invocation
```python
proc = await asyncio.create_subprocess_exec(
    "claude", "-p", prompt,
    "--output-format", "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--dangerously-skip-permissions",
    "--max-turns", str(max_turns),
    cwd=worktree_path,
    env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token},
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    preexec_fn=os.setsid,  # process group for clean kill
)
```

### Streaming pipeline
```python
buf = b""
while True:
    chunk = await proc.stdout.read(4096)
    if not chunk:
        break
    buf += chunk
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # incomplete; will retry next chunk
        await dispatch(event)
```

### Event handling
- `system/init` → סוכן נוצר, session_id נשמר
- `stream_event` (text_delta) → token streaming ל-UI
- `tool_use` → בודקים approval mode → אישור אוטומטי או interrupt
- `tool_result` → log + Reviewer ניזון
- `message` → assistant message → state update
- `result` → סוכן סיים, total_cost_usd ו-usage נכתבים ל-cost_log
- `system/api_retry` → אם `error: rate_limit` → השהיה של non-critical workers + התראת UI + טלגרם

### Process management
- כל PID נשמר ב-`agents.pid`
- בעת אתחול backend → query agents עם status='active' → קח PID → אם לא קיים בPID list של המערכת, סמן כ-crashed
- recovery: טען checkpoint אחרון, שאל משתמש אם להמשיך
- termination: SIGTERM ל-process group, wait 5s, SIGKILL
- `ulimit -n 10240` ב-startup script

### Idle timeout
`CLAUDE_STREAM_IDLE_TIMEOUT_MS=600000` (10 דקות) — אם stream שותק, fail מהר במקום לתקוע.

---

## Cost Tracking

### Per-session
לכל event מסוג `result`, parse `total_cost_usd` ו-`usage`. כתוב ל-`cost_log`. הצג ב-token bar בטאב.

### Per-day / per-week aggregation
```sql
SELECT SUM(cost_usd_estimate) FROM cost_log 
WHERE ts > datetime('now', '-7 days');
```

### Rate limit awareness
מנוי Max לא חושף quota ב-API. גישה: burn rate analysis.
- מחשב ממוצע שעתי 7-day rolling
- אם שעה אחרונה > 2x ממוצע → התראה "burn rate גבוה"
- אם מתחיל לראות `api_retry: rate_limit` → התרעה אדומה ועצירת non-critical work

---

## OAuth + מנוי Max — אסטרטגיה

### בסיס
- המשתמש מריץ `claude setup-token` פעם אחת (אתחול HIVE) → טוקן OAuth ארוך-טווח.
- HIVE שומר ב-`~/.hive/credentials.json` עם file permissions 0600.
- כל הפעלת `claude` subprocess מקבלת `CLAUDE_CODE_OAUTH_TOKEN` כ-env var.

### Fallback ל-API key
`Worker` interface תומך בשתי implementations:
```python
class Worker(Protocol):
    async def run(self, prompt: str, ...) -> AsyncIterator[Event]: ...

class ClaudeCLIWorker(Worker):
    """משתמש ב-OAuth subscription"""

class ClaudeAPIWorker(Worker):
    """משתמש ב-ANTHROPIC_API_KEY"""
```

ב-settings: `worker_type: "cli" | "api"`. החלפה היא restart של backend.

### Disclaimer בהתקנה
ב-onboarding, המשתמש רואה הודעה:
> "HIVE משתמש במנוי Claude Max שלך דרך OAuth מקומי. השימוש האישי הזה תואם את ToS של Anthropic. אל תשתף את HIVE עם משתמשים אחרים על אותו מנוי, אל תשתמש בו לשימוש מסחרי, ואל תפיץ כשירות לאחרים."

---

## MCP Integration

### Servers שיגיעו pre-configured
- **Ollama** — `claude mcp add ollama -- npx mcp-ollama`
- **ComfyUI** — `claude mcp add comfy -- npx mcp-comfyui` (אם זמין)
- **Telegram bot API** — בנוי בתוך HIVE עצמו (לא MCP חיצוני)
- **File system** — מובנה ב-Claude Code

### Servers שהמשתמש מוסיף
ב-settings UI: רשימת MCP servers, אפשרות להוסיף custom, view של tools זמינים מכל server.

### חיווי במחקר
Twitter/X, Instagram, LinkedIn — לא יציב או דורש תשלום. במסך setup של pipeline persistent עם social media, מציגים אזהרה: "פלטפורמות social זמינות אבל בעלות מוגבלות API. ייתכן ויידרשו הגדרות נוספות וחשבונות developer."

---

## תיקיות פרויקט

```
hive/
├── backend/
│   ├── orchestrator/
│   │   ├── graph.py             # LangGraph definition
│   │   ├── nodes/
│   │   │   ├── planner.py       # team composition
│   │   │   ├── spawner.py       # אתחול workers
│   │   │   ├── reviewer.py      # continuous review loop
│   │   │   ├── approval.py      # interrupt() handlers
│   │   │   └── aggregator.py    # סיכום
│   │   └── state.py             # GraphState TypedDict
│   ├── workers/
│   │   ├── base.py              # Worker interface
│   │   ├── claude_cli.py        # ClaudeCLIWorker (OAuth, default)
│   │   ├── claude_api.py        # ClaudeAPIWorker (API key fallback)
│   │   ├── ollama.py            # OllamaWorker (local LLM)
│   │   ├── stream_parser.py     # NDJSON pipeline
│   │   └── process_manager.py   # PIDs, recovery, cleanup
│   ├── worktrees/
│   │   └── manager.py           # git worktree create/remove/merge
│   ├── roles/
│   │   ├── registry.py
│   │   └── builtin/             # YAML files
│   ├── skills/
│   │   ├── registry.py          # SQLite + embedding search
│   │   └── injector.py          # bind skills to subagent
│   ├── persistence/
│   │   ├── db.py                # SQLite + migrations
│   │   ├── events.py            # event sourcing
│   │   └── checkpoints.py       # LangGraph SqliteSaver wrapper
│   ├── pipelines/
│   │   ├── builder.py           # build phase
│   │   ├── runner.py            # run phase
│   │   └── scheduler.py         # APScheduler integration
│   ├── api/
│   │   ├── ws.py                # WebSocket handlers
│   │   ├── http.py              # REST endpoints (webhooks)
│   │   └── schemas.py           # Pydantic models
│   ├── telegram/
│   │   ├── bot.py               # aiogram setup
│   │   ├── handlers/
│   │   │   ├── commands.py      # /status, /pause, etc.
│   │   │   ├── conversational.py # free chat
│   │   │   └── callbacks.py     # inline button handlers
│   │   └── fsm.py               # approval flow states
│   ├── cost/
│   │   └── tracker.py           # parse usage events
│   ├── config.py
│   └── main.py                  # FastAPI app + startup
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── ProjectCard.tsx
│   │   │   ├── TabBar.tsx
│   │   │   ├── TreeCanvas.tsx   # @xyflow/react
│   │   │   ├── AgentNode.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── InputSection.tsx
│   │   │   ├── EventLog.tsx
│   │   │   └── ApprovalModal.tsx
│   │   ├── stores/
│   │   │   ├── session.ts       # zustand
│   │   │   └── projects.ts
│   │   ├── ws.ts                # WebSocket client
│   │   └── types.ts
│   ├── package.json
│   └── vite.config.ts
├── cli/
│   └── hive.py                  # `hive start`, `hive resume`, etc.
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
│   ├── architecture.md
│   ├── api.md
│   └── user-guide.md
├── pyproject.toml
├── CLAUDE.md                    # ל-Claude Code שמפתח את HIVE
└── README.md
```

---

## Phases של פיתוח

עבודה לפי phases. בכל phase: קוד + טסטים + עדכון CLAUDE.md. אישור משתמש לפני מעבר ל-phase הבא.

### Phase 0 — Worker bedrock (5-7 ימים)
**מטרה:** שלוש implementations של Worker שעובדים.
- `Worker` interface (base.py)
- `ClaudeCLIWorker` — subprocess wrapper נכון (process groups, env vars), stream_parser עם buffer + split על `\n`, כל event types מטופלים, טיפול ב-`api_retry` עם backoff
- `OllamaWorker` — HTTP client ל-`http://localhost:11434`, streaming של tokens, המרה ל-event format מאוחד שדומה ל-stream-json
- Backend detection בעת startup (Ollama? Claude CLI? API key?)
- טסטים: mock subprocess + טסטים של כל event type, בדיקת fallback בין backends
- CLI bootstrap: `hive run "task" --backend ollama:llama3.1` או `--backend claude:sonnet`

**Definition of done:** 
- `hive run "echo hello" --backend claude:sonnet` עובד
- `hive run "translate to Hebrew: hello" --backend ollama:llama3.1` עובד (אם Ollama מותקן)
- שני ה-backends פולטים events בפורמט אחיד שה-orchestrator יכול לצרוך

### Phase 1 — Single Agent + State (3-5 ימים)
**מטרה:** סוכן בודד דרך LangGraph עם persistence.
- LangGraph graph בסיסי (node אחד)
- `SqliteSaver` integration
- DB schema + migrations
- Event log writes
- recovery על startup
- CLAUDE.md update

**Definition of done:** משימה רצה, נשמרת ל-SQLite, רצה ה-backend מחדש, ניתן לראות את ה-state.

### Phase 2 — Multi-agent + Worktrees (5-7 ימים)
**מטרה:** Orchestrator + workers במקביל.
- Planner node — Opus, מחזיר team composition כ-JSON
- Spawner — יוצר worktrees, מפעיל workers ב-parallel (LangGraph Send)
- Reviewer node — רץ במקביל, צופה ב-events
- Worker pool עם תקרת מקביליות
- Merge logic ב-Reviewer
- CLAUDE.md update

**Definition of done:** משימה "בנה לי REST API פשוט" — ה-Orchestrator בוחר team (Thinker + 2 Builders + Tester), כל אחד עובד ב-worktree, Reviewer ממזג, המשתמש רואה את התוצאה.

### Phase 3 — Approval modes + Confidence (3-5 ימים)
**מטרה:** Human-in-the-loop.
- `interrupt()` nodes ב-3 מצבים שונים
- Confidence parsing + escalation logic
- Approval correlation IDs
- CLI prompts לאישור (UI יבוא ב-Phase 5)

**Definition of done:** משימה ב-Per Action mode עוצרת לפני כל write ושואלת בקונסול. אחרי "yes" ממשיכה.

### Phase 4 — Skills Registry (2-3 ימים)
**מטרה:** Skill injection.
- Skills table + embeddings
- `sentence-transformers` integration (local model)
- CLI: `hive skills list / import / create / test`
- Injection לוגיקה (top-K)
- Roles library מאוכלסת

**Definition of done:** `hive skills test "build a react component" returns relevant frontend skills`.

### Phase 5 — Web UI + WebSocket (7-10 ימים)
**מטרה:** ממשק גרפי מלא.
- FastAPI WebSocket
- Frontend bootstrap (Vite + React + Tailwind)
- Dashboard עם cards
- Tab bar
- TreeCanvas עם @xyflow/react
- Sidebar tabs
- Input section + urgency modes
- Approval modals
- WebSocket state diffs

**Definition of done:** משתמש פותח דפדפן, רואה dashboard, יוצר פרויקט, רואה את העץ עובד בזמן אמת, מאשר/דוחה דרך ה-UI.

### Phase 6 — Persistent Pipelines (5-7 ימים)
**מטרה:** אוטומציות.
- Pipeline spec serialization
- APScheduler integration
- Webhook endpoints
- Pipeline build wizard (UI)
- Run history view
- Two-phase logic (build vs run)

**Definition of done:** אוטומציה "כל יום ב-17:00 כתוב הייקו ושמור לקובץ" עובדת אוטומטית.

### Phase 7 — Telegram Bot (3-5 ימים)
**מטרה:** שליטה מרחוק.
- aiogram bot setup
- Commands handlers
- Inline buttons + callbacks
- Conversational handler
- FSM לזרימות אישור
- Configuration UI להגדרת bot

**Definition of done:** משתמש שולח `/status` ומקבל סטטוס פרויקטים. בקשת אישור מגיעה דרך טלגרם עם כפתורים, לחיצה ממשיכה את ה-pipeline.

### Phase 8 — Polish (3-5 ימים)
- Cost dashboard מלא
- Auto-recovery testing
- Error handling shotgun (כל path)
- Quiet hours לטלגרם
- Skill creation wizard
- Documentation
- Onboarding flow

---

## דרישות מ-Claude Code לפני התחלה

לפני שמתחיל לכתוב קוד, Claude Code צריך לשאול את המשתמש:

1. באיזו מערכת הפעלה תרוץ HIVE? (macOS / Linux / Windows-WSL)
2. רוצה להתקין דרך `uv` או `pip + venv`?
3. PORT ל-WebSocket / API (ברירת מחדל: 8765)?
4. האם להגדיר systemd/launchd service ל-backend או להפעיל ידנית בכל פעם?
5. רוצה לחבר טלגרם bot כבר עכשיו או בהמשך?
6. **האם Ollama מותקן על המחשב? אם כן, אילו מודלים זמינים (פלט של `ollama list`)?** ה-Orchestrator ישתמש בהם למשימות פשוטות ויחסוך טוקנים. אם לא — Claude Code לבדו עובד מצוין; אפשר להוסיף Ollama אחר כך.

ואז להסביר:
- שיתחיל מ-Phase 0
- שיציג plan לפני כל phase ויחכה לאישור
- שיכתוב טסטים לפני סיום כל phase
- שיעדכן CLAUDE.md עם החלטות ארכיטקטוניות תוך כדי
- שישתמש ב-type hints מלא ו-Python 3.11+
- שלא ישתמש ב-OpenAI API או כל שירות חיצוני — רק `claude` CLI subprocess ו-MCP servers
- שיוודא שהקוד עובד על כל מערכות ההפעלה הרלוונטיות

---

## דברים שאסור לעשות

1. **אל תהפוך את HIVE למוצר רב-משתמשים.** שימוש אישי בלבד דרך OAuth של Max זה fine. multi-tenant deployment על אותו מנוי = הפרת ToS.
2. **אל תאמן או תזמן embeddings מ-Anthropic.** השתמש במודל מקומי (`sentence-transformers/all-MiniLM-L6-v2`).
3. **אל תנסה לפרסר טקסט גולמי מ-`claude`.** השתמש רק ב-`--output-format stream-json` NDJSON.
4. **אל תריץ סוכן בלי worktree.** אפילו אם זה רק קריאה.
5. **אל תשתמש ב-`--bare`** — הוא חוסם OAuth ודורש API key.
6. **אל תקפוץ phases.** כל phase מסתיים עם טסטים שעוברים.
7. **אל תוסיף תלות חיצונית כבדה** (Redis, Postgres, Celery). SQLite + APScheduler in-process מספיק.
8. **אל תפרסם את HIVE לציבור** בלי שינוי ל-API key mode וקריאה זהירה של ToS.

---

## הצלחה = ?

**MVP מוצלח:**
- משתמש מפעיל HIVE, רואה dashboard.
- יוצר פרויקט, מתאר משימה.
- ה-Orchestrator בוחר team, מציג אותו, מקבל אישור.
- 3 סוכנים רצים במקביל ב-worktrees, נראים בעץ, מתעדכנים בזמן אמת.
- Reviewer מתערב ומתקן בעיה.
- Per Action mode עובר אישורים דרך ה-UI.
- בסיום: code merged ל-main, סיכום עם cost.
- כל זה תוך פחות מ-3% מ-weekly quota של Max.

**v1.0:**
- Multi-project tabs עובדים.
- אוטומציה אחת persistent רצה לפחות שבוע יציב.
- שליטה מלאה דרך טלגרם.
- recovery אוטומטי אחרי crash.
- 50+ skills בריג'יסטרי.

---

*סוף מסמך התוכנית. Claude Code: קרא, שאל את 5 השאלות, ובוא נתחיל מ-Phase 0.*

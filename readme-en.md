<div align="center">

# 🫁 Peakflow Monitor

**Telegram Bot and Mini App for Peak Expiratory Flow (PEF) monitoring**

*Multi-family · Multiple children · Mini App · Doctor PDF · Gamification · Analytics · PWA*

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-0078D4.svg)](https://docs.aiogram.dev)
[![SQLite](https://img.shields.io/badge/SQLite-schema%20v5-003B57.svg)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/Tests-598%20passed-brightgreen.svg)](test/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 📖 About

Peak flowmetry measures the maximum speed of exhalation (PEF), a key metric for monitoring
asthma and other respiratory conditions. The service helps families track readings, spot
trends, and never miss a measurement: a **bot** for quick actions and alerts, and a
**Telegram Mini App** for charts, analytics, and settings.

The service is multi-family: each family is isolated (`family_id`), a family has one or
more children, and each parent has their own active child. Registration is self-service:
create a family or join by invite code.

### ✨ Features

| Feature | Description |
|---------|-------------|
| 🏠 **Families & invites** | Create a family or join by code (`/start`, deep-link) |
| 👨‍👩‍👧 **Multiple children** | Active child per parent (bot and Mini App) |
| 🕐 **Auto morning/evening** | Time of day: `< 12:00` → 🌅, `≥ 12:00` → 🌆 |
| 📊 **Status block** | Today's readings, last result, trend, 🔥 day streak |
| 📥 **Quick input** | Button → number → done (stepwise hundreds/tens), range 100–690 |
| ✏️ **Edit last** | "✏️ Edit last" — no delete-and-re-enter |
| 📈 **Summary & week** | Today's readings with stats; week vs last week by morning/evening |
| 📊 **Chart (Mini App)** | Types line/bars/points, range week/month, tooltip with note, "Compare" mode |
| 🏅 **Gamification** | Day streak; achievements 7/30/100 days and 100/500/1000 readings |
| 📄 **Doctor PDF** | A4 report (chart + stats + table) for week/month/quarter from bot and Mini App |
| 📈 **Analytics** | Zones (pie), average PEF by weekday (heatmap), 14-day trend, note correlation |
| 📲 **PWA** | Install Mini App to home screen, offline app shell |
| 🔔 **Reminders** | For child and parents; hours configurable per family |
| 📋 **Weekly report** | Every Sunday at 21:00, per child |
| 📲 **Notifications** | Each new reading — to the family's parents |
| 🚨 **Red zone alerts** | Instant alert when PEF < 60% (except the author) |
| 📥 **Export & backup** | CSV by period; single-family DB backup |
| 🩺 **Metrics** | Extended `/healthz` and optional `GET /metrics` (Prometheus) |

---

## 🚀 Quick Start

### 1. Get a Bot Token

Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.

### 2. Find Telegram IDs

Open [@userinfobot](https://t.me/userinfobot) → send a message → copy the `Id`.

### 3. Configure `.env`

```bash
cp .env.example .env
```

```env
BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxx
CHILD_ID=123456789              # seed/fallback for family #1 child
PARENT_IDS=987654321,111222333  # seed/fallback for family #1 parents
CHILD_NAME=Emma
TARGET_PEF=260
DB_PATH=peakflow.db
TZ_OFFSET=5

WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8080
WEBAPP_URL=
METRICS_ENABLED=0
METRICS_TOKEN=
```

> Direct IDs in `.env` are only needed for family #1 (migration seed and fallback).
> Other families register in the bot: "🏠 Create family" or "🔑 Join by code".

### 4. Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

---

## 📱 Mini App

When `WEBAPP_URL` is set, the bot adds a "💨 Diary" menu button. Tabs:

- **Today** — today's readings, quick input with a note;
- **History** — paginated, edit/delete (parents);
- **Chart** — canvas without libraries: types line/bars/points, range week/month,
  tooltip (value, %, zone, note), "Compare" mode (current vs previous period);
- **Stats** — averages, trend, 🔥 streak and badge grid;
- **Analytics** — zones (pie), weekday heatmap, 14-day trend, note correlation;
- **⚙️ Settings** (parents) — target PEF, reminder hours, doctor PDF, CSV export, DB backup.

Access is limited to the child and parents: Telegram initData signature is verified and
data older than 24 hours is rejected. PWA: the Mini App installs to the home screen and a
service worker caches the app shell (API data is never cached).

---

## 🎨 Color Zones

| Zone | % of Target | Meaning |
|:---:|:---:|---------|
| 🟢 | ≥ 80% | Good control |
| 🟡 | 60–79% | Caution — worsening |
| 🔴 | < 60% | Danger — contact your doctor |

---

## 🔔 Automatic Notifications

| Event | When | Who |
|-------|------|-----|
| ⏰ Missed reading (morning/evening) | Per family hours (default 10:00 / 22:00) | Family parents |
| 🤖 Auto-carry record | Same, if a previous value exists | Family parents |
| 📋 Weekly report | Sun 21:00 | Family parents (per child) |
| 🚨 Red zone | Instant | Parents (except the author) |
| 📝 New reading | Instant | Other parents |
| 🎉 Achievement | On unlock (once) | Family parents |

---

## 🗂 Project Structure

```
peakflow/
├── bot.py              # Bot logic (handlers, FSM, scheduler)
├── database.py         # SQLite CRUD + migrations (PRAGMA user_version, schema v5)
├── config.py           # Settings and thresholds
├── report.py           # Pure helpers and CSV
├── report_pdf.py       # Doctor PDF reports (A4)
├── gamification.py     # Day streak and achievements
├── analytics.py        # Zones, weekdays, trend, note correlation
├── metrics.py          # In-process metrics + Prometheus render
├── web/                # Mini App: api.py, server.py, auth.py, notify.py, static/ (PWA)
├── scripts/            # migration_dry_run.py
├── test/               # Pytest tests
├── manage.sh           # Install & operations
├── .env.example        # .env template
├── PROJECT.md          # Full technical documentation
├── wiki.md             # Code-level logic documentation
└── roadmap.md          # Historical audit and roadmap
```

---

## 🧪 Tests

```bash
python -m pytest test/ -v
```

```
598 passed
```

Tests run without `.env` (`test/conftest.py` sets a test DB and dummy token). CI
([GitHub Actions](.github/workflows/ci.yml)) additionally runs `pyflakes` and `compileall`
on Python 3.11 and 3.12.

---

## ⚙️ Configuration

### Environment Variables

| Variable | Type | Req. | Default | Description |
|----------|------|:---:|:---:|-------------|
| `BOT_TOKEN` | string | ✅ | — | Bot token from BotFather |
| `CHILD_ID` | int | ❌ | `0` | Family #1 child Telegram ID (seed/fallback) |
| `PARENT_IDS` | string | ❌ | `0,0` | Family #1 parent IDs, comma-separated (seed/fallback) |
| `CHILD_NAME` | string | ❌ | `Ребёнок` | Family #1 child display name |
| `TARGET_PEF` | int | ❌ | `260` | Default target PEF |
| `DB_PATH` | string | ❌ | `peakflow.db` | SQLite database path |
| `TZ_OFFSET` | int | ❌ | `5` | Time zone (UTC offset) |
| `WEBAPP_HOST` | string | ❌ | `127.0.0.1` | Mini App web server host |
| `WEBAPP_PORT` | int | ❌ | `8080` | Port (0 = disable web) |
| `WEBAPP_URL` | string | ❌ | — | Public HTTPS URL of the Mini App |
| `METRICS_ENABLED` | bool | ❌ | `0` | `1` — enable `GET /metrics` |
| `METRICS_TOKEN` | string | ❌ | — | Bearer token for `/metrics` |

### Constants (in `config.py`)

| Constant | Value | Description |
|----------|:---:|-------------|
| `ZONE_GREEN` | `80` | Green zone threshold, % |
| `ZONE_YELLOW` | `60` | Yellow zone threshold, %; red is below |
| `WEEKLY_REPORT_DAY` | `6` | Report day (0=Mon, 6=Sun) |
| `WEEKLY_REPORT_HOUR` | `21` | Report hour |

> Reminder hours are per family (set by a parent).

---

## ⚠️ Disclaimer

> **This bot is not a medical device.**
> It is designed solely for monitoring and tracking data.
> All treatment decisions must be made in consultation with a qualified healthcare professional.

---

## 📄 License

MIT

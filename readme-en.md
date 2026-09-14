<div align="center">

# 🫁 Peakflow Monitor

**Telegram Bot for Peak Expiratory Flow (PEF) Monitoring**

*1 child + 2 parents · Auto-registration · Smart reminders*

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-0078D4.svg)](https://docs.aiogram.dev)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57.svg)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/Tests-171%20passed-brightgreen.svg)](test/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 📖 About

Peak flowmetry measures the maximum speed of exhalation (PEF — Peak Expiratory Flow), a key metric for monitoring asthma and other respiratory conditions. This bot helps families track readings, spot trends, and never miss a measurement.

### ✨ Features

| Feature | Description |
|---------|-------------|
| 🤖 **Auto-registration** | All 3 family members known by ID from `.env` — no setup needed |
| 🕐 **Auto morning/evening** | Time of day detected automatically: `< 12:00` → 🌅, `≥ 12:00` → 🌆 |
| 📊 **Status block** | In main menu: today's readings, last result, trend |
| 📥 **Quick input** | Button → number → done. Minimum taps |
| ✏️ **Edit last** | "✏️ Edit last" button — no delete-and-re-enter |
| 📈 **Today's summary** | All today's readings + morning/evening stats (parents only) |
| 📊 **Week vs last week** | Weekly comparison with separate morning/evening trends |
| 🏆 **Best / Worst** | Best and worst results annotated on the chart |
| 🔔 **Missed reading alerts** | 10:00 / 22:00 — if child forgot to measure |
| 📋 **Weekly report** | Every Sunday at 21:00 — full summary to both parents |
| 📲 **Parent notifications** | Each new reading notified to the other parent |
| 🚨 **Red zone alerts** | Instant alert to both parents when PEF < 60% of target |

---

## 🚀 Quick Start

### 1. Get a Bot Token

Open [@BotFather](https://t.me/BotFather) → `/newbot` → follow instructions → copy the token.

### 2. Find Telegram IDs

Open [@userinfobot](https://t.me/userinfobot) → send any message → copy the `Id` for each family member.

### 3. Configure `.env`

```bash
cp .env.example .env
```

```env
BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxx
CHILD_ID=123456789
PARENT_IDS=987654321,111222333
CHILD_NAME=Emma
TARGET_PEF=260
DB_PATH=peakflow.db
```

### 4. Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

---

## 📱 What It Looks Like

### Main Menu

```
👋 Emma | Target: 260 L/min

Today: 🌅 240 🟢 | 🌆 —
Last: 240 🟢 (+10)

┌─────────────────────────────┐
│  💨 Measurement             │
│  📋 History  │  📊 Chart    │
│  📊 Summary  │  📈 Week     │
│  ✏️ Edit last               │
└─────────────────────────────┘
```

### Adding a Reading

```
💨 Enter PEF (L/min)
🌅 Morning (auto-detected)
Range: 50–800

> 245
```

```
✅ 🌅 Morning: 245 L/min 🟢
Zone: Green (94% of target)
📈 Change: +5 L/min
```

### Weekly Report

```
📋 Emma — week 2026-04-07 — 2026-04-13

Readings: 12 (🌅 7 / 🌆 5)
Average: 248
🏆 Best: 270 (🌆 12.04)
⚠️ Worst: 225 (🌅 09.04)

📈 This week vs last:
🌅 Morning: 240 → 246 (+6) 🟢
🌆 Evening: 255 → 251 (-4) 🟡
```

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
| ⏰ Morning reading missed | 10:00, if no 🌅 | Both parents |
| ⏰ Evening reading missed | 22:00, if no 🌆 | Both parents |
| 📋 Weekly report | Sun 21:00 | Both parents |
| 🚨 Red zone | Instant | Both parents |
| 📝 New reading | Instant | Other parent |

---

## 🗂 Project Structure

```
picklo/
├── bot.py              # Main bot logic (~790 lines)
├── database.py         # SQLite CRUD (~275 lines)
├── config.py           # Settings, family IDs, thresholds (~70 lines)
├── test/               # Pytest tests (test/test_bot.py etc.)
├── requirements.txt    # Python dependencies
├── .env                # Environment variables
├── .env.example        # .env template
├── readme-en.md        # This file
├── readme-ru.md        # Russian version
└── PROJECT.md          # Full technical documentation
```

---

## 🧪 Tests

```bash
python -m pytest test/ -v
```

```
19 passed in ~9s
```

| Category | Tests |
|----------|:---:|
| Database | 11 |
| Config | 4 |
| Bot helpers | 4 |

---

## ⚙️ Configuration

### Environment Variables

| Variable | Type | Req. | Default | Description |
|----------|------|:---:|:---:|-------------|
| `BOT_TOKEN` | string | ✅ | — | Bot token from BotFather |
| `CHILD_ID` | int | ✅ | — | Child's Telegram ID |
| `PARENT_IDS` | string | ✅ | — | Parents' IDs, comma-separated |
| `CHILD_NAME` | string | ❌ | `Child` | Child's display name |
| `TARGET_PEF` | int | ❌ | `260` | Target PEF from doctor (L/min) |
| `DB_PATH` | string | ❌ | `peakflow.db` | SQLite database path |

### Constants (in `config.py`)

| Constant | Value | Description |
|----------|:---:|-------------|
| `ZONE_GREEN` | `80` | Green zone threshold, % |
| `ZONE_YELLOW` | `60` | Yellow zone threshold, % |
| `REMINDER_MORNING_DEADLINE` | `10` | Morning reading deadline (hour) |
| `REMINDER_EVENING_DEADLINE` | `22` | Evening reading deadline (hour) |
| `WEEKLY_REPORT_DAY` | `6` | Report day (0=Mon, 6=Sun) |
| `WEEKLY_REPORT_HOUR` | `21` | Report hour |

---

## 📊 PEF Norms by Age

Reference values (do not replace medical advice):

| Age | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 |
|-----|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| PEF, L/min | 140 | 170 | 200 | 230 | 260 | 290 | 320 | 350 | 380 | 410 | 440 | 470 | 500 | 530 | 560 |

---

## 📸 Chart

The 30-day chart includes:

- 📈 Line of all readings
- 🟠 Morning reading dots
- 🟣 Evening reading dots
- 🟢 Target norm line
- 🏆 Best result annotation
- ⚠️ Worst result annotation

---

## ⚠️ Disclaimer

> **This bot is not a medical device.**  
> It is designed solely for monitoring and tracking data.  
> All treatment decisions must be made in consultation with a qualified healthcare professional.

---

## 📄 License

MIT

---

<div align="center">

**Made with ❤️ for health**

</div>

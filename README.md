# 🧵 AI Apparel Pattern Drafting Bot

A professional Telegram bot that converts measurement sheet photos into production-ready DXF patterns with AAMA-standard layering, Bezier curve interpolation, and 2D blueprint previews.

## Architecture

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Telegram    │────▶│  OpenRouter     │────▶│  Generator   │────▶│  DXF + PNG   │
│  User Photo  │     │  Claude Sonnet  │     │  Engine      │     │  Output      │
│  Upload      │     │  (Multimodal)   │     │  (Bezier)    │     │  (AAMA)      │
└──────────────┘     └─────────────────┘     └──────────────┘     └──────────────┘
                            │                                               │
                            ▼                                               ▼
                     ┌─────────────┐                               ┌──────────────┐
                     │ Style + Size │                               │  Render      │
                     │ Extraction   │                               │  Worker      │
                     └─────────────┘                               └──────────────┘
```

## Features

- **Multimodal Measurement Extraction** — Upload a photo of a measurement sheet; the bot uses Claude Sonnet 4 via OpenRouter to extract body measurements, garment type, silhouette, and styling details.
- **Dynamic Pattern Drafting** — Bezier curve interpolation for necklines, armholes, sleeve caps. No hardcoded templates — every pattern is computed from your actual measurements.
- **Style-Aware Drafting** — Cowl necklines, gathered panels, asymmetric hems, pleats, drop shoulders, wrap closures — all dynamically applied based on parsed style.
- **AAMA-Standard DXF Output** — Layer 1 (cut lines), Layer 3 (internal lines), Layer 4 (seam lines), Layer 8 (annotations). Compatible with CAD pattern cutters.
- **2D Blueprint Preview** — Labelled technical drawing with measurement table, grainlines, notches, and legend.
- **Template Database** — Stores generated patterns for future matching by garment type + measurements.
- **Text Input Fallback** — Can also parse measurements typed as text.

## Setup

### Prerequisites

1. **Telegram Bot Token** — Create a bot via [@BotFather](https://t.me/BotFather)
2. **Google API Key** (free) — Get one at [Google AI Studio](https://aistudio.google.com/apikey). The free tier includes multimodal vision.
3. **OpenRouter API Key** (optional fallback) — Get one at [openrouter.ai](https://openrouter.ai/keys)
3. **Render Account** — Sign up at [render.com](https://render.com)
4. **GitHub Repo** — Fork or clone `ajazkhan-web/garment`

### Step 1: Configure GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret Name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from BotFather |
| `GOOGLE_API_KEY` | Your Google API key (free, primary engine) |
| `OPENROUTER_API_KEY` | Your OpenRouter API key (optional fallback) |
| `RENDER_DEPLOY_HOOK_URL` | Deploy hook URL from Render (Step 3) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (for deploy notifications) |

To get your chat ID: send any message to [@userinfobot](https://t.me/userinfobot) on Telegram.

### Step 2: Create Render Service

1. Go to [render.com](https://render.com) → New → **Background Worker**
2. Connect your GitHub repo: `ajazkhan-web/garment`
3. Configure:
   - **Name**: `apparel-pattern-bot`
   - **Branch**: `main`
   - **Runtime**: Python 3
   - **Build Command**: `pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir matplotlib Pillow || echo "matplotlib/Pillow optional — skipping"`
   - **Start Command**: `python telegram_bot.py`
4. Add Environment Variables:
   - `TELEGRAM_BOT_TOKEN` = your token
   - `GOOGLE_API_KEY` = your Google API key (free, primary)
   - `OPENROUTER_API_KEY` = your OpenRouter key (optional fallback)
   - `BOT_MODE` = `polling`
5. Click **Create Background Worker**

### Step 3: Set Up Auto-Deploy

1. In your Render service → Settings → **Auto-Deploy**: Enable
2. In Settings → **Deploy Hook** → Create one → Copy the URL
3. Add the deploy hook URL as `RENDER_DEPLOY_HOOK_URL` in GitHub Secrets (Step 1)

Now every `git push` to `main` will:
1. Run syntax checks + end-to-end tests (GitHub Actions)
2. Trigger a Render deploy
3. Send you a Telegram notification when the deploy starts

### Step 4: Verify

1. Open Telegram and message your bot: `/start`
2. Upload a measurement sheet photo
3. The bot will extract measurements, show a summary, and ask you to `/confirm`
4. After confirmation, you'll receive:
   - A `.dxf` pattern file (AAMA-standard layers)
   - A `.png` blueprint preview (if matplotlib is available)

## Commands

| Command | Description |
|---|---|
| `/start` | Initialize the bot |
| `/garment <type>` | Set garment type (dress, top, skirt, shirt, kurti) |
| `/ease <level>` | Set ease (minimal, standard, loose) |
| `/draft` | Show current spec summary |
| `/confirm` | Generate DXF + blueprint from current session |
| `/edit <field> <value>` | Edit a measurement field |
| `/templates` | List stored pattern templates |
| `/reset` | Clear session and start over |

## Tech Stack

- **python-telegram-bot** 21.6 — Telegram Bot API
- **Google Gemini 2.5 Flash** (free tier) — Multimodal measurement extraction (primary)
- **OpenRouter** (Claude Sonnet 4) — Fallback extraction (optional, paid)
- **ezdxf** 1.4.4 — DXF file generation with AAMA layers
- **matplotlib** — 2D blueprint rendering (optional)
- **Pillow** — Image processing (optional)
- **Render** — Background worker deployment

## File Structure

```
├── telegram_bot.py      # Bot logic, OpenRouter client, command handlers
├── generator.py         # Drafting engine, PatternPiece, DXFExporter, TemplateDB
├── blueprint.py         # 2D blueprint renderer (matplotlib)
├── config.py            # Configuration (models, paths, AAMA layers)
├── requirements.txt     # Python dependencies (matplotlib/Pillow optional)
├── render.yaml          # Render service configuration
├── .github/workflows/   # CI/CD pipeline (auto-deploy + Telegram notifications)
└── README.md            # This file
```

## License

© EJAJ KHAN. All rights reserved.

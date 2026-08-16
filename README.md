# AI-Powered Automated Apparel Pattern Drafting Telegram Bot

Automated CAD pattern generation system integrated with a Telegram bot, powered by the OpenRouter API. Automates professional garment drafting based on custom measurement sheets and exports industry-standard DXF/AAMA files compatible with Optitex, Gerber, and Lectra.

## Features

- **Measurement Sheet Ingestion** — Upload photos of measurement sheets or type measurements as text; the bot uses OpenRouter's multimodal LLM to extract structured data
- **Garment Specification Summary** — Returns a structured breakdown before drafting (ease, seam allowances, construction plan, pattern pieces)
- **Universal Drafting Engine** — Handles dresses, kurtis, bodice blocks, skirts, shirts, and sleeves using standard flat-pattern drafting formulas (Aldrich method)
- **AAMA DXF Export** — Professional DXF files with industry-standard layers:
  - Layer 1: Cutting lines
  - Layer 5: Seam lines
  - Layer 7: Grainlines
  - Layer 8: Notches
  - Layer 3: Internal lines (darts, reference)
  - Layer 9: Mirror axes (CF/CB)
- **PDS Template Storage** — Upload `.PDS`/`.DXF` templates; the system stores and matches them for future auto-grading
- **File Caching** — Generated patterns are cached locally and indexed in SQLite

## Project Structure

```
├── telegram_bot.py      # Telegram bot interface + OpenRouter integration
├── generator.py         # Universal drafting engine + DXF/AAMA exporter
├── config.py            # Central configuration (env vars, constants, paths)
├── requirements.txt     # Python dependencies
├── render.yaml          # Render.com deployment config
├── data/                # SQLite template database (auto-created)
├── templates/           # Stored PDS/DXF templates (auto-created)
├── output/              # Generated DXF files (auto-created)
└── cache/               # File cache (auto-created)
```

## Setup

### Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- OpenRouter API Key (from [openrouter.ai](https://openrouter.ai))

### Local Development

```bash
# Clone the repo
git clone <your-repo-url>
cd apparel-pattern-bot

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TELEGRAM_BOT_TOKEN="your-bot-token"
export OPENROUTER_API_KEY="your-openrouter-key"
export BOT_MODE="polling"

# Run
python telegram_bot.py
```

### Deploy to Render

1. Push this repo to GitHub
2. Create a new Web Service on [Render](https://render.com)
3. Connect your GitHub repository
4. Set environment variables:
   - `TELEGRAM_BOT_TOKEN`
   - `OPENROUTER_API_KEY`
   - `BOT_MODE=polling`
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python telegram_bot.py`

## Usage

1. Start a chat with your bot on Telegram
2. Upload a measurement sheet photo OR type measurements (e.g. "bust 92, waist 72, hip 96, dress length 100")
3. Review the extracted measurements
4. Send `/draft` to see the garment specification summary
5. Send `/confirm` to generate the DXF/AAMA file
6. The bot sends the CAD file directly in chat

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show help |
| `/draft` | Generate spec summary |
| `/confirm` | Generate and deliver DXF file |
| `/garment <type>` | Set garment type (dress, kurti, bodice, skirt, shirt, sleeve) |
| `/ease <level>` | Set ease (minimal, standard, loose) |
| `/templates` | List stored PDS templates |
| `/edit <key> <value>` | Edit a measurement |
| `/reset` | Clear current session |

## Technical Stack

- **Interface:** Telegram Bot API (python-telegram-bot)
- **AI/LLM Engine:** OpenRouter API (Claude 3.5 Sonnet — multimodal)
- **CAD Generation:** ezdxf with AAMA layer standards
- **Storage:** SQLite for template matching and metadata
- **Deployment:** Render (continuous deployment via GitHub)

## License

MIT

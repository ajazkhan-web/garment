"""
config.py — Configuration for the Apparel Pattern Drafting Bot.
Sole AI provider: Google Gemini (free tier, multimodal vision).
No OpenRouter — no fallback. Gemini is the only engine.
"""
import os

# ─── Telegram ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ─── AI Provider (Google Gemini ONLY) ───
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"          # fast, free tier, multimodal
GEMINI_VISION_MODEL = "gemini-2.5-flash"   # same model handles text+image

# Strict enforcement: Gemini is the sole AI provider. No fallback.
DEFAULT_AI_PROVIDER = "gemini"
USE_GEMINI = True

# ─── AAMA DXF Layer Standards ───
# AAMA-standard DXF layer names: key -> DXF layer name string
# Matches the format expected by generator.py (string keys, string values)
AAMA_LAYERS = {
    "CUT":        "1",   # Main cut outline
    "SEAM":       "8",   # Seam allowances / stitching lines
    "GRAIN":      "4",   # Grainlines / internal reference
    "NOTCH":      "3",   # Notch marks
    "INTERNAL":   "4",   # Internal lines (darts, gathers)
    "REFERENCE":  "6",   # Reference lines
    "ANNOTATION": "7",   # Annotations, text labels
    "MIRROR":     "9",   # Mirror / symmetry lines
}

# ─── Pattern Drafting Constants ───
SEAM_ALLOWANCE = 1.0       # cm
HEM_ALLOWANCE = 2.5        # cm
OUTPUT_DIR = os.path.join(os.getcwd(), "output")
DATABASE_PATH = os.path.join(os.getcwd(), "data", "templates.db")
TEMPLATE_DIR = os.path.join(os.getcwd(), "templates")

# ─── Ease Values ───
EASE_BODICE = {"minimal": 2.0, "standard": 4.0, "loose": 6.0}
EASE_SKIRT = {"minimal": 2.0, "standard": 3.0, "loose": 5.0}

DEFAULT_EASE = "standard"
EASE_VALUES = {
    "minimal":  2.0,
    "standard": 4.0,
    "loose":    8.0,
}

# ─── Template Database ───
TEMPLATE_DB_PATH = os.environ.get("TEMPLATE_DB_PATH", "templates.db")

# ─── Bot Settings ───
BOT_MODE = os.environ.get("BOT_MODE", "polling")
MAX_SESSION_MINUTES = 30

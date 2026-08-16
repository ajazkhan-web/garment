"""
config.py — Configuration for the Apparel Pattern Drafting Bot.
Supports dual AI providers: Google Gemini (primary, free) + OpenRouter (fallback).
"""
import os

# ─── Telegram ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ─── AI Provider Selection ───
# Primary: Google Gemini (free tier, multimodal)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"          # fast, free tier, multimodal
GEMINI_VISION_MODEL = "gemini-2.5-flash"   # same model handles text+image

# Fallback: OpenRouter (Claude Sonnet 4, paid)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_VISION_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# ─── Which provider to use ───
# If GOOGLE_API_KEY is set, use Gemini (free). Otherwise use OpenRouter.
USE_GEMINI = bool(GOOGLE_API_KEY)

# ─── AAMA DXF Layer Standards ───
AAMA_LAYERS = {
    1: {"name": "CUT",      "color": 7,   "desc": "Main cut outline"},
    3: {"name": "INTERNAL", "color": 3,   "desc": "Internal lines (darts, gathers)"},
    4: {"name": "SEAM",     "color": 5,   "desc": "Seam allowances / stitching lines"},
    8: {"name": "ANNOT",    "color": 2,   "desc": "Annotations, text, grainlines"},
}

# ─── Pattern Defaults ───
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

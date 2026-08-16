"""
config.py — Configuration for the Professional Apparel Pattern Drafting Bot.
Sole AI provider: Google Gemini (multimodal vision + reasoning).
"""

import os


def _normalize_google_key(key: str) -> str:
    """Google AI Studio keys use format 'AQ.<rest>'. Secret scanners sometimes
    strip the 'AQ.' prefix — restore it automatically. Legacy 'AIza...' keys
    are left untouched."""
    if not key:
        return key
    if key.startswith("AIza") or key.startswith("AQ."):
        return key
    return "AQ." + key


# ─── Telegram ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ─── AI Provider (Google Gemini ONLY) ───
_KEY_CANDIDATES = [
    os.environ.get("GOOGLE_API_KEY_3", ""),
    os.environ.get("GOOGLE_API_KEY_2_2", ""),
    os.environ.get("GOOGLE_API_KEY_2", ""),
    os.environ.get("GOOGLE_API_KEY", ""),
]
GOOGLE_API_KEY = ""
for _k in _KEY_CANDIDATES:
    if _k:
        GOOGLE_API_KEY = _normalize_google_key(_k)
        break

# gemini-3.5-flash is the current available model (Aug 2026).
# gemini-flash-latest gets 503 under load; 3.5-flash is stable.
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_VISION_MODEL = "gemini-3.5-flash"

# Reasoning budget: 256 tokens gives the model enough room to accurately
# read measurement tables, identify garment types, and reason about style.
GEMINI_THINKING_BUDGET = 256

# Strict: Gemini is the sole AI provider. No fallback.
DEFAULT_AI_PROVIDER = "gemini"

# ─── AAMA DXF Layer Standards ───
AAMA_LAYERS = {
    "CUT":        "1",
    "SEAM":       "8",
    "GRAIN":      "4",
    "NOTCH":      "3",
    "INTERNAL":   "4",
    "REFERENCE":  "6",
    "ANNOTATION": "7",
    "MIRROR":     "9",
}

# ─── Pattern Drafting Constants ───
SEAM_ALLOWANCE = 1.0       # cm
HEM_ALLOWANCE = 2.5        # cm
OUTPUT_DIR = os.path.join(os.getcwd(), "output")
DATABASE_PATH = os.path.join(os.getcwd(), "pattern_references.db")
TEMPLATE_DIR = os.path.join(os.getcwd(), "templates")

# ─── Ease Values ───
EASE_VALUES = {
    "minimal":  2.0,
    "standard": 4.0,
    "loose":    8.0,
}
DEFAULT_EASE = "standard"

# ─── Reference Library ───
REFERENCE_DB_PATH = os.environ.get("REFERENCE_DB_PATH", "pattern_references.db")

# ─── Bot Settings ───
BOT_MODE = os.environ.get("BOT_MODE", "polling")
MAX_SESSION_MINUTES = 30

"""
Configuration module — centralises env vars, constants, and shared settings.
"""
import os

# --- API Keys (from secrets / env) ---
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# --- OpenRouter ---
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-sonnet-4"  # multimodal-capable, valid OpenRouter model
OPENROUTER_VISION_MODEL = "anthropic/claude-sonnet-4"

# --- Bot Mode ---
BOT_MODE = os.environ.get("BOT_MODE", "polling")  # "polling" or "webhook"

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DATABASE_PATH = os.path.join(BASE_DIR, "data", "templates.db")

# --- Ensure dirs exist ---
for d in (CACHE_DIR, TEMPLATE_DIR, OUTPUT_DIR, os.path.dirname(DATABASE_PATH)):
    os.makedirs(d, exist_ok=True)

# --- Drafting Constants ---
EASE_BODICE = {
    "minimal": 2.0,   # cm
    "standard": 4.0,
    "loose": 6.0,
}
EASE_SKIRT = {
    "minimal": 2.0,
    "standard": 3.0,
    "loose": 5.0,
}
SEAM_ALLOWANCE = 1.0   # cm default
HEM_ALLOWANCE = 2.5     # cm default

# AAMA DXF Layer names (industry standard)
AAMA_LAYERS = {
    "CUT":           "1",    # Cutting outline
    "SEAM":          "5",    # Seam lines
    "GRAIN":         "7",    # Grainlines
    "NOTCH":         "8",    # Notches
    "INTERNAL":      "3",    # Internal lines (darts, drill marks)
    "REFERENCE":     "4",    # Reference / construction lines
    "ANNOTATION":    "6",    # Text annotations
    "MIRROR":        "9",    # Mirror lines
}

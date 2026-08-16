"""
telegram_bot.py - Telegram Bot for AI Pattern Generation Pipeline

Flow:
  1. User sends management sheet / dress photo via Telegram
  2. Bot sends image to AI (OpenAI GPT-4 Vision) for measurement extraction
  3. generator.py creates the 2D pattern (DXF + preview)
  4. Bot sends DXF + preview image back to user via Telegram

Setup:
  1. pip install -r requirements.txt
  2. Create a Telegram bot via @BotFather, get the token
  3. Get an OpenAI API key from https://platform.openai.com
  4. Copy .env.example to .env and fill in your keys
  5. Run: python telegram_bot.py

Author: Built with Solene (Base44 Superagent) pattern drafting methodology
"""

import os
import json
import logging
import tempfile
import asyncio
from typing import Dict, Optional

# Telegram
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextBuilder,
    filters,
)

# AI Vision
import openai

# Pattern generator
from generator import generate_pattern, GARMENT_DRAFTERS

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Vision-capable model on OpenRouter. You can swap to "anthropic/claude-3.5-sonnet" too.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================================
# AI DRESS ANALYSIS - Extract measurements from spec sheet photo
# ============================================================

async def analyze_spec_sheet(image_path: str) -> Dict:
    """
    Send the spec sheet photo to GPT-4 Vision and get structured measurements.
    Returns a dict of measurement_name -> value_in_inches.
    """
    client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    # Read image as base64
    import base64
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # Determine file extension for mime type
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg"
    if ext == ".png":
        mime = "image/png"
    elif ext == ".webp":
        mime = "image/webp"

    prompt = """You are a professional garment pattern maker with 20 years of experience.
Analyze this management/spec sheet image carefully and extract ALL measurements.

Return a JSON object with these fields (all values in INCHES as numbers, no units in the value):
{
  "garment_type": "tshirt_dress|shirt|jacket|pants|palazzo",
  "length": number,
  "bust": number (or "chest" for jackets),
  "waist": number,
  "hip": number,
  "bottom": number (or "bottom_width" for pants),
  "shoulder": number,
  "neck_width": number,
  "armhole": number,
  "sleeve_length": number,
  "sleeve_opening": number,
  "fnd": number (front neck drop),
  "bnd": number (back neck drop),
  "fabric_type": "string",
  "notes": "any special construction notes visible on the sheet"
}

For jackets/bombers, also extract:
  "bicep", "bottom_relax", "rib_height", "full_zip" (true/false),
  "side_pocket_opening", "neck_drop_front", "neck_drop_back"

For pants, also extract:
  "front_rise", "back_rise", "hip_drop"

For wrap/blazer dresses with a shawl collar, waist seam, darts, or side pleats, also extract:
  "back_neck_width", "front_neck_drop_to_cross" (HPS to wrap cross point),
  "cb_collar_height", "bicep", "waistband_top_below_ah" (waistband top seam distance below armhole),
  "waistband_height", "num_left_pleats", "pleat_depth", "pleat_to_pleat_dist",
  "pleat_dist_from_waist", "overlap_side_facing_width", "num_buttons",
  "overlap_gap_from_left_side", "overlap_extra_bottom_point",
  "above_waist_dart_length", "num_waist_darts"
  Set garment_type to "wrap_blazer_dress" if you see a shawl/wrap collar with a waist seam and pleats/darts - this is NOT a simple shirt.

If a measurement is not visible on the sheet, omit it (don't guess).
Be extremely precise - read every number carefully.
Return ONLY the JSON, no other text."""

    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_data}"
                        }
                    }
                ]
            }
        ],
        max_tokens=1000,
        temperature=0.1,  # Low temperature for precise extraction
    )

    raw_text = response.choices[0].message.content.strip()

    # Clean up markdown code blocks if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

    measurements = json.loads(raw_text)
    return measurements


# ============================================================
# GARMENT TYPE DETECTION
# ============================================================

def detect_garment_type(measurements: Dict) -> str:
    """Auto-detect garment type from measurements if not specified."""
    gt = measurements.get("garment_type", "").lower().strip()
    if gt in GARMENT_DRAFTERS:
        return gt

    # Auto-detect from measurement keys - CHECK MOST SPECIFIC FIRST
    if "front_rise" in measurements or "back_rise" in measurements:
        return "pants"
    if "rib_height" in measurements or "bottom_relax" in measurements:
        return "jacket"
    # Wrap blazer dress: shawl collar + waist seam + darts/pleats/overlap markers
    if ("cb_collar_height" in measurements or "shawl" in str(measurements.get("notes", "")).lower()
            or "wrap" in str(measurements.get("notes", "")).lower()
            or "waistband_height" in measurements or "waistband_top_below_ah" in measurements
            or "num_left_pleats" in measurements or "front_neck_drop_to_cross" in measurements):
        return "wrap_blazer_dress"
    if "yoke_height" in measurements or "collar_height" in measurements:
        return "shirt"
    if "sleeve_length" in measurements and measurements.get("sleeve_length", 0) > 15:
        return "shirt"
    return "tshirt_dress"


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def start_command(update: Update, context):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to the AI Pattern Generator Bot!\n\n"
        "Send me a photo of your management/spec sheet and I'll:\n"
        "1. Analyze the measurements\n"
        "2. Generate a 2D pattern (DXF file)\n"
        "3. Send you the DXF + preview image\n\n"
        "Supported garment types: t-shirt dress, shirt, jacket/bomber, pants/palazzo\n\n"
        "Make sure your spec sheet photo is clear and all measurements are readable."
    )


async def help_command(update: Update, context):
    """Handle /help command."""
    await update.message.reply_text(
        "📤 Send a clear photo of your management/spec sheet.\n"
        "🤖 I'll extract measurements using AI vision\n"
        "📐 Generate the 2D pattern automatically\n"
        "📁 Send you the DXF file + preview\n\n"
        "Tips for best results:\n"
        "- Good lighting, no shadows\n"
        "- All text/numbers clearly visible\n"
        "- Include the garment photo if available\n"
        "- One spec sheet per message"
    )


async def handle_photo(update: Update, context):
    """
    Main handler: receives spec sheet photo, analyzes it, generates pattern,
    sends back DXF + preview.
    """
    photo = update.message.photo[-1]  # Highest resolution
    file_id = photo.file_id

    # Get the file
    telegram_file = await context.bot.get_file(file_id)

    # Download to temp file
    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = os.path.join(tmpdir, "spec_sheet.jpg")
        await telegram_file.download_to_drive(image_path)

        # Step 1: Acknowledge receipt
        await update.message.reply_text(
            "📸 Got your spec sheet! Analyzing measurements...\n"
            "Step 1/4: AI dress analysis 🔍"
        )

        # Step 2: AI analysis
        try:
            measurements = await analyze_spec_sheet(image_path)
            gt = detect_garment_type(measurements)
            await update.message.reply_text(
                f"✅ Measurements extracted!\n"
                f"Garment type: {gt}\n"
                f"Found {len(measurements)} measurements\n"
                f"Step 2/4: Generating pattern... 📐"
            )
            logger.info(f"Measurements: {json.dumps(measurements, indent=2)}")
        except json.JSONDecodeError as e:
            await update.message.reply_text(
                "❌ Could not parse measurements from the image.\n"
                "Please send a clearer photo of the spec sheet."
            )
            return
        except Exception as e:
            await update.message.reply_text(
                f"❌ AI analysis failed: {str(e)}\n"
                "Check your OpenAI API key and try again."
            )
            return

        # Step 3: Generate pattern
        output_dir = os.path.join(tmpdir, "output")
        try:
            result = generate_pattern(measurements, gt, output_dir)
            await update.message.reply_text(
                f"✅ Pattern generated! {result['piece_count']} pieces\n"
                f"Step 3/4: Creating files... 📄\n\n"
                f"Measurements used:\n" +
                "\n".join(f"  {k}: {v}\"" for k, v in measurements.items()
                         if isinstance(v, (int, float)))
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Pattern generation failed: {str(e)}\n"
                f"Garment type: {gt}\n"
                "Check the measurements and try again."
            )
            return

        # Step 4: Send results
        await update.message.reply_text("Step 4/4: Sending files... 📤")

        # Send preview image
        if result.get("preview") and os.path.exists(result["preview"]):
            with open(result["preview"], "rb") as preview_file:
                await update.message.reply_photo(
                    photo=InputFile(preview_file),
                    caption=f"📊 Pattern Preview - {result['piece_count']} pieces"
                )

        # Send DXF file
        if result.get("dxf") and os.path.exists(result["dxf"]):
            with open(result["dxf"], "rb") as dxf_file:
                await update.message.reply_document(
                    document=InputFile(dxf_file, filename=os.path.basename(result["dxf"])),
                    caption="📐 DXF file (AAMA/ASTM, native cm) - Import via File → Import → DXF in Optitex 15"
                )
            await update.message.reply_text(
                "✅ Done! Import the DXF into Optitex 15:\n"
                "1. Open Optitex 15\n"
                "2. File → Import → DXF\n"
                "3. Select the file\n"
                "4. Format: AAMA/ASTM, Units: Centimeters, Scale: 1:1\n"
                "5. Click OK"
            )
        else:
            await update.message.reply_text(
                "⚠️ Preview generated but DXF creation failed.\n"
                "Make sure ezdxf is installed: pip install ezdxf"
            )


async def handle_text(update: Update, context):
    """Handle text messages."""
    text = update.message.text.lower()
    if "hello" in text or "hi" in text:
        await start_command(update, context)
    elif "help" in text:
        await help_command(update, context)
    else:
        await update.message.reply_text(
            "📤 Send me a photo of your management/spec sheet to generate a pattern.\n"
            "Type /help for instructions."
        )


def main():
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set!")
        print("1. Create a bot via @BotFather on Telegram")
        print("2. Set TELEGRAM_BOT_TOKEN environment variable or in .env file")
        return

    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set!")
        print("1. Get an API key from https://openrouter.ai/keys")
        print("2. Set OPENROUTER_API_KEY environment variable or in .env file")
        return

    print("=" * 50)
    print("  AI Pattern Generator Telegram Bot")
    print("=" * 50)
    print(f"  Supported garments: {', '.join(GARMENT_DRAFTERS.keys())}")
    print(f"  AI model: {OPENROUTER_MODEL} (via OpenRouter)")
    print(f"  DXF format: AAMA/ASTM R2000, native cm")
    print("=" * 50)
    print("  Bot is running... Press Ctrl+C to stop")
    print("=" * 50)

    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Start polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
Extension
Extension Embed



Actions

Your Business

Settings

Help
Search Amazon

United States
Search Amazon


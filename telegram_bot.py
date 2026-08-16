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
import re
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
    filters,
)

# AI Vision
import openai

# Pattern generator
from generator import generate_pattern, GARMENT_DRAFTERS

# PDS file parser (learns from your Optitex pattern files)
from pds_parser import parse_pds, save_to_library, find_matching_template, summarize_pieces

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
  "garment_type": "tshirt_dress|shirt|jacket|pants|palazzo|wrap_blazer_dress",
  "style_name": "short descriptive name, e.g. 'Poncho Wrap Top' or 'Camo Bomber Jacket'",
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
            style_name = measurements.get("style_name") or gt.replace("_", " ").title()

            template = find_matching_template(gt)
            template_note = f"\n📚 Found saved template: {template['style_name']} ({template['piece_count']} pieces) — reusing construction logic" if template else ""

            await update.message.reply_text(
                f"✅ Analysis complete!\n"
                f"Style: {style_name}\n"
                f"Garment type: {gt.replace('_', ' ').title()}\n"
                f"Measurements found: {len(measurements)}"
                f"{template_note}\n\n"
                f"Step 2/4: Drafting pattern... 📐"
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
        except Exception as e:
            await update.message.reply_text(
                f"❌ Pattern generation failed: {str(e)}\n"
                f"Garment type: {gt}\n"
                "Check the measurements and try again."
            )
            return

        # Step 4: Send results (professional summary card, like a real pattern-maker's draft note)
        m = measurements
        length_val = m.get("length")
        bust_val = m.get("bust") or m.get("chest")
        sleeve_val = m.get("sleeve_length")
        waist_val = m.get("waist")

        summary_lines = [f"🎉 Optitex 2D Pattern Draft Ready!", ""]
        summary_lines.append(f"• Style: {style_name}")
        summary_lines.append(f"• Garment type: {gt.replace('_', ' ').title()}")
        if length_val: summary_lines.append(f"• Length: {length_val}\"")
        if bust_val: summary_lines.append(f"• Bust/Chest: {bust_val}\"")
        if waist_val: summary_lines.append(f"• Waist: {waist_val}\"")
        if sleeve_val: summary_lines.append(f"• Sleeve: {sleeve_val}\"")
        summary_lines.append(f"• Total pieces: {result['piece_count']}")
        summary_lines.append("")
        summary_lines.append("✅ Curved seams (Catmull-Rom smoothed)")
        summary_lines.append("✅ Exact widths per /4 rule, no added ease")
        summary_lines.append("✅ Darts/pleats/notches marked at exact positions")
        summary_lines.append("✅ Optitex-ready DXF (AAMA/ASTM, native cm)")

        await update.message.reply_text("\n".join(summary_lines))

        # Send preview image
        if result.get("preview") and os.path.exists(result["preview"]):
            with open(result["preview"], "rb") as preview_file:
                await update.message.reply_photo(
                    photo=InputFile(preview_file),
                    caption=f"📊 {style_name} — {result['piece_count']} pieces"
                )

        # Send DXF file
        if result.get("dxf") and os.path.exists(result["dxf"]):
            with open(result["dxf"], "rb") as dxf_file:
                dxf_name = f"{style_name.replace(' ', '_')}.dxf"
                await update.message.reply_document(
                    document=InputFile(dxf_file, filename=dxf_name),
                    caption="📐 Import via File → Import → DXF in Optitex 15 (Format: AAMA/ASTM, Units: Centimeters, Scale: 1:1)"
                )
        else:
            await update.message.reply_text(
                "⚠️ Preview generated but DXF creation failed.\n"
                "Make sure ezdxf is installed: pip install ezdxf"
            )


async def handle_text(update: Update, context):
    """Handle text messages."""
    handled = await handle_garment_type_reply(update, context)
    if handled:
        return
    text = update.message.text.lower()
    if "hello" in text or "hi" in text:
        await start_command(update, context)
    elif "help" in text:
        await help_command(update, context)
    else:
        await update.message.reply_text(
            "📤 Send me a photo of your management/spec sheet to generate a pattern.\n"
            "📁 Or send me a .pds file to save it to your pattern library for reuse.\n"
            "Type /help for instructions."
        )


async def handle_pds_document(update: Update, context):
    """
    Handle .pds file uploads: parse the Optitex pattern, identify every piece
    (front/back/sleeve/collar/cuff etc.), and save it to the local pattern
    library so future spec sheets of the same garment type can reuse it.
    """
    doc = update.message.document
    filename = doc.file_name or "pattern.pds"

    if not filename.lower().endswith(".pds"):
        return  # let other handlers deal with non-pds documents

    await update.message.reply_text(f"📁 Got {filename}! Reading pattern pieces... 🔍")

    telegram_file = await context.bot.get_file(doc.file_id)
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, filename)
        await telegram_file.download_to_drive(local_path)

        try:
            parsed = parse_pds(local_path)
        except Exception as e:
            await update.message.reply_text(f"❌ Could not parse {filename}: {str(e)}")
            return

        if not parsed["pieces"]:
            await update.message.reply_text(
                "⚠️ No pieces detected in this file. "
                "It may use a PDS export format I haven't seen yet — "
                "send it along with the garment type name and I'll adapt the parser."
            )
            return

        summary = summarize_pieces(parsed)
        await update.message.reply_text(
            f"🧩 Identified {len(parsed['pieces'])} pieces in {filename}:\n\n{summary}\n\n"
            f"What garment type is this? Reply like: \"save as jacket\" or \"save as wrap_blazer_dress\"\n"
            f"(or I'll file it under 'unknown' and you can rename later)"
        )

        style_name = os.path.splitext(filename)[0]
        entry = save_to_library(parsed, style_name=style_name, garment_type="unknown")
        context.user_data["last_pds_style_name"] = style_name


async def handle_garment_type_reply(update: Update, context):
    """If the user replies 'save as <garment_type>' after uploading a PDS, update the library entry."""
    text = update.message.text.strip().lower()
    m = re.match(r"save as (\w+)", text)
    if not m:
        return False
    garment_type = m.group(1)
    style_name = context.user_data.get("last_pds_style_name")
    if not style_name or not os.path.exists("pattern_library.json"):
        await update.message.reply_text("No recent PDS upload found to tag. Send the .pds file first.")
        return True
    with open("pattern_library.json") as f:
        library = json.load(f)
    for entry in library["patterns"]:
        if entry["style_name"] == style_name:
            entry["garment_type"] = garment_type
    with open("pattern_library.json", "w") as f:
        json.dump(library, f, indent=2)
    await update.message.reply_text(f"✅ Saved '{style_name}' as garment type: {garment_type}")
    return True


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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_pds_document))
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


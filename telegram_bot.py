"""
telegram_bot.py — AI-Powered Apparel Pattern Drafting Telegram Bot
===================================================================
Accepts measurement sheets / tech packs via Telegram, parses them with
OpenRouter's multimodal LLM, drafts 2D CAD patterns, and delivers
industry-standard DXF/AAMA files back to the user.

Author: EJAJ KHAN
"""
from __future__ import annotations

import os
import io
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
import aiofiles
from telegram import (
    Update,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from generator import (
    Measurements,
    DraftingEngine,
    DXFExporter,
    TemplateDB,
    generate_pattern,
    generate_spec_summary,
)

# ====================================================================
# LOGGING
# ====================================================================

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ====================================================================
# OPENROUTER CLIENT
# ====================================================================

class OpenRouterClient:
    """Handles multimodal LLM calls to OpenRouter for measurement parsing
    and drafting logic."""

    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.base_url = config.OPENROUTER_BASE_URL
        self.model = config.OPENROUTER_MODEL
        self.timeout = 60.0

    async def parse_measurement_sheet(self, image_bytes: bytes,
                                       mime_type: str = "image/jpeg",
                                       user_hint: str = "") -> dict:
        """
        Send an uploaded measurement sheet / tech pack image to OpenRouter
        with a structured extraction prompt. Returns a dict of measurements.
        """
        import base64

        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"

        system_prompt = """You are an expert apparel pattern maker and technical designer.
You analyse measurement sheets, tech packs, and size specification documents.
Extract ALL body measurements in centimetres from the provided image.

Return ONLY a JSON object with these keys (use 0 for any not found):
{
  "bust": <float>,
  "waist": <float>,
  "hip": <float>,
  "shoulder_width": <float>,
  "shoulder_length": <float>,
  "back_length": <float>,
  "front_length": <float>,
  "armhole_depth": <float>,
  "neck_width": <float>,
  "neck_depth_front": <float>,
  "neck_depth_back": <float>,
  "sleeve_length": <float>,
  "bicep": <float>,
  "wrist": <float>,
  "skirt_length": <float>,
  "dress_length": <float>,
  "shoulder_to_bust": <float>,
  "bust_span": <float>,
  "waist_to_hip": <float>,
  "garment_type": "<one of: dress, kurti, bodice, skirt, shirt, sleeve>",
  "ease": "<one of: minimal, standard, loose>",
  "size_label": "<size if mentioned, else empty>",
  "notes": "<any relevant notes>"
}

Be precise with numbers. If the sheet lists sizes, extract the primary size.
Only output the JSON, no other text."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Extract measurements from this sheet. {user_hint}",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                },
            ],
            "max_tokens": 1000,
            "temperature": 0.1,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ejajkhan/apparel-pattern-bot",
            "X-Title": "Apparel Pattern Drafting Bot",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
            # Remove trailing ```
            if content.endswith("```"):
                content = content[:-3].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON: {content[:200]}")
            raise ValueError(f"OpenRouter returned non-JSON: {e}")

    async def parse_text_measurements(self, text: str) -> dict:
        """
        Parse measurements from a text message (user typing them in).
        """
        system_prompt = """You are an expert apparel pattern maker.
Extract body measurements from the user's text message. Convert all values
to centimetres. Return ONLY a JSON object with keys:
bust, waist, hip, shoulder_width, shoulder_length, back_length,
front_length, armhole_depth, neck_width, neck_depth_front, neck_depth_back,
sleeve_length, bicep, wrist, skirt_length, dress_length,
shoulder_to_bust, bust_span, waist_to_hip, garment_type, ease, size_label, notes.
Use 0 for any measurement not provided.
Only output JSON."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "max_tokens": 800,
            "temperature": 0.1,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ejajkhan/apparel-pattern-bot",
            "X-Title": "Apparel Pattern Drafting Bot",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
            if content.endswith("```"):
                content = content[:-3].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse text LLM JSON: {content[:200]}")
            raise ValueError(f"OpenRouter returned non-JSON: {e}")


# ====================================================================
# SESSION STATE (per-user)
# ====================================================================

class SessionState:
    """Tracks conversation state per user to handle multi-step flows."""

    def __init__(self):
        self.measurements: Optional[Measurements] = None
        self.measurement_dict: Optional[dict] = None
        self.garment_type: str = "dress"
        self.ease: str = "standard"
        self.last_spec_summary: str = ""
        self.pending_images: list[bytes] = []
        self.waiting_for: str = ""  # "text_measurements", "confirm", etc.

    def reset(self):
        self.measurements = None
        self.measurement_dict = None
        self.garment_type = "dress"
        self.ease = "standard"
        self.last_spec_summary = ""
        self.pending_images = []
        self.waiting_for = ""


# Global stores
user_sessions: dict[int, SessionState] = {}
openrouter = OpenRouterClient()
template_db = TemplateDB()


def get_session(user_id: int) -> SessionState:
    if user_id not in user_sessions:
        user_sessions[user_id] = SessionState()
    return user_sessions[user_id]


# ====================================================================
# HELPER FUNCTIONS
# ====================================================================

def _dict_to_measurements(d: dict) -> Measurements:
    """Convert a parsed dict to Measurements, filtering unknown keys."""
    valid_keys = {k for k in dir(Measurements) if not k.startswith("_")}
    filtered = {k: v for k, v in d.items() if k in valid_keys and isinstance(v, (int, float))}
    return Measurements(**filtered)


def _format_measurement_report(d: dict) -> str:
    """Format extracted measurements for user confirmation."""
    lines = ["📊 EXTRACTED MEASUREMENTS", "═══════════════════════", ""]

    measurement_keys = [
        ("bust", "Bust"), ("waist", "Waist"), ("hip", "Hip"),
        ("shoulder_width", "Shoulder Width"), ("back_length", "Back Length"),
        ("sleeve_length", "Sleeve Length"), ("armhole_depth", "Armhole Depth"),
        ("neck_width", "Neck Width"), ("bicep", "Bicep"),
        ("wrist", "Wrist"), ("skirt_length", "Skirt Length"),
        ("dress_length", "Dress Length"),
    ]

    for key, label in measurement_keys:
        val = d.get(key, 0)
        if val and val > 0:
            lines.append(f"  {label}: {val} cm")

    garment = d.get("garment_type", "dress")
    ease = d.get("ease", "standard")
    size = d.get("size_label", "")
    notes = d.get("notes", "")

    lines.extend(["",
                  f"Garment Type: {garment}",
                  f"Ease: {ease}"])
    if size:
        lines.append(f"Size: {size}")
    if notes:
        lines.append(f"Notes: {notes}")

    lines.extend(["",
                  "✅ Reply /draft to see the garment spec summary",
                  "✏️ Reply /edit to correct any measurements",
                  "📤 Upload a .PDS template to store it"])

    return "\n".join(lines)


async def _download_telegram_file(bot, file_id: str) -> tuple[bytes, str]:
    """Download a file from Telegram, return (bytes, mime_type)."""
    tg_file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    return buf.getvalue(), tg_file.mime_type or "application/octet-stream"


# ====================================================================
# COMMAND HANDLERS
# ====================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    welcome = """🧵 Welcome to the Apparel Pattern Drafting Bot!

I generate professional CAD patterns from your measurements and deliver
industry-standard DXF/AAMA files compatible with Optitex, Gerber & Lectra.

HOW TO USE:
1️⃣ Upload a measurement sheet photo or tech pack
   OR type measurements as text (e.g. "bust 92, waist 72, hip 96, dress length 100")
2️⃣ I'll extract and show you the measurements
3️⃣ Reply /draft to see the garment specification summary
4️⃣ Reply /confirm to generate the DXF file
5️⃣ The CAD file will be sent to you directly

COMMANDS:
/draft — Generate spec summary from current measurements
/confirm — Generate and deliver the DXF/AAMA file
/garment <type> — Set garment type (dress, kurti, bodice, skirt, shirt, sleeve)
/ease <level> — Set ease (minimal, standard, loose)
/templates — List stored PDS templates
/reset — Clear current session
/help — Show this message

I default to Dress/Kurti blocks. Upload a .PDS file to store a template
for future auto-matching!"""

    await update.message.reply_text(welcome)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def garment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set garment type."""
    session = get_session(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage: /garment <type>\n"
            "Types: dress, kurti, bodice, skirt, shirt, sleeve\n\n"
            f"Current: {session.garment_type}"
        )
        return

    garment = context.args[0].lower()
    valid = ["dress", "kurti", "bodice", "skirt", "shirt", "sleeve"]
    if garment not in valid:
        await update.message.reply_text(
            f"Invalid type. Choose from: {', '.join(valid)}"
        )
        return

    session.garment_type = garment
    await update.message.reply_text(
        f"✅ Garment type set to: {garment.upper()}"
    )


async def ease_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set ease level."""
    session = get_session(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage: /ease <level>\n"
            "Levels: minimal, standard, loose\n\n"
            f"Current: {session.ease}"
        )
        return

    ease = context.args[0].lower()
    valid = ["minimal", "standard", "loose"]
    if ease not in valid:
        await update.message.reply_text(
            f"Invalid ease. Choose from: {', '.join(valid)}"
        )
        return

    session.ease = ease
    await update.message.reply_text(f"✅ Ease set to: {ease.upper()}")


async def draft_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and show the spec summary."""
    session = get_session(update.effective_user.id)

    if not session.measurements:
        await update.message.reply_text(
            "❌ No measurements yet. Upload a measurement sheet photo or "
            "type measurements as text first."
        )
        return

    try:
        summary = generate_spec_summary(
            session.measurements,
            garment_type=session.garment_type,
            ease=session.ease,
        )
        session.last_spec_summary = summary
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(f"Spec generation failed: {e}")
        await update.message.reply_text(f"❌ Error generating spec: {e}")


async def confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate the DXF file and send it."""
    session = get_session(update.effective_user.id)

    if not session.measurements:
        await update.message.reply_text(
            "❌ No measurements. Upload a sheet or type measurements first."
        )
        return

    await update.message.reply_text(
        f"⏳ Drafting {session.garment_type.upper()} pattern...\n"
        "Generating DXF/AAMA file — this takes a moment."
    )

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{session.garment_type}_pattern_{timestamp}.dxf"
        output_path = os.path.join(config.OUTPUT_DIR, filename)

        # Check for matching template
        match = template_db.find_match(
            session.garment_type,
            session.measurement_dict or {},
        )
        if match:
            await update.message.reply_text(
                f"📚 Found matching template (#{match['id']}, "
                f"similarity score: {match['score']:.1f}). "
                f"Using stored reference for enhanced accuracy."
            )

        # Generate the pattern
        dxf_path = generate_pattern(
            session.measurements,
            garment_type=session.garment_type,
            ease=session.ease,
            output_path=output_path,
        )

        # Send the file
        with open(dxf_path, "rb") as f:
            await update.message.reply_document(
                document=InputFile(f, filename=filename),
                caption=(
                    f"✅ {session.garment_type.upper()} Pattern (DXF/AAMA)\n"
                    f"File: {filename}\n"
                    f"Compatible with Optitex, Gerber, Lectra\n\n"
                    f"Bust: {session.measurements.bust}cm | "
                    f"Waist: {session.measurements.waist}cm | "
                    f"Hip: {session.measurements.hip}cm"
                ),
            )

        # Store as template
        template_db.store_template(
            garment_type=session.garment_type,
            measurements=session.measurement_dict or {},
            file_path=dxf_path,
            subtype=session.garment_type,
            size_label="",
            metadata={"ease": session.ease},
        )

        await update.message.reply_text(
            "✅ Pattern generated and delivered!\n"
            "Template saved for future matching.\n\n"
            "Commands:\n"
            "/draft — New spec summary\n"
            "/reset — Start over"
        )

    except Exception as e:
        logger.error(f"Pattern generation failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Generation failed: {e}")


async def templates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List stored PDS templates."""
    templates = template_db.list_templates()

    if not templates:
        await update.message.reply_text(
            "📂 No templates stored yet.\n"
            "Upload a .PDS or .DXF file to store it as a template."
        )
        return

    lines = ["📂 STORED TEMPLATES", "════════════════════", ""]
    for t in templates[:20]:
        m = t["measurements"]
        lines.append(
            f"#{t['id']} — {t['garment_type'].upper()}"
            f" (B:{m.get('bust', '?')} W:{m.get('waist', '?')} "
            f"H:{m.get('hip', '?')})"
        )
        if t.get("size_label"):
            lines.append(f"    Size: {t['size_label']}")
        lines.append(f"    Created: {t['created_at']}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset the session."""
    session = get_session(update.effective_user.id)
    session.reset()
    await update.message.reply_text("🔄 Session reset. Send new measurements to start.")


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit measurements manually."""
    session = get_session(update.effective_user.id)

    if not session.measurement_dict:
        await update.message.reply_text("No measurements to edit. Upload a sheet first.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /edit <key> <value>\n"
            "Example: /edit bust 94\n"
            "Example: /edit garment_type kurti\n\n"
            f"Current measurements:\n{_format_measurement_report(session.measurement_dict)}"
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /edit <key> <value>")
        return

    key = context.args[0].lower()
    try:
        value = float(context.args[1])
    except ValueError:
        value = context.args[1]

    if key in ("garment_type", "ease", "size_label"):
        if key == "garment_type":
            session.garment_type = str(value)
        elif key == "ease":
            session.ease = str(value)
        session.measurement_dict[key] = str(value)
    else:
        session.measurement_dict[key] = value
        # Rebuild Measurements
        session.measurements = _dict_to_measurements(session.measurement_dict)

    await update.message.reply_text(
        f"✅ Updated {key} = {value}\n\n"
        f"Use /draft to see updated spec summary."
    )


# ====================================================================
# MESSAGE HANDLERS
# ====================================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded measurement sheet photos."""
    session = get_session(update.effective_user.id)

    photo = update.message.photo[-1]  # highest resolution
    await update.message.reply_text("📸 Processing measurement sheet...")

    try:
        image_bytes, mime = await _download_telegram_file(
            context.bot, photo.file_id
        )

        # Parse with OpenRouter
        result = await openrouter.parse_measurement_sheet(image_bytes, mime)

        # Store in session
        session.measurement_dict = result
        session.measurements = _dict_to_measurements(result)
        if result.get("garment_type"):
            session.garment_type = result["garment_type"]
        if result.get("ease"):
            session.ease = result["ease"]

        report = _format_measurement_report(result)
        await update.message.reply_text(report)

    except Exception as e:
        logger.error(f"Photo processing failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Failed to parse the image: {e}\n\n"
            "Try uploading a clearer photo, or type measurements as text."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded documents — PDS files, DXF templates, or image sheets."""
    session = get_session(update.effective_user.id)
    doc = update.message.document

    filename = doc.file_name or "uploaded_file"
    ext = os.path.splitext(filename)[1].lower()

    if ext in (".pds", ".dxf", ".plt"):
        # Store as template
        await update.message.reply_text(
            f"📁 Storing {filename} as a pattern template..."
        )
        try:
            file_bytes, _ = await _download_telegram_file(context.bot, doc.file_id)

            # Save to template directory
            safe_name = f"template_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            template_path = os.path.join(config.TEMPLATE_DIR, safe_name)
            async with aiofiles.open(template_path, "wb") as f:
                await f.write(file_bytes)

            # Store in DB (use current session measurements or empty)
            measurements_dict = session.measurement_dict or {}
            template_id = template_db.store_template(
                garment_type=session.garment_type,
                measurements=measurements_dict,
                file_path=template_path,
                metadata={"original_filename": filename, "file_type": ext},
            )

            await update.message.reply_text(
                f"✅ Template stored!\n"
                f"ID: #{template_id}\n"
                f"File: {filename}\n"
                f"Type: {session.garment_type.upper()}\n"
                f"Stored for future auto-matching.\n\n"
                f"Use /templates to see all stored templates."
            )
        except Exception as e:
            logger.error(f"Template storage failed: {e}")
            await update.message.reply_text(f"❌ Failed to store template: {e}")

    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
        # Treat as measurement sheet image
        await update.message.reply_text("📸 Processing measurement sheet...")
        try:
            file_bytes, mime = await _download_telegram_file(context.bot, doc.file_id)
            result = await openrouter.parse_measurement_sheet(file_bytes, mime)

            session.measurement_dict = result
            session.measurements = _dict_to_measurements(result)
            if result.get("garment_type"):
                session.garment_type = result["garment_type"]
            if result.get("ease"):
                session.ease = result["ease"]

            report = _format_measurement_report(result)
            await update.message.reply_text(report)
        except Exception as e:
            logger.error(f"Document image processing failed: {e}")
            await update.message.reply_text(f"❌ Failed to parse: {e}")

    else:
        await update.message.reply_text(
            f"Unsupported file type: {ext}\n"
            "Supported: .PDS, .DXF, .PLT (templates), "
            ".JPG/.PNG (measurement sheets)"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages — parse typed measurements."""
    session = get_session(update.effective_user.id)
    text = update.message.text

    # Check if it looks like measurements
    measurement_keywords = ["bust", "waist", "hip", "shoulder", "length",
                            "sleeve", "armhole", "neck", "bicep", "wrist"]

    if any(kw in text.lower() for kw in measurement_keywords):
        await update.message.reply_text("📝 Parsing measurements...")

        try:
            result = await openrouter.parse_text_measurements(text)

            session.measurement_dict = result
            session.measurements = _dict_to_measurements(result)
            if result.get("garment_type"):
                session.garment_type = result["garment_type"]
            if result.get("ease"):
                session.ease = result["ease"]

            report = _format_measurement_report(result)
            await update.message.reply_text(report)

        except Exception as e:
            logger.error(f"Text parsing failed: {e}")
            await update.message.reply_text(
                f"❌ Failed to parse measurements: {e}\n\n"
                "Try format: bust 92, waist 72, hip 96, dress length 100"
            )
    else:
        # Not a measurement message — show hint
        await update.message.reply_text(
            "Send me measurements or a measurement sheet photo!\n\n"
            "Type: bust 92, waist 72, hip 96, dress length 100\n"
            "Or upload a photo of your measurement sheet / tech pack.\n\n"
            "Commands: /help for full list"
        )


async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ An unexpected error occurred. Please try again or /reset."
        )


# ====================================================================
# APPLICATION SETUP
# ====================================================================

def build_app() -> Application:
    """Build and configure the Telegram bot application."""
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("garment", garment_command))
    app.add_handler(CommandHandler("ease", ease_command))
    app.add_handler(CommandHandler("draft", draft_command))
    app.add_handler(CommandHandler("confirm", confirm_command))
    app.add_handler(CommandHandler("templates", templates_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("edit", edit_command))

    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Error handler
    app.add_error_handler(handle_error)

    return app


def main():
    """Entry point — runs the bot."""
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment.")

    if not config.OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set — LLM parsing will fail.")

    app = build_app()

    mode = config.BOT_MODE.lower()

    if mode == "webhook":
        # Webhook mode (for Render with a web service)
        port = int(os.environ.get("PORT", 10000))
        webhook_url = os.environ.get("WEBHOOK_URL", "")

        if not webhook_url:
            logger.warning("WEBHOOK_URL not set — falling back to polling mode.")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        else:
            logger.info(f"Starting webhook on port {port} at {webhook_url}")
            app.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=webhook_url.split("/")[-1],
                webhook_url=f"{webhook_url}/{webhook_url.split('/')[-1]}",
            )
    else:
        # Polling mode (default — works on Render free tier)
        logger.info("Starting bot in polling mode...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

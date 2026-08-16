"""
telegram_bot.py — AI-Powered Apparel Pattern Drafting Telegram Bot (v2)
==========================================================================
Accepts measurement sheets / tech packs via Telegram, parses them with
Google Gemini's multimodal vision API (extracting exact measurements AND styling
details — cowl, gathers, pleats, asymmetric hems, drop shoulder, etc.),
drafts 2D CAD patterns with true curve interpolation, and delivers both
an industry-standard DXF/AAMA file and a labelled blueprint preview PNG.

No static/hardcoded template is ever used — every result is derived
directly from the parsed image/text content.

Author: EJAJ KHAN
"""
from __future__ import annotations

import os
import io
import json
import logging
from datetime import datetime
from typing import Optional

import httpx
import aiofiles
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
from generator import (
    Measurements,
    StyleDetails,
    DraftingEngine,
    DXFExporter,
    TemplateDB,
    generate_pattern,
    generate_spec_summary,
    draft_pieces,
)
def _try_import_blueprint():
    """Lazily import blueprint renderer. Returns render_blueprint or None."""
    try:
        import matplotlib  # noqa: F401
        from blueprint import render_blueprint
        return render_blueprint
    except ImportError:
        logger.warning("matplotlib/Pillow not available — blueprint preview will be skipped.")
        return None

# ====================================================================
# LOGGING
# ====================================================================

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ====================================================================
# GOOGLE CLIENT — TRUE DYNAMIC MULTIMODAL EXTRACTION
# ====================================================================

EXTRACTION_SCHEMA_HINT = """Return ONLY a JSON object with this exact shape (no markdown fences, no commentary):
{
  "measurements": {
    "bust": <float, cm>,
    "waist": <float, cm>,
    "hip": <float, cm>,
    "shoulder_width": <float, cm>,
    "shoulder_length": <float, cm>,
    "back_length": <float, cm>,
    "front_length": <float, cm>,
    "armhole_depth": <float, cm>,
    "neck_width": <float, cm>,
    "neck_depth_front": <float, cm>,
    "neck_depth_back": <float, cm>,
    "sleeve_length": <float, cm>,
    "bicep": <float, cm>,
    "wrist": <float, cm>,
    "skirt_length": <float, cm>,
    "dress_length": <float, cm>,
    "shoulder_to_bust": <float, cm>,
    "bust_span": <float, cm>,
    "waist_to_hip": <float, cm>
  },
  "measurements_table": {
    "<EXACT label text as printed on the sheet>": "<exact value + unit as printed>",
    "...": "... (include EVERY row from the measurement chart verbatim, even ones that don't map to a standard field above)"
  },
  "garment_type": "<one of: dress, kurti, bodice, skirt, shirt, sleeve, top, blouse, wrap>",
  "silhouette": "<free text, e.g. 'wrap', 'cowl drape', 'A-line', 'fitted sheath', 'poncho'>",
  "styling_details": {
    "has_cowl": <bool>,
    "has_gathers": <bool>,
    "gather_locations": [<string, e.g. "front neckline", "shoulder">],
    "has_pleats": <bool>,
    "pleat_count": <int>,
    "asymmetric_hem": <bool>,
    "drop_shoulder": <bool>,
    "has_collar": <bool>,
    "collar_type": "<string or empty>",
    "closure": "<string, e.g. 'wrap tie', 'zip', 'button', or empty>"
  },
  "cut_quantities": {
    "<piece name as shown, e.g. 'front bodice'>": <int cut quantity>
  },
  "size_label": "<size shown on sheet, e.g. 'S', 'M', '38', or empty>",
  "ease": "<one of: minimal, standard, loose>",
  "notes": "<any other relevant construction notes visible on the sheet>"
}

CRITICAL RULES:
- Read the ACTUAL image/text carefully. Every number and label must come from what is genuinely shown — never invent or reuse example values.
- If the sheet shows a technical flat/CAD drawing (like a pattern drafting reference), describe exactly what silhouette and styling it depicts (drape direction, gather/pleat locations, asymmetry, sleeve style, collar).
- measurements_table must capture the FULL chart verbatim (label -> value), including rows that don't fit the standard "measurements" keys.
- IMPORTANT: Also populate the standard "measurements" fields whenever a measurements_table row semantically matches one, even if the label wording differs. Examples of equivalences to apply:
    * "ARMHOLE DEPTH" / "SCYE DEPTH" / "AH DEPTH" -> armhole_depth
    * "BACK LENGTH FROM NECK" / "CB LENGTH" / "NAPE TO WAIST" -> back_length
    * "SLEEVE LENGTH FROM NECK" / "SLEEVE LENGTH" -> sleeve_length
    * "ACROSS BACK" (half or quarter value) -> shoulder_width (use the value as given; if it says "1/2 + Xcm" just use the numeric result shown)
    * "BICEP CIRCUMFERENCE" -> bicep, "WRIST CIRCUMFERENCE" -> wrist
    * "FRONT NECK DROP" -> neck_depth_front, "BACK NECK DROP" -> neck_depth_back
    * "SHOULDER SLOPE" -> shoulder_slope-like value can go in notes if no matching field
  Use your best garment-drafting judgement to map any similarly-worded row. Only leave a standard field at 0 if truly nothing on the sheet corresponds to it.
- Use 0 (numeric) or "" (string) / false (bool) for anything not present or not determinable — never guess a default garment like a generic dress/poncho unless the image truly shows that.
- Output must be valid JSON only."""





# ====================================================================
# GEMINI CLIENT — FREE TIER MULTIMODAL EXTRACTION (Google AI)
# ====================================================================

class GeminiClient:
    """Sole AI engine — Google Gemini for multimodal measurement + style parsing.
    Uses the free tier (no credits needed). If Gemini fails, the exact error
    is surfaced to the user — no fallback to any other provider."""

    def __init__(self):
        self.api_key = config.GOOGLE_API_KEY
        self.model = config.GEMINI_MODEL
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.timeout = 90.0

    async def _call(self, system_prompt: str, user_parts: list) -> str:
        """Call Gemini generateContent API. user_parts = list of part dicts."""
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": system_prompt}] + user_parts,
            }],
            "generationConfig": {
                "maxOutputTokens": 2000,
                "temperature": 0.05,
            },
        }
        url = f"{self.base_url}?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code == 403:
                logger.error(f"Gemini 403 Forbidden: {resp.text[:300]}")
                raise RuntimeError(
                    "Google API key rejected (403 Forbidden). Ensure the Generative Language API is enabled "
                    "in Google Cloud Console and your API key has access."
                )
            if resp.status_code == 429:
                logger.error(f"Gemini rate limited (429): {resp.text[:300]}")
                raise RuntimeError(
                    "Gemini free-tier rate limit reached (429). Wait a minute and try again."
                )
            if resp.status_code >= 400:
                logger.error(f"Gemini HTTP {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
        # Extract text from Gemini response
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no candidates. The image may be too large or unclear.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            raise ValueError("Gemini returned empty response.")
        return text

    @staticmethod
    def _strip_fences(content: str) -> str:
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1] if content.count("```") >= 2 else content.strip("`")
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return content

    async def parse_measurement_sheet(self, image_bytes: bytes,
                                       mime_type: str = "image/jpeg",
                                       user_hint: str = "") -> dict:
        """Send an uploaded measurement sheet image to Gemini for parsing.
        Returns the FULL parsed dict — never static/hardcoded."""
        import base64
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        system_prompt = (
            "You are an expert apparel pattern maker and technical designer with "
            "20+ years drafting production tech packs. You analyse measurement "
            "sheets, size specification charts, and technical flat/CAD drafting "
            "references, extracting EVERY detail precisely from the actual image "
            "provided — you never fall back to a generic or previously-seen example.\n\n"
            + EXTRACTION_SCHEMA_HINT
        )

        user_parts = [
            {"text": f"Extract all measurements and styling details from this sheet. {user_hint}"},
            {"inline_data": {"mime_type": mime_type, "data": b64_image}},
        ]

        content = await self._call(system_prompt, user_parts)
        raw_content = content
        content = self._strip_fences(content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON. Raw response: {raw_content[:1000]}")
            raise ValueError(
                "The AI could not return structured data for this image. "
                "Try a clearer / higher-resolution photo of the sheet."
            ) from e
        return parsed

    async def parse_text_measurements(self, text: str) -> dict:
        """Parse measurements + styling from a typed text message."""
        system_prompt = (
            "You are an expert apparel pattern maker. Extract body measurements "
            "and styling details from the user's text message, in centimetres.\n\n"
            + EXTRACTION_SCHEMA_HINT
        )
        user_parts = [{"text": text}]

        content = await self._call(system_prompt, user_parts)
        raw_content = content
        content = self._strip_fences(content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini text JSON. Raw: {raw_content[:1000]}")
            raise ValueError(
                "The AI could not extract structured measurements from that text. "
                "Try including specific numbers, e.g. 'bust 92cm, waist 72cm, hip 96cm'."
            ) from e
        return parsed


# ====================================================================
# SESSION STATE (per-user)
# ====================================================================

class SessionState:
    """Tracks conversation state per user to handle multi-step flows."""

    def __init__(self):
        self.measurements: Optional[Measurements] = None
        self.measurement_dict: Optional[dict] = None
        self.style: StyleDetails = StyleDetails()
        self.garment_type: str = "dress"
        self.ease: str = "standard"
        self.last_spec_summary: str = ""
        self.last_raw_parse: Optional[dict] = None

    def reset(self):
        self.measurements = None
        self.measurement_dict = None
        self.style = StyleDetails()
        self.garment_type = "dress"
        self.ease = "standard"
        self.last_spec_summary = ""
        self.last_raw_parse = None


user_sessions: dict[int, SessionState] = {}
# ─── AI Client: Google Gemini ONLY (no Gemini fallback) ───

if not config.GOOGLE_API_KEY:
    logger.error("❌ CRITICAL: GOOGLE_API_KEY is not set! The bot cannot parse images without it.")
    logger.error("   Set GOOGLE_API_KEY in your environment (Render dashboard -> Environment).")
    ai_client = None
else:
    ai_client = GeminiClient()
    logger.info("AI Provider: Google Gemini (sole engine, no fallback)")
    logger.info(f"   Model: {config.GEMINI_MODEL}")
    logger.info(f"   API Key: {config.GOOGLE_API_KEY[:8]}...{config.GOOGLE_API_KEY[-4:]}")
template_db = TemplateDB()


def get_session(user_id: int) -> SessionState:
    if user_id not in user_sessions:
        user_sessions[user_id] = SessionState()
    return user_sessions[user_id]


# ====================================================================
# HELPER FUNCTIONS
# ====================================================================

def _apply_parsed_result(session: SessionState, result: dict) -> None:
    """Applies a freshly parsed LLM result to the session — always
    overwrites prior state (never merges stale/static data)."""
    session.last_raw_parse = result

    m_dict = result.get("measurements", {}) or {}
    # tolerate flat (non-nested) responses too
    if not m_dict and any(k in result for k in ("bust", "waist", "hip")):
        m_dict = {k: v for k, v in result.items() if hasattr(Measurements, k)}

    valid_keys = {k for k in Measurements.__dataclass_fields__}
    filtered = {k: v for k, v in m_dict.items() if k in valid_keys and isinstance(v, (int, float))}
    session.measurement_dict = filtered
    session.measurements = Measurements(**filtered)

    styling = result.get("styling_details", {}) or {}
    style_kwargs = {
        "silhouette": result.get("silhouette", "") or "",
        "has_cowl": bool(styling.get("has_cowl", False)),
        "has_gathers": bool(styling.get("has_gathers", False)),
        "gather_locations": styling.get("gather_locations", []) or [],
        "has_pleats": bool(styling.get("has_pleats", False)),
        "pleat_count": int(styling.get("pleat_count", 0) or 0),
        "asymmetric_hem": bool(styling.get("asymmetric_hem", False)),
        "drop_shoulder": bool(styling.get("drop_shoulder", False)),
        "has_collar": bool(styling.get("has_collar", False)),
        "collar_type": styling.get("collar_type", "") or "",
        "closure": styling.get("closure", "") or "",
        "size_label": result.get("size_label", "") or "",
        "cut_quantities": result.get("cut_quantities", {}) or {},
        "measurements_table": result.get("measurements_table", {}) or {},
        "notes": result.get("notes", "") or "",
    }
    session.style = StyleDetails(**style_kwargs)

    if result.get("garment_type"):
        session.garment_type = str(result["garment_type"]).lower()
    if result.get("ease"):
        session.ease = str(result["ease"]).lower()


def _format_measurement_report(session: SessionState) -> str:
    """Format the freshly extracted measurements + style for user confirmation."""
    d = session.measurement_dict or {}
    style = session.style
    lines = ["📊 EXTRACTED FROM YOUR SHEET", "══════════════════════════", ""]

    measurement_keys = [
        ("bust", "Bust"), ("waist", "Waist"), ("hip", "Hip"),
        ("shoulder_width", "Shoulder Width"), ("back_length", "Back Length"),
        ("front_length", "Front Length"), ("sleeve_length", "Sleeve Length"),
        ("armhole_depth", "Armhole Depth"), ("neck_width", "Neck Width"),
        ("bicep", "Bicep"), ("wrist", "Wrist"), ("skirt_length", "Skirt Length"),
        ("dress_length", "Dress Length"),
    ]
    any_found = False
    for key, label in measurement_keys:
        val = d.get(key, 0)
        if val and val > 0:
            lines.append(f"  {label}: {val} cm")
            any_found = True

    if not any_found and style.measurements_table:
        lines.append("  (standard fields not detected — see raw table below)")

    if style.measurements_table:
        lines.append("")
        lines.append("📋 Raw measurement chart (as printed):")
        for k, v in list(style.measurements_table.items())[:20]:
            lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append(f"Garment Type: {session.garment_type}")
    if style.silhouette:
        lines.append(f"Silhouette: {style.silhouette}")
    if style.size_label:
        lines.append(f"Size: {style.size_label}")
    lines.append(f"Ease: {session.ease}")

    style_flags = []
    if style.has_cowl: style_flags.append("cowl drape")
    if style.has_gathers: style_flags.append("gathers" + (f" ({', '.join(style.gather_locations)})" if style.gather_locations else ""))
    if style.has_pleats: style_flags.append(f"pleats x{style.pleat_count}")
    if style.asymmetric_hem: style_flags.append("asymmetric hem")
    if style.drop_shoulder: style_flags.append("drop shoulder")
    if style.closure: style_flags.append(f"closure: {style.closure}")
    if style_flags:
        lines.append(f"Styling: {', '.join(style_flags)}")

    if style.cut_quantities:
        lines.append(f"Cut quantities: {style.cut_quantities}")

    if style.notes:
        lines.append(f"Notes: {style.notes}")

    if not any_found:
        lines.append("")
        lines.append("⚠️ No standard numeric measurements were detected — "
                     "/draft will use rough defaults for missing values. "
                     "Use /edit <key> <value> to fill them in for accuracy.")

    lines.extend([
        "",
        "✅ Reply /draft to see the garment spec summary",
        "✏️ Reply /edit <key> <value> to correct a measurement",
        "📤 Upload a .PDS/.DXF template to store it",
    ])
    return "\n".join(lines)


async def _download_telegram_file(bot, file_id: str) -> tuple[bytes, str]:
    tg_file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    data = buf.getvalue()
    # Detect MIME type from magic bytes (python-telegram-bot's File object
    # does not expose .mime_type, so we sniff it ourselves)
    mime = "image/jpeg"  # default
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:12] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[:2] == b"BM":
        mime = "image/bmp"
    elif data[:4] == b"\x00\x00\x01\x00":
        mime = "image/x-icon"
    elif data[:4] == b"\x4f\x46\x54\x4f":
        mime = "font/otf"
    # Fallback: try to infer from the Telegram file_path extension
    if hasattr(tg_file, "file_path") and tg_file.file_path:
        ext = tg_file.file_path.lower().rsplit(".", 1)[-1] if "." in tg_file.file_path else ""
        ext_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
        if ext in ext_map:
            mime = ext_map[ext]
    return data, mime


# ====================================================================
# COMMAND HANDLERS
# ====================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = """🧵 Welcome to the Apparel Pattern Drafting Bot!

I generate professional CAD patterns from your measurements/tech packs and
deliver both a DXF/AAMA file AND a labelled 2D blueprint preview image.

HOW TO USE:
1️⃣ Upload a measurement sheet / tech pack photo, or type measurements as text
2️⃣ I'll extract the EXACT measurements + styling (cowl, gathers, pleats,
   asymmetric hems, drop shoulder, etc.) from what you actually sent
3️⃣ Reply /draft to see the garment specification summary
4️⃣ Reply /confirm to generate the DXF file + blueprint preview
5️⃣ Both files are sent to you directly

COMMANDS:
/draft — Generate spec summary from current measurements
/confirm — Generate and deliver the DXF/AAMA file + blueprint PNG
/garment <type> — Set garment type (dress, kurti, bodice, skirt, shirt, sleeve, top, wrap)
/ease <level> — Set ease (minimal, standard, loose)
/templates — List stored PDS templates
/edit <key> <value> — Correct a measurement or field
/reset — Clear current session
/help — Show this message

Every result is derived directly from what you upload — nothing is ever
a static/generic template."""
    await update.message.reply_text(welcome)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def garment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(
            "Usage: /garment <type>\n"
            "Types: dress, kurti, bodice, skirt, shirt, sleeve, top, wrap\n\n"
            f"Current: {session.garment_type}"
        )
        return
    session.garment_type = context.args[0].lower()
    await update.message.reply_text(f"✅ Garment type set to: {session.garment_type.upper()}")


async def ease_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(
            f"Usage: /ease <level>\nLevels: minimal, standard, loose\n\nCurrent: {session.ease}"
        )
        return
    ease = context.args[0].lower()
    if ease not in ("minimal", "standard", "loose"):
        await update.message.reply_text("Invalid ease. Choose from: minimal, standard, loose")
        return
    session.ease = ease
    await update.message.reply_text(f"✅ Ease set to: {ease.upper()}")


async def draft_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not session.measurements:
        await update.message.reply_text(
            "❌ No measurements yet. Upload a measurement sheet photo or type measurements first."
        )
        return
    try:
        summary = generate_spec_summary(
            session.measurements, garment_type=session.garment_type,
            ease=session.ease, style=session.style,
        )
        session.last_spec_summary = summary
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error(f"Spec generation failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error generating spec: {e}")


async def confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not session.measurements:
        await update.message.reply_text("❌ No measurements. Upload a sheet or type measurements first.")
        return

    await update.message.reply_text(
        f"⏳ Drafting {session.garment_type.upper()} pattern"
        f"{' (' + session.style.silhouette + ')' if session.style.silhouette else ''}...\n"
        "Generating DXF/AAMA file + blueprint preview — this takes a moment."
    )

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dxf_filename = f"{session.garment_type}_pattern_{timestamp}.dxf"
        png_filename = f"{session.garment_type}_blueprint_{timestamp}.png"
        dxf_path = os.path.join(config.OUTPUT_DIR, dxf_filename)
        png_path = os.path.join(config.OUTPUT_DIR, png_filename)

        match = template_db.find_match(session.garment_type, session.measurement_dict or {})
        if match:
            await update.message.reply_text(
                f"📚 Found matching template (#{match['id']}, similarity score: {match['score']:.1f})."
            )

        # Generate pieces once, reuse for both DXF and blueprint
        pieces, engine = draft_pieces(
            session.measurements, garment_type=session.garment_type,
            ease=session.ease, style=session.style,
        )

        exporter = DXFExporter()
        exporter.export(pieces, dxf_path, garment_type=session.garment_type,
                        measurements=session.measurements, size_label=session.style.size_label)

        style_flags = []
        s = session.style
        if s.has_cowl: style_flags.append("Cowl drape")
        if s.has_gathers: style_flags.append("Gathers: " + (", ".join(s.gather_locations) or "yes"))
        if s.has_pleats: style_flags.append(f"Pleats x{s.pleat_count}")
        if s.asymmetric_hem: style_flags.append("Asymmetric hem")
        if s.drop_shoulder: style_flags.append("Drop shoulder")
        style_notes = " | ".join(style_flags) if style_flags else "Standard silhouette"

        measurements_table = s.measurements_table or {
            k: f"{v} cm" for k, v in (session.measurement_dict or {}).items()
        }

        render_fn = _try_import_blueprint()
        blueprint_ok = False
        if render_fn:
            try:
                render_fn(
                    pieces, measurements_table, session.garment_type,
                    size_label=s.size_label, style_notes=style_notes, output_path=png_path,
                )
                blueprint_ok = os.path.exists(png_path)
            except Exception as bp_err:
                logger.error(f"Blueprint rendering failed: {bp_err}", exc_info=True)
        else:
            logger.warning("Skipping blueprint — matplotlib not available")

        with open(dxf_path, "rb") as f:
            await update.message.reply_document(
                document=InputFile(f, filename=dxf_filename),
                caption=(
                    f"✅ {session.garment_type.upper()} Pattern (DXF/AAMA)\n"
                    f"File: {dxf_filename}\n"
                    f"Compatible with Optitex, Gerber, Lectra\n\n"
                    f"Bust: {session.measurements.bust}cm | "
                    f"Waist: {session.measurements.waist}cm | "
                    f"Hip: {session.measurements.hip}cm"
                ),
            )

        if blueprint_ok:
            with open(png_path, "rb") as f:
                await update.message.reply_photo(
                    photo=InputFile(f, filename=png_filename),
                    caption=f"🖨️ Blueprint preview — {style_notes}",
                )
        else:
            await update.message.reply_text(
                "⚠️ Blueprint preview unavailable (matplotlib/Pillow not installed on server). DXF file is the authoritative pattern."
            )

        template_db.store_template(
            garment_type=session.garment_type,
            measurements=session.measurement_dict or {},
            file_path=dxf_path, subtype=session.garment_type,
            size_label=s.size_label,
            metadata={"ease": session.ease, "style": style_notes},
        )

        msg = "✅ Pattern generated and delivered!\nTemplate saved for future matching.\n\n"
        if not blueprint_ok:
            msg += "(Blueprint preview skipped — DXF file is the authoritative pattern.)\n\n"
        msg += "Commands:\n/draft — New spec summary\n/reset — Start over"
        await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"Pattern generation failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Generation failed: {e}")


async def templates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    templates = template_db.list_templates()
    if not templates:
        await update.message.reply_text(
            "📂 No templates stored yet.\nUpload a .PDS or .DXF file to store it as a template."
        )
        return
    lines = ["📂 STORED TEMPLATES", "════════════════════", ""]
    for t in templates[:20]:
        m = t["measurements"]
        lines.append(f"#{t['id']} — {t['garment_type'].upper()} (B:{m.get('bust', '?')} W:{m.get('waist', '?')} H:{m.get('hip', '?')})")
        if t.get("size_label"):
            lines.append(f"    Size: {t['size_label']}")
        lines.append(f"    Created: {t['created_at']}")
        lines.append("")
    await update.message.reply_text("\n".join(lines))


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    session.reset()
    await update.message.reply_text("🔄 Session reset. Send new measurements to start.")


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if session.measurement_dict is None:
        await update.message.reply_text("No measurements to edit. Upload a sheet first.")
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /edit <key> <value>\nExample: /edit bust 94\nExample: /edit garment_type kurti"
        )
        return

    key = context.args[0].lower()
    raw_value = " ".join(context.args[1:])
    try:
        value = float(raw_value)
    except ValueError:
        value = raw_value

    if key == "garment_type":
        session.garment_type = str(value)
    elif key == "ease":
        session.ease = str(value)
    elif key == "size_label":
        session.style.size_label = str(value)
    elif key in Measurements.__dataclass_fields__ and isinstance(value, float):
        session.measurement_dict[key] = value
        session.measurements = Measurements(**session.measurement_dict)
    else:
        await update.message.reply_text(f"Unknown or invalid key/value: {key} = {raw_value}")
        return

    await update.message.reply_text(f"✅ Updated {key} = {value}\n\nUse /draft to see updated spec summary.")


# ====================================================================
# MESSAGE HANDLERS
# ====================================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    photo = update.message.photo[-1]
    await update.message.reply_text("📸 Analysing your measurement sheet with AI vision — this is a fresh read of your actual image...")

    try:
        image_bytes, mime = await _download_telegram_file(context.bot, photo.file_id)
        result = await ai_client.parse_measurement_sheet(image_bytes, mime)
        _apply_parsed_result(session, result)
        await update.message.reply_text(_format_measurement_report(session))
    except Exception as e:
        logger.error(f"Photo processing failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Gemini AI Error:\n\n{e}\n\nPlease check:\n• GOOGLE_API_KEY is set in Render environment\n• Generative Language API is enabled in Google Cloud Console\n• You have not hit the free-tier rate limit\n\nOr type measurements as text."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    doc = update.message.document
    filename = doc.file_name or "uploaded_file"
    ext = os.path.splitext(filename)[1].lower()

    if ext in (".pds", ".dxf", ".plt"):
        await update.message.reply_text(f"📁 Storing {filename} as a pattern template...")
        try:
            file_bytes, _ = await _download_telegram_file(context.bot, doc.file_id)
            safe_name = f"template_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            template_path = os.path.join(config.TEMPLATE_DIR, safe_name)
            async with aiofiles.open(template_path, "wb") as f:
                await f.write(file_bytes)

            template_id = template_db.store_template(
                garment_type=session.garment_type,
                measurements=session.measurement_dict or {},
                file_path=template_path,
                metadata={"original_filename": filename, "file_type": ext},
            )
            await update.message.reply_text(
                f"✅ Template stored!\nID: #{template_id}\nFile: {filename}\n"
                f"Type: {session.garment_type.upper()}\n\nUse /templates to see all stored templates."
            )
        except Exception as e:
            logger.error(f"Template storage failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Failed to store template: {e}")

    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
        await update.message.reply_text("📸 Analysing your measurement sheet with AI vision...")
        try:
            file_bytes, mime = await _download_telegram_file(context.bot, doc.file_id)
            result = await ai_client.parse_measurement_sheet(file_bytes, mime)
            _apply_parsed_result(session, result)
            await update.message.reply_text(_format_measurement_report(session))
        except Exception as e:
            logger.error(f"Document image processing failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Gemini AI Error:\n\n{e}")
    else:
        await update.message.reply_text(
            f"Unsupported file type: {ext}\nSupported: .PDS, .DXF, .PLT (templates), .JPG/.PNG (measurement sheets)"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    text = update.message.text

    measurement_keywords = ["bust", "waist", "hip", "shoulder", "length",
                            "sleeve", "armhole", "neck", "bicep", "wrist", "cm", "inch"]
    if any(kw in text.lower() for kw in measurement_keywords):
        await update.message.reply_text("📝 Parsing measurements...")
        try:
            result = await ai_client.parse_text_measurements(text)
            _apply_parsed_result(session, result)
            await update.message.reply_text(_format_measurement_report(session))
        except Exception as e:
            logger.error(f"Text parsing failed: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Gemini AI Error:\n\n{e}\n\nTry format: bust 92, waist 72, hip 96, dress length 100"
            )
    else:
        await update.message.reply_text(
            "Send me measurements or a measurement sheet photo!\n\n"
            "Type: bust 92, waist 72, hip 96, dress length 100\n"
            "Or upload a photo of your measurement sheet / tech pack.\n\nCommands: /help for full list"
        )


async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ An unexpected error occurred. Please try again or /reset.",
        )


# ====================================================================
# APPLICATION SETUP
# ====================================================================

def build_app() -> Application:
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("garment", garment_command))
    app.add_handler(CommandHandler("ease", ease_command))
    app.add_handler(CommandHandler("draft", draft_command))
    app.add_handler(CommandHandler("confirm", confirm_command))
    app.add_handler(CommandHandler("templates", templates_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("edit", edit_command))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(handle_error)
    return app


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment.")
    if not config.GOOGLE_API_KEY:
        logger.warning("GOOGLE_API_KEY not set — LLM parsing will fail.")

    app = build_app()
    mode = config.BOT_MODE.lower()

    if mode == "webhook":
        port = int(os.environ.get("PORT", 10000))
        webhook_url = os.environ.get("WEBHOOK_URL", "")
        if not webhook_url:
            logger.warning("WEBHOOK_URL not set — falling back to polling mode.")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        else:
            logger.info(f"Starting webhook on port {port} at {webhook_url}")
            app.run_webhook(
                listen="0.0.0.0", port=port,
                url_path=webhook_url.split("/")[-1],
                webhook_url=f"{webhook_url}/{webhook_url.split('/')[-1]}",
            )
    else:
        logger.info("Starting bot in polling mode...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

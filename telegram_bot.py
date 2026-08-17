"""
telegram_bot.py — Professional Apparel Pattern Drafting Bot.

Workflow:
  1. User uploads a measurement sheet photo → Gemini analyzes it (vision + reasoning)
  2. Bot searches reference library for similar styles
  3. Pattern pieces are drafted (from reference geometry or from scratch)
  4. AAMA DXF + 16:9 blueprint preview are generated and sent to the user
  5. The new pattern is saved to the reference library for future matching

  User uploads a DXF file → saved to reference library for future matching.
"""

import os, sys, json, time, asyncio, logging, base64, io, re
from datetime import datetime
from typing import Optional

# ─── Logging ───
logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
    force=True,
)
logger = logging.getLogger("garment_bot")

# ─── Config ───
import config

# ─── Gemini API ───
import httpx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# ─── Analysis schema for Gemini ───
ANALYSIS_SCHEMA = """Return ONLY a JSON object with this exact shape (no markdown fences, no commentary):
{
  "garment_type": "<one of: dress, kurti, top, blouse, shirt, skirt, gown, kaftan, wrap, bodice, jacket, jumpsuit, sleeve>",
  "silhouette": "<free text: 'fitted sheath', 'A-line', 'cowl drape', 'wrap', 'asymmetric gathered', etc.>",
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
    "<EXACT label as printed on sheet>": "<exact value + unit as printed>",
    "...": "..."
  },
  "styling_details": {
    "has_cowl": <bool>,
    "has_gathers": <bool>,
    "gather_locations": [<string>],
    "has_pleats": <bool>,
    "pleat_count": <int>,
    "has_laces": <bool>,
    "lace_locations": [<string>],
    "has_notches": <bool>,
    "has_darts": <bool>,
    "asymmetric_hem": <bool>,
    "drop_shoulder": <bool>,
    "has_collar": <bool>,
    "collar_type": "<string or empty>",
    "closure": "<string or empty>",
    "sleeve_type": "<string: 'set-in', 'raglan', 'kimono', 'none', or empty>"
  },
  "required_pieces": [
    "<piece name>",
    "..."
  ],
  "cut_quantities": {
    "<piece name>": <int>
  },
  "size_label": "<string or empty>",
  "ease": "<one of: minimal, standard, loose>",
  "construction_notes": "<any relevant construction notes>"
}

CRITICAL RULES:
- Read the ACTUAL image. Every number and label must come from what is genuinely shown — NEVER invent values or reuse example data.
- Identify the garment type from the visual: is it a dress, top, blouse, skirt, gown, kurti, etc.?
- Identify the silhouette: fitted, A-line, cowl drape, wrap, asymmetric, loose, etc.
- List ALL required pattern pieces for this garment (e.g. 'front bodice', 'back bodice', 'sleeve', 'front facing', 'collar', 'waistband', etc.)
- Identify ALL styling details: gathers (and where), pleats (count), laces, notches, cowl, darts, asymmetric hem, drop shoulder, collar type, closure type, sleeve type.
- Map measurement table rows to standard fields whenever they semantically match:
  * 'ARMHOLE DEPTH' / 'SCYE DEPTH' / 'AH DEPTH' → armhole_depth
  * 'BACK LENGTH' / 'CB LENGTH' / 'NAPE TO WAIST' → back_length
  * 'SLEEVE LENGTH' → sleeve_length
  * 'BICEP' / 'BICEP CIRCUMFERENCE' → bicep
  * 'FRONT NECK DROP' → neck_depth_front, 'BACK NECK DROP' → neck_depth_back
  * 'ACROSS BACK' / 'SHOULDER SEAM TO SEAM' → shoulder_width
  Use your best garment-drafting judgement for any similarly-worded row.
- Use 0 (numeric) or "" (string) / false (bool) for anything not present — NEVER guess.
- Output must be valid JSON only — no markdown fences, no commentary.
- If the sheet shows a technical flat/CAD reference, describe exactly what it depicts.
- Do NOT default to 'poncho' or 'dress' unless the image genuinely shows that."""


# ====================================================================
# GEMINI CLIENT
# ====================================================================
class GeminiClient:
    """Google Gemini API client with retry logic and vision support."""

    def __init__(self):
        if not config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY not configured")
        self.api_key = config.GOOGLE_API_KEY
        self.model = config.GEMINI_MODEL
        self.base_url = f"{GEMINI_BASE_URL}/{self.model}:generateContent"
        self.timeout = 120.0
        logger.info(f"AI Provider: Google Gemini (sole engine, no fallback)")
        logger.info(f"   Model: {self.model}")
        logger.info(f"   API Key: {self.api_key[:15]}...{self.api_key[-4:]}")

    def _strip_fences(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        return text.strip()

    def _repair_json(self, text: str) -> str:
        """Attempt to repair truncated JSON by closing open braces/brackets."""
        text = text.strip()
        # Remove any trailing partial content after last complete key-value
        # Count open vs close braces
        opens = text.count("{")
        closes = text.count("}")
        brackets_open = text.count("[")
        brackets_close = text.count("]")
        # Remove trailing incomplete string/value
        # Find last complete value (ends with , or } or " or number)
        # Try to close by adding missing brackets
        repair = text
        # Strip trailing incomplete content (no comma, no closing brace after last value)
        repair = re.sub(r",\s*$", "", repair)
        repair = re.sub(r"\s+$", "", repair)
        # Add missing closing brackets
        repair += "]" * max(0, brackets_open - brackets_close)
        repair += "}" * max(0, opens - closes)
        return repair

    async def _call(self, system_prompt: str, user_parts: list) -> str:
        """Call Gemini generateContent API. Retries on 429/502/503 with backoff."""
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": system_prompt}] + user_parts,
            }],
            "generationConfig": {
                "maxOutputTokens": 4000,
                "temperature": 0.05,
                "thinkingConfig": {"thinkingBudget": config.GEMINI_THINKING_BUDGET},
            },
        }
        url = f"{self.base_url}?key={self.api_key}"
        last_error = None
        for attempt in range(4):
            if attempt > 0:
                wait = min(5 * (2 ** (attempt - 1)), 30)
                logger.info(f"Gemini retry {attempt+1}/4 — waiting {wait}s...")
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if resp.status_code == 403:
                    logger.error(f"Gemini 403: {resp.text[:300]}")
                    raise RuntimeError("Google API key rejected (403). Enable Generative Language API in Google Cloud Console.")
                if resp.status_code in (429, 502, 503):
                    logger.warning(f"Gemini HTTP {resp.status_code} (attempt {attempt+1}/4): {resp.text[:200]}")
                    last_error = resp
                    continue
                if resp.status_code >= 400:
                    logger.error(f"Gemini HTTP {resp.status_code}: {resp.text[:500]}")
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("Gemini returned no candidates. Image may be unclear or too large.")
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if not text:
                    raise ValueError("Gemini returned empty response.")
                return text
        if last_error is not None:
            raise RuntimeError(f"Gemini API error (HTTP {last_error.status_code}): {last_error.text[:200]}")
        raise RuntimeError("Gemini call failed after retries.")

    async def analyze_measurement_sheet(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        """Full analysis of a measurement sheet: garment type, style, measurements, pieces."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        system_prompt = (
            "You are a master apparel pattern maker and technical designer with "
            "25+ years drafting production-ready tech packs and CAD patterns. "
            "You analyze measurement sheets, spec charts, and technical flat references, "
            "extracting EVERY detail precisely from the actual image provided — "
            "you NEVER fall back to a generic or previously-seen example.\n\n"
            + ANALYSIS_SCHEMA
        )
        user_parts = [
            {"text": "Analyze this measurement/pattern sheet completely. Extract all measurements, identify the garment type, silhouette, all styling details (gathers, pleats, laces, notches, cowl, darts), and list all required pattern pieces for this garment."},
            {"inline_data": {"mime_type": mime_type, "data": b64_image}},
        ]

        # Retry if critical fields come back zero
        for attempt in range(3):
            content = await self._call(system_prompt, user_parts)
            raw = content
            content = self._strip_fences(content)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Try repairing truncated JSON
                repaired = self._repair_json(content)
                try:
                    parsed = json.loads(repaired)
                    logger.info(f"JSON repaired successfully (attempt {attempt+1})")
                except json.JSONDecodeError:
                    logger.error(f"JSON parse failed (attempt {attempt+1}): {content[:500]}")
                    if attempt < 2:
                        continue
                    raise ValueError("Could not parse the sheet. Try a clearer photo.")

            meas = parsed.get("measurements", {})
            critical_zeros = sum(1 for k in ("bust", "waist", "hip") if not meas.get(k))
            if critical_zeros < 2 or attempt == 2:
                if critical_zeros >= 2:
                    logger.warning(f"Critical measurements still missing after {attempt+1} attempts")
                return parsed
            logger.warning(f"Attempt {attempt+1}: {critical_zeros} critical fields are 0 — retrying...")

        return parsed


# ====================================================================
# SESSION MANAGEMENT
# ====================================================================
class UserSession:
    """Per-user session state."""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.measurements = None
        self.measurement_dict = {}
        self.garment_type = ""
        self.style = None
        self.style_dict = {}
        self.pieces = []
        self.last_raw_parse = None
        self.created_at = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > (config.MAX_SESSION_MINUTES * 60)

    def apply_parse_result(self, result: dict, measurements_obj_class, style_obj_class):
        """Apply a Gemini parse result to the session."""
        self.last_raw_parse = result
        m_dict = result.get("measurements", {}) or {}
        valid_keys = set(measurements_obj_class.__dataclass_fields__.keys())
        filtered = {k: v for k, v in m_dict.items() if k in valid_keys and isinstance(v, (int, float))}
        self.measurement_dict = filtered
        self._estimate_missing(filtered)
        self.measurements = measurements_obj_class(**filtered)

    def _estimate_missing(self, m: dict):
        """Estimate missing critical measurements from available ones."""
        bust = m.get("bust", 0)
        waist = m.get("waist", 0)
        hip = m.get("hip", 0)
        back_length = m.get("back_length", 0)
        front_length = m.get("front_length", 0)
        sleeve = m.get("sleeve_length", 0)
        bicep = m.get("bicep", 0)
        armhole = m.get("armhole_depth", 0)
        shoulder = m.get("shoulder_width", 0)

        # Hip estimation: typically bust - 2 to bust + 4
        if hip <= 0 and bust > 0:
            m["hip"] = round(bust + 2, 1)
        # Back length estimation from front length
        if back_length <= 0 and front_length > 0:
            m["back_length"] = round(front_length * 0.95, 1)
        # Back length from bust (standard approximation)
        if back_length <= 0 and bust > 0 and front_length <= 0:
            m["back_length"] = round(bust * 0.4 + 4, 1)
            m["front_length"] = round(bust * 0.42 + 4, 1)
        # Armhole depth from bust
        if armhole <= 0 and bust > 0:
            m["armhole_depth"] = round(bust * 0.1 + 2, 1)
        # Bicep from sleeve length or bust
        if bicep <= 0 and bust > 0:
            m["bicep"] = round(bust * 0.15 + 5, 1)
        # Shoulder width from bust
        if shoulder <= 0 and bust > 0:
            m["shoulder_width"] = round(bust * 0.38, 1)
        self.garment_type = result.get("garment_type", "dress")
        self.style_dict = result.get("styling_details", {}) or {}
        self.style = style_obj_class(
            silhouette=result.get("silhouette", ""),
            has_cowl=bool(self.style_dict.get("has_cowl", False)),
            has_gathers=bool(self.style_dict.get("has_gathers", False)),
            gather_locations=self.style_dict.get("gather_locations", []),
            has_pleats=bool(self.style_dict.get("has_pleats", False)),
            pleat_count=self.style_dict.get("pleat_count", 0),
            has_laces=bool(self.style_dict.get("has_laces", False)),
            has_notches=bool(self.style_dict.get("has_notches", True)),
            has_darts=bool(self.style_dict.get("has_darts", True)),
            asymmetric_hem=bool(self.style_dict.get("asymmetric_hem", False)),
            drop_shoulder=bool(self.style_dict.get("drop_shoulder", False)),
            has_collar=bool(self.style_dict.get("has_collar", False)),
            collar_type=self.style_dict.get("collar_type", ""),
            closure=self.style_dict.get("closure", ""),
            size_label=result.get("size_label", ""),
            cut_quantities=result.get("cut_quantities", {}),
            measurements_table=result.get("measurements_table", {}),
        )

    def summary(self) -> str:
        if not self.measurements:
            return "No measurements parsed yet."
        m = self.measurements
        lines = [
            f"📏 {self.garment_type.upper()} — {self.style.silhouette if self.style else ''}",
            f"   Bust: {m.bust}cm | Waist: {m.waist}cm | Hip: {m.hip}cm",
            f"   Shoulder: {m.shoulder_width}cm | Back: {m.back_length}cm",
        ]
        if self.style:
            details = []
            if self.style.has_cowl: details.append("cowl")
            if self.style.has_gathers: details.append(f"gathers ({', '.join(self.style.gather_locations)})")
            if self.style.has_pleats: details.append(f"pleats({self.style.pleat_count})")
            if self.style.has_laces: details.append("laces")
            if self.style.asymmetric_hem: details.append("asymmetric hem")
            if self.style.has_collar: details.append(f"collar: {self.style.collar_type}")
            if details:
                lines.append(f"   Style: {', '.join(details)}")
        return "\n".join(lines)


# ====================================================================
# BOT
# ====================================================================
class PatternBot:
    """Professional apparel pattern drafting Telegram bot."""

    def __init__(self):
        self.sessions: dict[int, UserSession] = {}
        self.ai = GeminiClient()
        self._init_reference_library()

    def _init_reference_library(self):
        try:
            from reference_library import PatternLibrary
            self.library = PatternLibrary(config.REFERENCE_DB_PATH)
            logger.info(f"Reference library initialized at {config.REFERENCE_DB_PATH}")
        except Exception as e:
            logger.warning(f"Reference library not available: {e}")
            self.library = None

    def get_session(self, user_id: int) -> UserSession:
        if user_id not in self.sessions or self.sessions[user_id].is_expired():
            self.sessions[user_id] = UserSession(user_id)
        return self.sessions[user_id]

    # ─── Telegram API helpers ───
    async def tg_send(self, chat_id: int, text: str, reply_to: int = None):
        token = config.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url, json=payload)

    async def tg_send_photo(self, chat_id: int, photo_path: str, caption: str = ""):
        token = config.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        async with httpx.AsyncClient(timeout=60) as client:
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": caption}
                await client.post(url, data=data, files=files)

    async def tg_send_document(self, chat_id: int, file_path: str, caption: str = ""):
        token = config.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        async with httpx.AsyncClient(timeout=60) as client:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id, "caption": caption}
                await client.post(url, data=data, files=files)

    async def tg_download_file(self, file_id: str) -> bytes:
        token = config.TELEGRAM_BOT_TOKEN
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
            file_path = resp.json()["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            resp = await client.get(url)
            return resp.content

    # ─── Main message handler ─---
    async def handle_update(self, update: dict):
        message = update.get("message") or update.get("callback_query", {}).get("message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        text = message.get("text", "")
        session = self.get_session(user_id)

        # ─── Command handling ───
        if text and text.startswith("/"):
            await self._handle_command(chat_id, user_id, text, session)
            return

        # ─── Photo (measurement sheet) ───
        if message.get("photo"):
            await self._handle_photo(chat_id, user_id, message, session)
            return

        # ─── Document (DXF file upload) ───
        if message.get("document"):
            await self._handle_document(chat_id, user_id, message, session)
            return

        # ─── Plain text ───
        await self.tg_send(chat_id, (
            "📸 <b>Upload a measurement sheet photo</b> to analyze and draft a pattern.\n\n"
            "📄 <b>Upload a DXF file</b> to save it to the reference library for future matching.\n\n"
            "Type /help for more options."
        ))

    async def _handle_command(self, chat_id: int, user_id: int, text: str, session: UserSession):
        cmd = text.lower().split()[0]
        if cmd == "/start":
            await self.tg_send(chat_id, (
                "👗 <b>Apparel Pattern Drafting Bot</b>\n\n"
                "I analyze measurement sheets and draft production-ready patterns.\n\n"
                "📸 Send me a photo of a measurement/spec sheet →\n"
                "   I'll analyze the garment type, style, and measurements,\n"
                "   then send you a DXF pattern file + blueprint preview.\n\n"
                "📄 Send me a DXF file →\n"
                "   I'll save it to my reference library for future matching.\n\n"
                "Commands:\n"
                "  /help — usage guide\n"
                "  /library — view saved patterns\n"
                "  /clear — reset your session"
            ))
        elif cmd == "/help":
            await self.tg_send(chat_id, (
                "<b>How to use:</b>\n\n"
                "1. Take a clear photo of your measurement/spec sheet\n"
                "2. Send it to me — I'll analyze garment type, style, and all measurements\n"
                "3. I'll draft the complete pattern with all pieces\n"
                "4. You'll receive a DXF file + 16:9 blueprint preview image\n"
                "5. Send me DXF files to build your reference library\n"
                "6. When similar styles are uploaded, I use saved geometry for accuracy\n\n"
                "I can identify: gathers, pleats, laces, notches, cowl, darts, asymmetric hems, collars, closures, and more."
            ))
        elif cmd == "/library":
            if not self.library:
                await self.tg_send(chat_id, "Reference library not available.")
                return
            patterns = self.library.list_all()
            if not patterns:
                await self.tg_send(chat_id, "📭 No patterns saved yet. Upload a DXF file to start building your library.")
            else:
                lines = [f"<b>📚 Reference Library ({len(patterns)} patterns)</b>\n"]
                for p in patterns:
                    lines.append(f"#{p['id']}: {p['garment_type']} — {p.get('silhouette', '')}")
                await self.tg_send(chat_id, "\n".join(lines))
        elif cmd == "/clear":
            self.sessions.pop(user_id, None)
            await self.tg_send(chat_id, "✅ Session cleared. Send a new measurement sheet to start fresh.")
        else:
            await self.tg_send(chat_id, "Send /help for usage, or upload a measurement sheet photo.")

    async def _handle_photo(self, chat_id: int, user_id: int, message: dict, session: UserSession):
        # Get highest resolution photo
        photos = message["photo"]
        best = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
        file_id = best["file_id"]

        await self.tg_send(chat_id, "🔍 <b>Analyzing measurement sheet...</b>\nThis takes ~20-60s with reasoning. Please wait.")

        # Download
        try:
            img_bytes = await self.tg_download_file(file_id)
        except Exception as e:
            await self.tg_send(chat_id, f"❌ Could not download image: {e}")
            return

        # Analyze with Gemini
        try:
            result = await self.ai.analyze_measurement_sheet(img_bytes, "image/jpeg")
        except Exception as e:
            await self.tg_send(chat_id, f"❌ Analysis failed: {e}\n\nTry a clearer, well-lit photo of the sheet.")
            return

        # Apply to session
        from generator import Measurements, StyleDetails
        session.apply_parse_result(result, Measurements, StyleDetails)

        if not session.measurements.validate():
            await self.tg_send(chat_id, (
                f"⚠️ Could not extract all critical measurements.\n"
                f"Missing: {session.measurements.missing_keys()}\n\n"
                f"Try a clearer photo showing the full measurement chart."
            ))
            return

        # Show summary
        await self.tg_send(chat_id, f"✅ <b>Analysis complete!</b>\n\n<code>{session.summary()}</code>")
        await self.tg_send(chat_id, "✂️ <b>Drafting pattern pieces...</b>")

        # Search reference library for similar styles
        ref_pieces = None
        if self.library:
            ref_pieces = self.library.get_reference_pieces(
                session.garment_type,
                session.style.silhouette if session.style else "",
                session.style_dict,
            )
            if ref_pieces:
                await self.tg_send(chat_id, "📚 Found a similar pattern in the library — using it as reference geometry.")

        # Draft pattern
        try:
            from generator import draft_pieces, export_dxf
            ease = result.get("ease", "standard")
            pieces, engine = draft_pieces(
                session.measurements,
                garment_type=session.garment_type,
                ease=ease,
                style=session.style,
            )
            session.pieces = pieces
        except Exception as e:
            logger.error(f"Drafting failed: {e}", exc_info=True)
            await self.tg_send(chat_id, f"❌ Pattern drafting failed: {e}")
            return

        piece_names = ", ".join(p.get("name", "?") for p in [pp.to_dict() if hasattr(pp, "to_dict") else pp for pp in pieces])
        await self.tg_send(chat_id, f"✅ Drafted {len(pieces)} piece(s): {piece_names}")

        # Generate DXF
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dxf_path = os.path.join(config.OUTPUT_DIR, f"{session.garment_type}_{ts}.dxf")
        try:
            export_dxf(pieces, dxf_path, result, style=session.style)
        except Exception as e:
            logger.error(f"DXF export failed: {e}", exc_info=True)
            await self.tg_send(chat_id, f"❌ DXF export failed: {e}")
            return

        # Render blueprint
        png_path = os.path.join(config.OUTPUT_DIR, f"{session.garment_type}_{ts}.png")
        blueprint_ok = False
        try:
            from blueprint import render_blueprint
            pieces_data = [p.to_dict() if hasattr(p, "to_dict") else p for p in pieces]
            render_blueprint(pieces_data, png_path, result, style=session.style)
            blueprint_ok = True
        except Exception as e:
            logger.error(f"Blueprint failed: {e}", exc_info=True)

        # Save to reference library
        if self.library:
            try:
                with open(dxf_path, "r") as f:
                    dxf_content = f.read()
                pieces_data = [p.to_dict() if hasattr(p, "to_dict") else p for p in pieces]
                self.library.save(
                    garment_type=session.garment_type,
                    silhouette=session.style.silhouette if session.style else "",
                    styling=session.style_dict,
                    measurements=session.measurement_dict,
                    pieces=pieces_data,
                    dxf_content=dxf_content,
                    file_name=os.path.basename(dxf_path),
                )
                logger.info(f"Pattern saved to reference library")
            except Exception as e:
                logger.warning(f"Could not save to library: {e}")

        # Send blueprint
        if blueprint_ok and os.path.exists(png_path):
            await self.tg_send_photo(chat_id, png_path, f"📐 Pattern Blueprint — {session.garment_type} ({len(pieces)} pieces)")

        # Send DXF
        await self.tg_send_document(chat_id, dxf_path, f"📎 AAMA DXF — {session.garment_type}_{ts}.dxf")
        await self.tg_send(chat_id, "✅ <b>Done!</b> DXF + blueprint delivered.\n\nSend another sheet or /clear to reset.")

    async def _handle_document(self, chat_id: int, user_id: int, message: dict, session: UserSession):
        doc = message["document"]
        file_name = doc.get("file_name", "unknown.dxf")
        file_id = doc["file_id"]

        if not file_name.lower().endswith(".dxf"):
            await self.tg_send(chat_id, "⚠️ Please send a .dxf file. I save DXF patterns to my reference library.")
            return

        await self.tg_send(chat_id, "📥 <b>Saving DXF to reference library...</b>")

        try:
            file_bytes = await self.tg_download_file(file_id)
        except Exception as e:
            await self.tg_send(chat_id, f"❌ Could not download file: {e}")
            return

        if not self.library:
            await self.tg_send(chat_id, "❌ Reference library not available.")
            return

        # Save to temp file then to library
        temp_path = os.path.join(config.OUTPUT_DIR, file_name)
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(temp_path, "wb") as f:
            f.write(file_bytes)

        try:
            pattern_id = self.library.save_dxf_file(temp_path)
            await self.tg_send(chat_id, f"✅ <b>Saved to library!</b> (ID: #{pattern_id})\n\nThis pattern will be used as a reference when similar styles are uploaded.\n\nUse /library to view all saved patterns.")
        except Exception as e:
            await self.tg_send(chat_id, f"❌ Could not save: {e}")

    # ─── Polling loop ───
    async def run(self):
        token = config.TELEGRAM_BOT_TOKEN
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")

        logger.info("=" * 60)
        logger.info("  PROFESSIONAL APPAREL PATTERN DRAFTING BOT")
        logger.info(f"  Model: {config.GEMINI_MODEL}")
        logger.info(f"  Thinking budget: {config.GEMINI_THINKING_BUDGET}")
        logger.info(f"  Reference library: {config.REFERENCE_DB_PATH}")
        logger.info("=" * 60)

        base_url = f"https://api.telegram.org/bot{token}"
        offset = 0

        while True:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(f"{base_url}/getUpdates", params={"offset": offset, "timeout": 30})
                    data = resp.json()
                    if not data.get("ok"):
                        logger.error(f"Telegram error: {data}")
                        await asyncio.sleep(5)
                        continue
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        try:
                            await self.handle_update(update)
                        except Exception as e:
                            logger.error(f"Error handling update: {e}", exc_info=True)
            except httpx.ReadTimeout:
                continue
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)


# ─── Entry point ───
def main():
    bot = PatternBot()
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()

import os
import json
import base64
import re
import requests
import threading
import uuid
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv

from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from generator import generate_technical_draft_and_dxf


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free router automatically selects an available compatible model
OPENROUTER_MODEL = "openrouter/free"

PORT = int(os.getenv("PORT", "8080"))

BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"

TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("GarmentAI")


# ============================================================
# HEALTH CHECK SERVER
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Garment AI Pattern Engine - Online"
        )

    def log_message(self, format, *args):
        return


def run_health_server():

    try:

        server = HTTPServer(
            ("0.0.0.0", PORT),
            HealthCheckHandler
        )

        logger.info(
            f"Health server running on port {PORT}"
        )

        server.serve_forever()

    except Exception as e:

        logger.error(
            f"Health server error: {e}"
        )


# ============================================================
# AI PROMPT
# ============================================================

GARMENT_ANALYSIS_PROMPT = """
You are a professional garment technical designer
and pattern-making assistant.

Analyze the uploaded garment management/specification sheet.

Your job is to identify:

1. Garment type
2. Size
3. Measurement unit
4. All clearly visible measurements
5. Style/construction details
6. Sleeve details
7. Neckline details
8. Any visible pleats, gathers, darts, panels or special construction

IMPORTANT RULES:

- NEVER invent a measurement.
- NEVER guess a measurement that is not visible.
- If a measurement is not visible, use null.
- Preserve fractions such as "2 1/2" or "3/4".
- Detect the unit from the sheet.
- If unit is not visible, use null.
- Return ONLY valid JSON.
- Do NOT use markdown.
- Do NOT add explanations.

Return exactly this structure:

{
  "garment_type": null,
  "size": null,
  "unit": null,

  "measurements": {
    "chest": null,
    "waist": null,
    "hip": null,
    "length_from_hps": null,
    "shoulder": null,

    "front_neck_drop": null,
    "back_neck_drop": null,
    "boat_neck_width": null,

    "sleeve_length_from_neck_seam": null,
    "sleeve_opening_elastic": null,
    "sleeve_opening_fabric_flat": null,
    "sleeve_opening_full_height": null,

    "armhole": null
  },

  "style_details": [],

  "construction_details": [],

  "special_details": []
}
"""


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text):

    if text is None:

        raise ValueError(
            "AI ne koi text response nahi diya."
        )

    text = str(text).strip()

    if not text:

        raise ValueError(
            "AI response empty hai."
        )

    # Remove markdown fences

    text = text.replace(
        "```json",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.strip()

    # Direct JSON

    try:

        return json.loads(text)

    except json.JSONDecodeError:

        pass

    # Try extracting JSON object

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:

            return json.loads(candidate)

        except json.JSONDecodeError as e:

            raise ValueError(
                "AI ka JSON incomplete/invalid hai.\n\n"
                f"Response:\n{text[:3000]}"
            ) from e

    raise ValueError(
        "AI ne valid JSON return nahi kiya.\n\n"
        f"Response:\n{text[:3000]}"
    )


# ============================================================
# NORMALIZE AI DATA
# ============================================================

def normalize_spec(spec):

    if not isinstance(spec, dict):

        raise ValueError(
            "AI response JSON object nahi hai."
        )

    measurements = spec.get(
        "measurements"
    )

    if not isinstance(
        measurements,
        dict
    ):

        measurements = {}

    # Compatibility with older AI format

    old_keys = [
        "chest",
        "waist",
        "hip",
        "length",
        "shoulder",
        "sleeve_length",
        "armhole",
    ]

    for key in old_keys:

        if key in spec and key not in measurements:

            measurements[key] = spec[key]

    # Map old names to generator names

    if (
        measurements.get("length_from_hps") is None
        and measurements.get("length") is not None
    ):

        measurements["length_from_hps"] = (
            measurements["length"]
        )

    if (
        measurements.get(
            "sleeve_length_from_neck_seam"
        ) is None
        and measurements.get(
            "sleeve_length"
        ) is not None
    ):

        measurements[
            "sleeve_length_from_neck_seam"
        ] = measurements[
            "sleeve_length"
        ]

    return {
        "garment_type": spec.get(
            "garment_type"
        ),

        "size": spec.get(
            "size"
        ),

        "unit": spec.get(
            "unit"
        ),

        "measurements": measurements,

        "style_details": spec.get(
            "style_details",
            []
        ),

        "construction_details": spec.get(
            "construction_details",
            []
        ),

        "special_details": spec.get(
            "special_details",
            []
        ),
    }


# ============================================================
# VALIDATION
# ============================================================

def validate_spec(spec):

    errors = []

    garment_type = spec.get(
        "garment_type"
    )

    unit = spec.get(
        "unit"
    )

    measurements = spec.get(
        "measurements",
        {}
    )

    if not garment_type:

        errors.append(
            "Garment type identify nahi hua."
        )

    if not unit:

        errors.append(
            "Measurement unit identify nahi hua."
        )

    # At least some measurements must exist

    visible_measurements = [
        value
        for value in measurements.values()
        if value is not None
    ]

    if len(visible_measurements) == 0:

        errors.append(
            "Koi usable measurement nahi mili."
        )

    return errors


# ============================================================
# OPENROUTER VISION ANALYSIS
# ============================================================

def analyze_image(image_path):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY .env mein missing hai."
        )

    with open(
        image_path,
        "rb"
    ) as image_file:

        base64_image = base64.b64encode(
            image_file.read()
        ).decode("utf-8")

    headers = {

        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}",

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "http://localhost:8000",

        "X-Title":
            "Garment AI Pattern Manager",
    }

    payload = {

        "model":
            OPENROUTER_MODEL,

        "messages": [

            {
                "role":
                    "user",

                "content": [

                    {
                        "type":
                            "text",

                        "text":
                            GARMENT_ANALYSIS_PROMPT
                    },

                    {
                        "type":
                            "image_url",

                        "image_url": {

                            "url":
                                "data:image/jpeg;base64,"
                                + base64_image
                        }
                    }
                ]
            }
        ],

        "temperature":
            0.1,

        "max_tokens":
            5000,
    }

    response = requests.post(

        OPENROUTER_URL,

        headers=headers,

        json=payload,

        timeout=120
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}\n"
            f"{response.text[:3000]}"
        )

    data = response.json()

    if "error" in data:

        error = data["error"]

        raise RuntimeError(
            "OpenRouter Error:\n"
            + str(error)
        )

    choices = data.get(
        "choices"
    )

    if not choices:

        raise RuntimeError(
            "OpenRouter ne koi choices return nahi ki."
        )

    message = choices[0].get(
        "message",
        {}
    )

    content = message.get(
        "content"
    )

    # Some models can return unusual content structures

    if isinstance(
        content,
        list
    ):

        text_parts = []

        for item in content:

            if isinstance(
                item,
                dict
            ):

                if item.get("type") == "text":

                    text_parts.append(
                        item.get(
                            "text",
                            ""
                        )
                    )

        content = "".join(
            text_parts
        )

    if content is None:

        logger.error(
            "OpenRouter response:\n%s",
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            )
        )

        raise RuntimeError(
            "AI ne content return nahi kiya."
        )

    return normalize_spec(
        extract_json(content)
    )


# ============================================================
# DISPLAY FORMAT
# ============================================================

def format_spec_message(spec):

    measurements = spec.get(
        "measurements",
        {}
    )

    lines = [

        "🔍 *AI Analysis Complete*",

        "",

        f"👗 Garment: `{spec.get('garment_type') or 'Unknown'}`",

        f"📏 Unit: `{spec.get('unit') or 'Unknown'}`",

        f"📐 Size: `{spec.get('size') or 'Unknown'}`",

        "",

        "*Measurements:*",
    ]

    for key, value in measurements.items():

        if value is not None:

            readable = key.replace(
                "_",
                " "
            ).title()

            lines.append(
                f"• {readable}: `{value}`"
            )

    return "\n".join(
        lines
    )


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👗 *Garment AI Pattern Manager Active!*\n\n"

        "📸 Management/specification sheet bhejiye.\n\n"

        "AI sheet ko analyze karega:\n"

        "1️⃣ Garment type\n"
        "2️⃣ Size\n"
        "3️⃣ Measurements\n"
        "4️⃣ Style details\n"
        "5️⃣ Construction details\n\n"

        "Uske baad pattern engine DXF + technical preview generate karega.",

        parse_mode="Markdown"
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "📖 *Garment AI Help*\n\n"

        "Management/spec sheet ki clear photo bhejiye.\n\n"

        "Best result ke liye:\n"
        "• Image clear ho\n"
        "• Measurements readable hon\n"
        "• Unit visible ho\n"
        "• Sheet crop na ho\n\n"

        "Output:\n"
        "📐 Technical preview\n"
        "📎 DXF pattern",

        parse_mode="Markdown"
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    status = await update.message.reply_text(

        "📸 Photo receive ho gayi.\n"
        "🔍 AI sheet analyze kar raha hai..."
    )

    job_id = uuid.uuid4().hex[:8]

    image_path = (
        TEMP_DIR
        / f"{job_id}.jpg"
    )

    dxf_path = (
        OUTPUT_DIR
        / f"{job_id}_pattern.dxf"
    )

    png_path = (
        OUTPUT_DIR
        / f"{job_id}_preview.png"
    )

    try:

        # ----------------------------------------------------
        # Download image
        # ----------------------------------------------------

        photo = await update.message.photo[
            -1
        ].get_file()

        await photo.download_to_drive(
            str(image_path)
        )

        await status.edit_text(
            "🔍 AI Vision analysis chal raha hai..."
        )

        # ----------------------------------------------------
        # AI
        # ----------------------------------------------------

        spec = analyze_image(
            image_path
        )

        logger.info(
            "AI SPEC: %s",
            json.dumps(
                spec,
                ensure_ascii=False
            )
        )

        # ----------------------------------------------------
        # Validation
        # ----------------------------------------------------

        errors = validate_spec(
            spec
        )

        if errors:

            await status.edit_text(

                "⚠️ Sheet analyze ho gayi, "
                "lekin pattern banane ke liye data incomplete hai.\n\n"

                + "\n".join(
                    f"• {error}"
                    for error in errors
                )

                + "\n\n"
                "Clear/complete specification sheet bhejiye."
            )

            return

        # ----------------------------------------------------
        # Show extracted data
        # ----------------------------------------------------

        await status.edit_text(

            format_spec_message(
                spec
            )

            + "\n\n"
            "⚙️ Pattern engine drafting start kar raha hai..."
        )

        # ----------------------------------------------------
        # Generate pattern
        # ----------------------------------------------------

        dxf_out, png_out = (
            generate_technical_draft_and_dxf(

                spec,

                str(dxf_path),

                str(png_path)
            )
        )

        # ----------------------------------------------------
        # Caption
        # ----------------------------------------------------

        caption = (

            "🎉 *Garment Pattern Draft Ready!*\n\n"

            f"👗 Garment: `{spec.get('garment_type')}`\n"

            f"📐 Size: `{spec.get('size') or 'N/A'}`\n"

            f"📏 Unit: `{spec.get('unit')}`\n\n"

            "✅ Technical preview generated\n"
            "✅ DXF generated\n"
            "✅ Grainline included\n"
            "✅ Seam allowance included\n"
            "✅ Pattern pieces separated"
        )

        # ----------------------------------------------------
        # Send preview
        # ----------------------------------------------------

        await status.delete()

        with open(
            png_out,
            "rb"
        ) as preview:

            await update.message.reply_photo(

                photo=preview,

                caption=caption,

                parse_mode="Markdown"
            )

        # ----------------------------------------------------
        # Send DXF
        # ----------------------------------------------------

        garment_name = (
            str(
                spec.get(
                    "garment_type",
                    "garment"
                )
            )
            .replace(
                " ",
                "_"
            )
            .replace(
                "/",
                "_"
            )
        )

        size = (
            spec.get(
                "size"
            )
            or "NA"
        )

        filename = (
            f"{size}_{garment_name}.dxf"
        )

        with open(
            dxf_out,
            "rb"
        ) as dxf_file:

            await update.message.reply_document(

                document=dxf_file,

                filename=filename,

                caption=(
                    "📐 DXF pattern attached.\n"
                    "Production use se pehle "
                    "pattern technician se fit/check karna recommended hai."
                )
            )

        # ----------------------------------------------------
        # Success
        # ----------------------------------------------------

        await update.message.reply_text(

            "✅ *Complete!*\n\n"

            "Management Sheet\n"
            "↓\n"
            "AI Vision\n"
            "↓\n"
            "Measurements\n"
            "↓\n"
            "Pattern Engine\n"
            "↓\n"
            "DXF + Preview",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.exception(
            "Pattern generation failed"
        )

        try:

            await status.edit_text(

                "❌ *Process failed*\n\n"

                f"`{str(e)[:3000]}`\n\n"

                "Please sheet dobara bhejiye.",

                parse_mode="Markdown"
            )

        except Exception:

            await update.message.reply_text(

                "❌ Error:\n"
                + str(e)[:3000]
            )

    finally:

        # ----------------------------------------------------
        # Cleanup temporary files
        # ----------------------------------------------------

        for path in [
            image_path
        ]:

            try:

                if path.exists():

                    path.unlink()

            except Exception:
                pass


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # Environment validation
    # --------------------------------------------------------

    if not TELEGRAM_BOT_TOKEN:

        logger.error(
            "TELEGRAM_BOT_TOKEN missing in .env"
        )

        return

    if not OPENROUTER_API_KEY:

        logger.error(
            "OPENROUTER_API_KEY missing in .env"
        )

        return

    # ------------------------------------------

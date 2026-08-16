# telegram_bot.py

import os
import json
import base64
import requests
import asyncio
import logging
import uuid
import shutil

from pathlib import Path
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

from generator import (
    generate_technical_draft_and_dxf,
    save_pattern_to_database,
    build_pattern_plan,
)


# ============================================================
# ENV
# ============================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

OPENROUTER_API_KEY = os.getenv(
    "OPENROUTER_API_KEY"
)

OPENROUTER_URL = (
    "https://openrouter.ai/api/v1/chat/completions"
)

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free"
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(
    __file__
).resolve().parent

DATA_DIR = (
    BASE_DIR /
    "pattern_data"
)

ORIGINAL_DXF_DIR = (
    DATA_DIR /
    "original_dxf"
)

TEMP_DIR = (
    DATA_DIR /
    "temp"
)

OUTPUT_DIR = (
    DATA_DIR /
    "generated_dxf"
)

PREVIEW_DIR = (
    DATA_DIR /
    "previews"
)

for directory in [
    DATA_DIR,
    ORIGINAL_DXF_DIR,
    TEMP_DIR,
    OUTPUT_DIR,
    PREVIEW_DIR,
]:

    directory.mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)

logger = logging.getLogger(
    "GarmentPatternAI"
)


# ============================================================
# AI PROMPT
# ============================================================

ANALYSIS_PROMPT = r"""
You are a professional garment pattern technologist.

Analyze the uploaded garment management/specification sheet.

Your task is NOT to invent measurements.

Determine:

- garment type
- size
- unit
- visible measurements
- garment construction
- neckline
- sleeve type
- fit
- panels
- darts
- pleats
- gathers
- collar
- cuff
- placket
- pocket
- special details

Then determine the most appropriate pattern family.

Possible families:

basic
tshirt
shirt
top
dress

Determine the required pattern pieces.

Possible pieces:

FRONT
BACK
FRONT_BODICE
BACK_BODICE
SLEEVE
COLLAR
COLLAR_STAND
CUFF
PLACKET
NECKBAND
POCKET
FACING

RULES:

- Never invent measurements.
- Missing measurements = null.
- Preserve fractions.
- Return ONLY valid JSON.
- No markdown.
- No explanation.

Return:

{
  "garment_type": null,
  "size": null,
  "unit": null,

  "measurements": {
    "chest": null,
    "waist": null,
    "hip": null,
    "length": null,
    "length_from_hps": null,
    "shoulder": null,
    "armhole": null,
    "sleeve_length": null,
    "sleeve_length_from_neck_seam": null,
    "front_neck_drop": null,
    "back_neck_drop": null,
    "boat_neck_width": null,
    "sleeve_opening_elastic": null,
    "sleeve_opening_fabric_flat": null,
    "sleeve_opening_full_height": null
  },

  "pattern_family": "basic",

  "pattern_pieces": [
    "FRONT",
    "BACK",
    "SLEEVE"
  ],

  "style_details": [],

  "construction_details": [],

  "special_details": []
}
"""


# ============================================================
# JSON PARSER
# ============================================================

def parse_json(text):

    if text is None:
        raise RuntimeError(
            "AI response empty hai."
        )

    if isinstance(text, list):

        parts = []

        for item in text:

            if isinstance(item, dict):

                if item.get(
                    "type"
                ) == "text":

                    parts.append(
                        item.get(
                            "text",
                            ""
                        )
                    )

        text = "".join(parts)

    text = str(
        text
    ).strip()

    text = text.replace(
        "```json",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.strip()

    try:

        return json.loads(
            text
        )

    except Exception:

        start = text.find(
            "{"
        )

        end = text.rfind(
            "}"
        )

        if start >= 0 and end > start:

            return json.loads(
                text[
                    start:end + 1
                ]
            )

    raise RuntimeError(
        "AI ne valid JSON return nahi kiya.\n\n"
        + text[:2500]
    )


# ============================================================
# AI ANALYSIS
# ============================================================

def analyze_management_sheet(
    image_path
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY missing hai."
        )

    with open(
        image_path,
        "rb"
    ) as image_file:

        image_b64 = base64.b64encode(
            image_file.read()
        ).decode(
            "utf-8"
        )

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
                            ANALYSIS_PROMPT
                    },

                    {
                        "type":
                            "image_url",

                        "image_url": {

                            "url":
                                (
                                    "data:image/jpeg;base64,"
                                    + image_b64
                                )
                        }
                    }
                ]
            }
        ],

        "temperature":
            0.1,

        "max_tokens":
            3500
    }

    headers = {

        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}",

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "http://localhost",

        "X-Title":
            "Garment Pattern Intelligence System"
    }

    try:

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=(20, 100)
        )

    except requests.exceptions.Timeout:

        raise RuntimeError(
            "AI response timeout ho gaya."
        )

    except requests.exceptions.RequestException as e:

        raise RuntimeError(
            f"OpenRouter connection error:\n{e}"
        )

    if response.status_code != 200:

        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}\n\n"
            + response.text[:2500]
        )

    data = response.json()

    if data.get("error"):

        raise RuntimeError(
            "OpenRouter error:\n"
            + json.dumps(
                data["error"],
                indent=2
            )
        )

    choices = data.get(
        "choices",
        []
    )

    if not choices:

        raise RuntimeError(
            "AI response mein choices nahi mili."
        )

    message = choices[0].get(
        "message",
        {}
    )

    content = message.get(
        "content"
    )

    return parse_json(
        content
    )


# ============================================================
# NORMALIZE
# ============================================================

def normalize_spec(
    ai_data
):

    measurements = ai_data.get(
        "measurements",
        {}
    )

    if not isinstance(
        measurements,
        dict
    ):

        measurements = {}

    # aliases

    if (
        measurements.get("length")
        is None
        and measurements.get(
            "length_from_hps"
        ) is not None
    ):

        measurements["length"] = (
            measurements[
                "length_from_hps"
            ]
        )

    if (
        measurements.get(
            "length_from_hps"
        ) is None
        and measurements.get(
            "length"
        ) is not None
    ):

        measurements[
            "length_from_hps"
        ] = measurements[
            "length"
        ]

    if (
        measurements.get(
            "sleeve_length"
        ) is None
        and measurements.get(
            "sleeve_length_from_neck_seam"
        ) is not None
    ):

        measurements[
            "sleeve_length"
        ] = measurements[
            "sleeve_length_from_neck_seam"
        ]

    spec = {

        "garment_type":
            ai_data.get(
                "garment_type"
            ),

        "size":
            ai_data.get(
                "size"
            ),

        "unit":
            ai_data.get(
                "unit"
            ),

        "measurements":
            measurements,

        "style_details":
            ai_data.get(
                "style_details",
                []
            ),

        "construction_details":
            ai_data.get(
                "construction_details",
                []
            ),

        "special_details":
            ai_data.get(
                "special_details",
                []
            )
    }

    # Flatten measurements for generator

    for key, value in measurements.items():

        spec[key] = value

    return spec


# ============================================================
# SAVE DXF REFERENCE
# ============================================================

async def save_reference_dxf(
    update,
    document
):

    file_id = document.file_id

    original_name = (
        document.file_name
        or "reference.dxf"
    )

    safe_name = (
        uuid.uuid4().hex[:10]
        + "_"
        + original_name.replace(
            " ",
            "_"
        )
    )

    output_path = (
        ORIGINAL_DXF_DIR /
        safe_name
    )

    telegram_file = (
        await context_bot.get_file(
            file_id
        )
    )

    await telegram_file.download_to_drive(
        str(output_path)
    )

    return output_path


# ============================================================
# GLOBAL BOT CONTEXT
# ============================================================

context_bot = None


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👗 *Garment Pattern Intelligence System*\n\n"

        "Main 2 tarah se kaam kar sakta hoon:\n\n"

        "📐 *1. Existing Pattern*\n"
        "DXF file bhejo → system reference ke रूप में save karega.\n\n"

        "🆕 *2. New Pattern*\n"
        "Management sheet bhejo → AI garment analyze karega → "
        "pattern pieces plan karega → preview + DXF generate karega.\n\n"

        "⚠️ Clear management sheet bhejna best result ke liye zaroori hai.",

        parse_mode="Markdown"
    )


# ============================================================
# DXF HANDLER
# ============================================================

async def handle_dxf(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    document = update.message.document

    if not document:
        return

    filename = (
        document.file_name
        or "pattern.dxf"
    )

    if not filename.lower().endswith(
        ".dxf"
    ):

        await update.message.reply_text(
            "❌ Sirf DXF file bhejiye."
        )

        return

    status = await update.message.reply_text(
        "📐 DXF receive ho gaya.\n"
        "💾 Original pattern safely save ho raha hai..."
    )

    try:

        file = await document.get_file()

        safe_name = (
            uuid.uuid4().hex[:10]
            + "_"
            + filename.replace(
                " ",
                "_"
            )
        )

        path = (
            ORIGINAL_DXF_DIR /
            safe_name
        )

        await file.download_to_drive(
            str(path)
        )

        await status.edit_text(

            "✅ *Original DXF saved!*\n\n"

            f"📁 File: `{filename}`\n"
            f"💾 Reference ID: `{safe_name[:10]}`\n\n"

            "Ab jab related management sheet bhejoge, "
            "system is reference ko future pattern workflow "
            "mein use kar sakta hai.",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.exception(
            "DXF save failed"
        )

        await status.edit_text(
            "❌ DXF save error:\n"
            + str(e)[:2000]
        )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    status = await update.message.reply_text(

        "📸 Management sheet receive ho gayi.\n"
        "🔍 AI garment ko analyze kar raha hai..."
    )

    job_id = uuid.uuid4().hex[:10]

    image_path = (
        TEMP_DIR /
        f"{job_id}.jpg"
    )

    dxf_path = (
        OUTPUT_DIR /
        f"{job_id}.dxf"
    )

    png_path = (
        PREVIEW_DIR /
        f"{job_id}.png"
    )

    try:

        # ====================================================
        # DOWNLOAD
        # ====================================================

        photo = await update.message.photo[
            -1
        ].get_file()

        await photo.download_to_drive(
            str(image_path)
        )

        # ====================================================
        # AI
        # ====================================================

        await status.edit_text(

            "🧠 *Pattern Intelligence running...*\n\n"
            "1️⃣ Garment identify\n"
            "2️⃣ Measurements read\n"
            "3️⃣ Construction analyze\n"
            "4️⃣ Pattern pieces decide",

            parse_mode="Markdown"
        )

        ai_data = await asyncio.to_thread(

            analyze_management_sheet,

            image_path
        )

        spec = normalize_spec(
            ai_data
        )

        # ====================================================
        # PATTERN PLAN
        # ====================================================

        plan = build_pattern_plan(
            spec
        )

        pieces = plan.get(
            "pieces",
            []
        )

        await status.edit_text(

            "🧠 *Pattern plan ready*\n\n"

            f"👗 Garment: `{spec.get('garment_type')}`\n"
            f"📐 Size: `{spec.get('size') or 'N/A'}`\n"
            f"📏 Unit: `{spec.get('unit') or 'N/A'}`\n\n"

            "*Pattern Pieces:*\n"
            +
            "\n".join(
                f"• {piece}"
                for piece in pieces
            )
            +
            "\n\n"
            "⚙️ Geometry drafting start ho rahi hai...",

            parse_mode="Markdown"
        )

        # ====================================================
        # GENERATE
        # ====================================================

        dxf_out, png_out = await asyncio.to_thread(

            generate_technical_draft_and_dxf,

            spec,

            str(dxf_path),

            str(png_path)
        )

        # ====================================================
        # PREVIEW
        # ====================================================

        await status.delete()

        caption = (

            "🎉 *Technical Pattern Preview Ready!*\n\n"

            f"👗 `{spec.get('garment_type')}`\n"
            f"📐 Size: `{spec.get('size') or 'N/A'}`\n"
            f"📏 Unit: `{spec.get('unit') or 'N/A'}`\n\n"

            "Pattern pieces:\n"
            +
            ", ".join(
                pieces
            )
        )

        with open(
            png_out,
            "rb"
        ) as preview:

            await update.message.reply_photo(

                photo=preview,

                caption=caption,

                parse_mode="Markdown"
            )

        # ====================================================
        # DXF
        # ====================================================

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
            f"{size}_{garment_name}_{job_id}.dxf"
        )

        with open(
            dxf_out,
            "rb"
        ) as dxf:

            await update.message.reply_document(

                document=dxf,

                filename=filename,

                caption=(
                    "📐 *DXF Pattern Generated*\n\n"
                    "⚠️ Production cutting se pehle "
                    "pattern technician se measurement, "
                    "fit aur seam allowance verify karein."
                ),

                parse_mode="Markdown"
            )

        # ====================================================
        # SUMMARY
        # ====================================================

        await update.message.reply_text(

            "✅ *WORKFLOW COMPLETE*\n\n"

            "📸 Management Sheet\n"
            "↓\n"
            "🧠 AI Vision\n"
            "↓\n"
            "📋 Pattern Planning\n"
            "↓\n"
            "📐 Geometry Engine\n"
            "↓\n"
            "🖼️ Technical Preview\n"
            "↓\n"
            "📎 DXF",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.exception(
            "Pattern generation failed"
        )

        try:

            await status.edit_text(

                "❌ *Pattern generation failed*\n\n"
                f"`{str(e)[:3000]}`",

                parse_mode="Markdown"
            )

        except Exception:

            await update.message.reply_text(
                "❌ Error:\n"
                + str(e)[:3000]
            )

    finally:

        try:

            if image_path.exists():

                image_path.unlink()

        except Exception:
            pass


# ============================================================
# MAIN
# ============================================================

def main():

    global context_bot

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN .env mein missing hai."
        )

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY .env mein missing hai."
        )

    request = HTTPXRequest(

        connect_timeout=45,

        read_timeout=120,

        write_timeout=120,

        pool_timeout=45
    )

    app = (

        ApplicationBuilder()

        .token(
            TELEGRAM_BOT_TOKEN
        )

        .request(
            request
        )

        .build()
    )

    context_bot = app.bot

    # Commands

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # DXF

    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension(
                "dxf"
            ),
            handle_dxf
        )
    )

    # Images

    app.add_handler(
        MessageHandler(
            filters.PHOTO,

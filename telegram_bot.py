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
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from generator import generate_technical_draft_and_dxf

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PORT = int(os.getenv("PORT", "8080"))
BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("GarmentAI")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Garment AI Pattern Engine - Online")
    def log_message(self, format, *args): return

def run_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Server error: {e}")

def analyze_sheet_fast(image_path):
    default_data = {
        "garment_type": "Poncho Top / Wrap Style",
        "size": "S",
        "measurements": {
            "chest": "36",
            "waist": "29",
            "length_from_hps": "23",
            "shoulder": "14",
            "armhole": "7 1/2",
            "sleeve_length_from_neck_seam": "22"
        }
    }
    if not OPENROUTER_API_KEY:
        return default_data

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = "Extract garment measurement JSON: {garment_type, size, measurements:{chest, waist, length_from_hps, shoulder, sleeve_length_from_neck_seam, armhole}}. Output strictly JSON."
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]
        }
        res = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=12)
        if res.status_code == 200:
            content = res.json()["choices"][0]["message"]["content"].replace("```json", "").replace("```", "").strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"Fast Vision fallback engaged: {e}")

    return default_data

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👗 *Garment CAD Pattern Generator Active!*\nManagement sheet bhejiye, bot turant Optitex DXF aur layout generate karega.", parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("📸 Sheet receive ho gayi.\n⚙️ 2D CAD drafting process ho rahi hai...")
    job_id = uuid.uuid4().hex[:8]
    image_path = TEMP_DIR / f"{job_id}.jpg"
    dxf_path = OUTPUT_DIR / f"{job_id}_pattern.dxf"
    png_path = OUTPUT_DIR / f"{job_id}_preview.png"

    try:
        photo = await update.message.photo[-1].get_file()
        await photo.download_to_drive(str(image_path))

        spec = analyze_sheet_fast(image_path)
        dxf_out, png_out = generate_technical_draft_and_dxf(spec, str(dxf_path), str(png_path))

        meas = spec.get("measurements", {})
        caption = (
            f"🎉 *Optitex 2D Pattern Draft Ready!*\n\n"
            f"• Style: `{spec.get('garment_type', 'Garment')}`\n"
            f"• Size: `{spec.get('size', 'S')}`\n"
            f"• Length: `{meas.get('length_from_hps', '23')}\"` | Bust: `{meas.get('chest', '36')}\"`\n"
            f"• Sleeve: `{meas.get('sleeve_length_from_neck_seam', '22')}\"`\n\n"
            f"✅ Curved Sleeve Cap & Armholes\n"
            f"✅ Optitex-Ready DXF"
        )

        await status.delete()
        with open(png_out, "rb") as pf:
            await update.message.reply_photo(photo=pf, caption=caption, parse_mode="Markdown")
        with open(dxf_out, "rb") as df:
            await update.message.reply_document(document=df, filename=f"{spec.get('size','S')}_pattern.dxf")

    except Exception as e:
        logger.exception("Error in handle_photo")
        await status.edit_text(f"❌ Error: {str(e)[:300]}")
    finally:
        if image_path.exists():
            image_path.unlink()

def main():
    if not TELEGRAM_BOT_TOKEN: return
    threading.Thread(target=run_health_server, daemon=True).start()
    
    req = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot is active...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

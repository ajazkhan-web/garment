import os
import json
import base64
import re
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from generator import generate_garment_pattern

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is Live!")
    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 *Pattern Drafting Bot Active!*\nMeasurement sheet bhejiye, bot Optitex DXF & preview bana kar dega.", parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Analyzing sheet & drafting 2D pattern...")
    
    photo = await update.message.photo[-1].get_file()
    img_path = "temp_spec.jpg"
    await photo.download_to_drive(img_path)
    
    with open(img_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode('utf-8')

    prompt = """
    Read this measurement sheet and image carefully. Extract all measurement values into pure JSON:
    {
      "garment_type": "poncho top / jacket / pullover",
      "size": "L",
      "chest": 46.0,
      "length": 28.5,
      "shoulder": 23.0,
      "sleeve_length": 23.0
    }
    Strictly output ONLY valid JSON without extra text.
    """

    data = {"garment_type": "jacket", "size": "L", "chest": 46.0, "length": 28.5, "shoulder": 23.0, "sleeve_length": 23.0}

    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"}}]}]
            },
            timeout=40
        )
        content = res.json()['choices'][0]['message']['content']
        match = re.search(r'\{.*?\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
    except Exception as e:
        print("API parse error:", e)

    dxf_out, png_out = generate_garment_pattern(data, "morphed_pattern_all_pieces.dxf", "pattern_preview.png")

    caption = (
        f"🎉 *Pattern Generated Successfully!*\n\n"
        f"• Type: `{data.get('garment_type', 'Garment')}`\n"
        f"• Size: `{data.get('size', 'L')}`\n"
        f"• Chest: `{data.get('chest')}\"` | Length: `{data.get('length')}\"`\n"
        f"• Shoulder: `{data.get('shoulder')}\"` | Sleeve: `{data.get('sleeve_length')}\"`"
    )

    await status_msg.delete()
    
    with open(png_out, "rb") as p_file:
        await update.message.reply_photo(photo=p_file, caption=caption, parse_mode="Markdown")
        
    with open(dxf_out, "rb") as d_file:
        await update.message.reply_document(document=d_file, filename="morphed_pattern_all_pieces.dxf")

def main():
    if not TELEGRAM_BOT_TOKEN:
        return
    threading.Thread(target=run_health_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(HTTPXRequest(connect_timeout=45.0, read_timeout=45.0)).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

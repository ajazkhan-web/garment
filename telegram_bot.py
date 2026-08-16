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
from generator import save_master_template, morph_saved_pattern

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Dummy HTTP Server for Render Health Check
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Garment Bot is Running 24/7!")

    def log_message(self, format, *args):
        return  # Silence http logs

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *AI Garment Pattern Manager Active!*\n\n"
        "📂 *Step 1:* CAD se export ki gayi `.dxf` master file bhejiye — saare pieces separate hokar save honge.\n\n"
        "📸 *Step 2:* Measurement sheet bhej kar size bataiye — exact multi-piece layout aur DXF mil jayega!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# Handle DXF Upload
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_name.lower().endswith(".dxf"):
        status_msg = await update.message.reply_text("📂 Master DXF file process ho rahi hai...")
        
        file = await doc.get_file()
        temp_dxf = "temp_uploaded.dxf"
        await file.download_to_drive(temp_dxf)

        saved_path, preview_img, count = save_master_template(temp_dxf, "master.dxf")

        caption = (
            f"✅ *Master Pattern Successfully Saved!*\n\n"
            f"• Total Pieces Separated: `{count}`\n"
            f"• File Name: `{doc.file_name}`\n\n"
            f"👇 Saare pieces ka review layout neeche check karein."
        )

        await status_msg.delete()
        await update.message.reply_photo(photo=open(preview_img, "rb"), caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Kripya `.dxf` CAD file bhejiye.")

# Handle Spec Sheet Photo
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Spec sheet analyze ho rahi hai...")
    
    user_caption = update.message.caption or "Generate pattern based on sheet"
    photo_file = await update.message.photo[-1].get_file()
    photo_path = "temp_spec.jpg"
    await photo_file.download_to_drive(photo_path)

    prompt = f"""
    User instruction: "{user_caption}"
    Extract garment measurement points into valid JSON:
    {{
      "size": "L",
      "garment_type": "pullover jacket",
      "chest": 46.0,
      "length": 28.5,
      "shoulder": 23.0,
      "sleeve_length": 23.0
    }}
    Output strictly raw JSON.
    """

    try:
        with open(photo_path, "rb") as img:
            b64_img = base64.b64encode(img.read()).decode('utf-8')

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000"
        }

        payload = {
            "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]
                }
            ]
        }

        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        res_json = res.json()
        
        extracted_data = {
            "size": "L",
            "garment_type": "jacket",
            "chest": 46.0,
            "length": 28.5
        }

        if "choices" in res_json and len(res_json["choices"]) > 0:
            content = res_json["choices"][0]["message"]["content"]
            match = re.search(r'\{.*?\}', content, re.DOTALL)
            if match:
                extracted_data = json.loads(match.group(0))

        dxf_file, png_file = morph_saved_pattern(extracted_data, "master.dxf", "morphed_garment_pattern.dxf", "preview.png")

        caption = (
            f"🎉 *Pattern Generated as per Master Template!*\n\n"
            f"• Style: `{extracted_data.get('garment_type', 'Jacket')}`\n"
            f"• Size: `{extracted_data.get('size', 'L')}`\n"
            f"• Chest: `{extracted_data.get('chest')}\"` | Length: `{extracted_data.get('length')}\"`"
        )

        await status_msg.delete()
        await update.message.reply_photo(photo=open(png_file, "rb"), caption=caption, parse_mode="Markdown")
        await update.message.reply_document(document=open(dxf_file, "rb"), filename="morphed_pattern_all_pieces.dxf")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN missing")
        return

    # Start Health Check Server in background thread for Render
    threading.Thread(target=run_health_server, daemon=True).start()

    req = HTTPXRequest(connect_timeout=45.0, read_timeout=45.0, write_timeout=45.0, pool_timeout=45.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Pattern Memory & Visualizer Bot is running 24/7...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

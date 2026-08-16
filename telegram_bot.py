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
from generator import save_master_dxf, generate_technical_draft_and_dxf

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Pattern AI Engine Online 24/7")
    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👗 *Garment AI Pattern & Drafting Engine Active!*\n\n"
        "1️⃣ *Save Master Template:* CAD export `.dxf` file bhejiye — bot permanently save karega.\n"
        "2️⃣ *Auto 2D CAD Drafting:* Kisi bhi new style ki Measurement Sheet bhejye — AI automatically 2D blueprint drafting image + Optitex-ready multi-piece DXF bana kar dega."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# 1. DXF Upload Handler
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_name.lower().endswith(".dxf"):
        status = await update.message.reply_text("📂 Processing & Saving Master Template DXF...")
        f = await doc.get_file()
        t_dxf = "temp_uploaded.dxf"
        await f.download_to_drive(t_dxf)
        
        g_name = doc.file_name.replace(".dxf", "")
        path, count = save_master_dxf(t_dxf, g_name)
        
        await status.delete()
        await update.message.reply_text(f"✅ *Master Pattern Saved!* (`{count}` pieces recognized)\nAb jab bhi is type ki sheet aayegi, bot automatically execute karega.", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Kripya `.dxf` pattern file bhejiye.")

# 2. Spec Sheet Photo Handler
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("📐 Sheet analyze ho rahi hai... Drafting AI pattern...")
    photo = await update.message.photo[-1].get_file()
    img_path = "temp_spec.jpg"
    await photo.download_to_drive(img_path)

    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')

    prompt = """
    Analyze this garment spec sheet image. Extract measurement values into strictly valid JSON:
    {
      "garment_type": "blazer dress / jacket / top",
      "size": "S",
      "chest": 36.0,
      "waist": 29.0,
      "length": 34.0,
      "shoulder": 13.5,
      "sleeve_length": 20.0,
      "armhole": 7.5
    }
    Output ONLY JSON.
    """

    spec = {"garment_type": "blazer dress", "size": "S", "chest": 36.0, "waist": 29.0, "length": 34.0, "shoulder": 13.5, "sleeve_length": 20.0, "armhole": 7.5}
    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]
            },
            timeout=40
        )
        content = res.json()['choices'][0]['message']['content']
        match = re.search(r'\{.*?\}', content, re.DOTALL)
        if match:
            spec = json.loads(match.group(0))
    except Exception as e:
        print("API error:", e)

    dxf_out, png_out = generate_technical_draft_and_dxf(spec, "drafted_pattern.dxf", "blueprint.png")

    caption = (
        f"🎉 *Optitex 2D Pattern Draft Ready!*\n\n"
        f"• Garment: `{spec.get('garment_type', 'Garment')}`\n"
        f"• Size: `{spec.get('size', 'S')}`\n"
        f"• Length: `{spec.get('length')}\"` | Chest/Bust: `{spec.get('chest')}\"`\n"
        f"• Waist: `{spec.get('waist')}\"` | Shoulder: `{spec.get('shoulder')}\"`"
    )

    await status.delete()
    with open(png_out, "rb") as pf:
        await update.message.reply_photo(photo=pf, caption=caption, parse_mode="Markdown")
    with open(dxf_out, "rb") as df:
        await update.message.reply_document(document=df, filename=f"{spec.get('size','S')}_{spec.get('garment_type','pattern')}.dxf")

def main():
    if not TELEGRAM_BOT_TOKEN: return
    threading.Thread(target=run_health_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(HTTPXRequest(connect_timeout=45.0, read_timeout=45.0)).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

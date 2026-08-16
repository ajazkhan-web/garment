import os
import json
import base64
import re
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from generator import save_master_template, morph_saved_pattern

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *AI Garment Pattern Manager Active!*\n\n"
        "📂 *Step 1:* CAD se export ki gayi `.dxf` master file bhejiye — main har piece ko alag-alag spread karke save kar lunga.\n\n"
        "📸 *Step 2:* Measurement sheet bhej kar sath mein jo bolna chahein likhein (e.g. _'Iss sheet ka L size banao'_).\n\n"
        "Main aapki baat samajh kar saare pieces ka clean layout banake DXF bhej dunga!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# Handle DXF Upload
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_name.lower().endswith(".dxf"):
        status_msg = await update.message.reply_text("📂 Master DXF file process ho rahi hai. Saare pieces separate kiye ja rahe hain...")
        
        file = await doc.get_file()
        temp_dxf = "temp_uploaded.dxf"
        await file.download_to_drive(temp_dxf)

        saved_path, preview_img, count = save_master_template(temp_dxf, "master.dxf")

        caption = (
            f"✅ *Master Pattern Successfully Saved!*\n\n"
            f"• Total Pieces Separated: `{count}`\n"
            f"• File Name: `{doc.file_name}`\n\n"
            f"👇 Saare alag-alag pieces ka layout neeche check karein. Ab aap spec sheet bhej sakte hain!"
        )

        await status_msg.delete()
        await update.message.reply_photo(photo=open(preview_img, "rb"), caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Kripya `.dxf` CAD file bhejiye.")

# Handle Spec Sheet Photo + Text/Voice
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Aapka message aur measurement sheet analyze ho rahi hai...")
    
    user_caption = update.message.caption or "Generate pattern based on sheet"
    photo_file = await update.message.photo[-1].get_file()
    photo_path = "temp_spec.jpg"
    await photo_file.download_to_drive(photo_path)

    prompt = f"""
    User Message/Instruction: "{user_caption}"
    Carefully read the garment measurement spec sheet in the image.
    Extract key measurements for the requested size (e.g., Size L if visible or requested).
    Return strictly a valid JSON object matching:
    {{
      "size": "L",
      "garment_type": "quarter zip pullover jacket",
      "chest": 46.0,
      "length": 28.5,
      "shoulder": 23.0,
      "sleeve_length": 23.0,
      "bottom_width": 42.0
    }}
    Only return raw JSON without backticks or extra text.
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
            "garment_type": "quarter zip jacket",
            "chest": 46.0,
            "length": 28.5,
            "shoulder": 23.0,
            "sleeve_length": 23.0
        }

        if "choices" in res_json and len(res_json["choices"]) > 0:
            content = res_json["choices"][0]["message"]["content"]
            match = re.search(r'\{.*?\}', content, re.DOTALL)
            if match:
                extracted_data = json.loads(match.group(0))

        # Generate Multi-Piece Morphed Pattern
        dxf_file, png_file = morph_saved_pattern(extracted_data, "master.dxf", "morphed_garment_pattern.dxf", "preview.png")

        caption = (
            f"🎉 *All Pattern Pieces Ready as per Master Template!*\n\n"
            f"• Style: `{extracted_data.get('garment_type', 'Camo Jacket')}`\n"
            f"• Size: `{extracted_data.get('size', 'L')}`\n"
            f"• Chest: `{extracted_data.get('chest')}\"` | Length: `{extracted_data.get('length')}\"`\n"
            f"• Shoulder: `{extracted_data.get('shoulder')}\"` | Sleeve: `{extracted_data.get('sleeve_length')}\"`\n\n"
            f"🖼️ Saare alag-alag pieces ka visual breakdown neeche image mein hai."
        )

        await status_msg.delete()
        await update.message.reply_photo(photo=open(png_file, "rb"), caption=caption, parse_mode="Markdown")
        await update.message.reply_document(document=open(dxf_file, "rb"), filename="morphed_pattern_all_pieces.dxf", caption="📁 Optitex Multi-Piece DXF File")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def main():
    req = HTTPXRequest(connect_timeout=45.0, read_timeout=45.0, write_timeout=45.0, pool_timeout=45.0)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(req).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("AI Pattern Manager is Active with Multi-Piece Separation...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
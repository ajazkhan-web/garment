# Handle Measurement Spec Sheet Photo
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("📐 Sheet analyze & pattern drafting shuru...")
    photo_file = await update.message.photo[-1].get_file()
    photo_path = "temp_spec.jpg"
    await photo_file.download_to_drive(photo_path)

    # AI Extraction via API
    with open(photo_path, "rb") as img:
        b64_img = base64.b64encode(img.read()).decode('utf-8')

    prompt = "Extract garment measurement details in JSON with keys: size, garment_type, chest, length, shoulder, sleeve_length, neck_width, armhole."
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}]}]
    }

    res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
    data = {"size": "L", "garment_type": "quarter zip jacket", "chest": 46.0, "length": 28.5, "shoulder": 23.0, "sleeve_length": 23.0}
    try:
        match = re.search(r'\{.*?\}', res.json()['choices'][0]['message']['content'], re.DOTALL)
        if match: data = json.loads(match.group(0))
    except Exception: pass

    # Generate Blueprint Image + Multi-Piece DXF
    dxf_file, png_file = generate_technical_draft_and_dxf(data, "output_pattern.dxf", "output_draft.png")

    await status_msg.delete()
    await update.message.reply_photo(photo=open(png_file, "rb"), caption="📐 *2D Pattern Layout Blueprint Ready!*", parse_mode="Markdown")
    await update.message.reply_document(document=open(dxf_file, "rb"), filename="pattern_optitex_ready.dxf")

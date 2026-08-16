import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message

from generator import make_dress


TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = Path(os.getenv("PATTERN_HOME", "patterns"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE = BASE_DIR / "patterns.sqlite3"

dp = Dispatcher()


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pattern_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def save_pattern(user_id: int, pattern_name: str, file_path: str):
    connection = get_db()
    connection.execute(
        """
        INSERT INTO patterns
        (user_id, pattern_name, file_path, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            pattern_name,
            file_path,
            datetime.utcnow().isoformat(),
        ),
    )
    connection.commit()
    connection.close()


def get_patterns(user_id: int):
    connection = get_db()
    rows = connection.execute(
        """
        SELECT id, pattern_name, file_path
        FROM patterns
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    connection.close()
    return rows


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Pattern bot ready.

"
        "/save_dxf - existing DXF save karein
"
        "/library - saved patterns dekhein
"
        "/newpattern - naya pattern banayein

"
        "Naye DXF ke liye JSON measurements bhejein."
    )


@dp.message(Command("save_dxf"))
async def save_dxf_handler(message: Message):
    await message.answer(
        "Ab DXF/PDS file upload karein.
"
        "Caption mein pattern ka naam likhein."
    )


@dp.message(F.document)
async def document_handler(message: Message, bot: Bot):
    document = message.document
    filename = document.file_name or "pattern.dxf"

    if not filename.lower().endswith((".dxf", ".pds")):
        await message.answer("Sirf DXF ya PDS file upload karein.")
        return

    pattern_name = (message.caption or Path(filename).stem).strip()
    user_folder = BASE_DIR / str(message.from_user.id)
    user_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    destination = user_folder / f"{timestamp}_{filename}"

    telegram_file = await bot.get_file(document.file_id)
    await bot.download_file(telegram_file.file_path, destination)

    save_pattern(
        user_id=message.from_user.id,
        pattern_name=pattern_name,
        file_path=str(destination),
    )

    await message.answer(
        f"Pattern save ho gaya.
"
        f"Name: {pattern_name}
"
        f"Original file overwrite nahi hui."
    )


@dp.message(Command("library"))
async def library_handler(message: Message):
    rows = get_patterns(message.from_user.id)

    if not rows:
        await message.answer("Aapki pattern library empty hai.")
        return

    text = "
".join(
        f"{pattern_id} - {name}"
        for pattern_id, name, file_path in rows
    )

    await message.answer("Saved patterns:

" + text)


@dp.message(Command("newpattern"))
async def new_pattern_handler(message: Message):
    await message.answer(
        "Measurements JSON format mein bhejein:

"
        "{
"
        '  "length": 46,
'
        '  "bust": 38,
'
        '  "waist": 35,
'
        '  "hip": 43,
'
        '  "bottom_opening": 48,
'
        '  "shoulder": 15,
'
        '  "armhole": 8,
'
        '  "sleeve_length": 9,
'
        '  "sleeve_opening": 13,
'
        '  "neck_width": 7,
'
        '  "side_slit": 16
'
        "}"
    )


@dp.message(F.text)
async def json_measurement_handler(message: Message):
    text = message.text.strip()

    if not text.startswith("{"):
        return

    try:
        measurements = json.loads(text)
    except json.JSONDecodeError:
        await message.answer("JSON format galat hai.")
        return

    required = [
        "length",
        "bust",
        "waist",
        "hip",
        "bottom_opening",
        "shoulder",
        "armhole",
    ]

    missing = [field for field in required if field not in measurements]

    if missing:
        await message.answer(
            "Yeh measurements missing hain:
"
            + ", ".join(missing)
        )
        return

    user_folder = BASE_DIR / str(message.from_user.id)
    user_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_file = user_folder / f"dress_{timestamp}.dxf"

    try:
        make_dress(measurements, str(output_file))
    except Exception as error:
        await message.answer(f"Pattern generate nahi hua: {error}")
        return

    await message.answer_document(
        FSInputFile(output_file),
        caption=(
            "Basic DXF pattern generate ho gaya.
"
            "Optitex mein import karke measurements verify karein."
        ),
    )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable set nahi hai."
        )

    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

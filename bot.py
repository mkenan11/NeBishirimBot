import os
import re
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ingredients.db"

MENU = ReplyKeyboardMarkup(
    [
        ["🧺 Ərzaqlarım", "🍽️ Nə bişirim?"],
        ["ℹ️ Kömək"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


# Verilenler bazasini yarat
def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                UNIQUE(user_id, normalized_name)
            )
        """)


# Istifadecini qeydiyyata al
def register_user(user_id):
    with sqlite3.connect(DB_PATH) as db:
        result = db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )
        return result.rowcount == 1


# Yalniz hemin istifadecinin erzaqlarini getir
def get_ingredients(user_id):
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            """
            SELECT name FROM ingredients
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()

    return [row[0] for row in rows]


# Erzaqlari yadda saxla
def add_ingredients(user_id, names):
    added = []
    existing = []

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )

        for name in names:
            normalized = name.casefold()

            result = db.execute(
                """
                INSERT OR IGNORE INTO ingredients
                (user_id, name, normalized_name)
                VALUES (?, ?, ?)
                """,
                (user_id, name, normalized),
            )

            if result.rowcount == 1:
                added.append(name)
            else:
                existing.append(name)

    return added, existing


# Start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_visit = register_user(user_id)

    if first_visit:
        message = (
            "Salam! 👋\n\n"
            "Evdə olan ərzaqları yaz və ya şəklini göndər. "
            "Mən sənə onlarla hazırlaya biləcəyin "
            "yeməkləri təklif edəcəyəm.\n\n"
            "Məsələn: Kartof, yumurta, soğan.\n\n"
            "Ərzaqları əlavə etdikdən sonra "
            "«🍽️ Nə bişirim?» düyməsinə bas."
        )
    else:
        count = len(get_ingredients(user_id))

        if count:
            message = (
                "Yenidən xoş gəldin! 👋\n\n"
                f"Siyahında {count} ərzaq var.\n"
                "Ərzaqlarını yeniləyə və ya yemək tapa bilərsən."
            )
        else:
            message = (
                "Yenidən xoş gəldin! 👋\n\n"
                "Siyahın boşdur. Ərzaq əlavə edərək başlaya bilərsən."
            )

    await update.message.reply_text(
        message,
        reply_markup=MENU,
    )


# Mesajlari ve menyunu idare et
async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    register_user(user_id)

    # Erzaqlarim
    if text == "🧺 Ərzaqlarım":
        items = get_ingredients(user_id)

        if not items:
            response = (
                "🧺 Ərzaqlarım\n\n"
                "Siyahın hələ boşdur.\n\n"
                "Ərzaqları vergüllə ayıraraq yaz.\n"
                "Məsələn: Kartof, yumurta, soğan"
            )
        else:
            # Ilk merhelede siyahini metn kimi gosteririk
            lines = [
                f"{i}. {name}"
                for i, name in enumerate(items, 1)
            ]

            response = (
                f"🧺 Ərzaqlarım ({len(items)})\n\n"
                + "\n".join(lines)
            )

    # Resept duymesi
    elif text == "🍽️ Nə bişirim?":
        items = get_ingredients(user_id)

        if not items:
            response = (
                "🧺 Siyahın boşdur.\n\n"
                "Əvvəlcə evində olan ərzaqları əlavə et."
            )
        else:
            response = (
                "🍽️ Resept sistemi hələ hazırlanır.\n\n"
                f"Siyahındakı {len(items)} ərzaq yadda saxlanılıb."
            )

    # Komek
    elif text == "ℹ️ Kömək":
        response = (
            "ℹ️ Kömək\n\n"
            "Hazırda ərzaqları mətnlə əlavə edə bilərsən.\n\n"
            "Məsələn: Kartof, yumurta, soğan\n\n"
            "Şəkil, redaktə və resept funksiyaları "
            "növbəti mərhələlərdə aktivləşdiriləcək."
        )

    # Erzaq elave edilmesi
    else:
        # Serbest cumleleri helelik sehv yadda saxlamayaq
        if re.search(
            r"\b(evdə|evde|var|yoxdur|yoxdu|bitib|qalmayıb)\b",
            text,
            re.IGNORECASE,
        ):
            await update.message.reply_text(
                "Hələlik ərzaq adlarını sadə siyahı kimi yaz.\n\n"
                "Məsələn: Kartof, yumurta, soğan",
                reply_markup=MENU,
            )
            return

        # Vergul, yeni setir ve 've' ile bol
        parts = re.split(
            r"[,;\n]|\s+və\s+",
            text,
            flags=re.IGNORECASE,
        )

        names = []

        for part in parts:
            name = part.strip(" \t\r\n.!?،؛")

            # Meselen: 3 kartof -> kartof
            name = re.sub(
                r"^\d+(?:[.,]\d+)?\s*"
                r"(?:(?:ədəd|dənə|qram|q|kq|kg)\s+)?",
                "",
                name,
                flags=re.IGNORECASE,
            ).strip()

            # Yanlis ve cox uzun daxilolmalari kec
            if not name or len(name) > 50:
                continue

            if re.search(r"\d", name):
                continue

            name = name[0].upper() + name[1:].lower()

            if name.casefold() not in [
                item.casefold() for item in names
            ]:
                names.append(name)

        if not names:
            response = (
                "Ərzaq adı müəyyən edə bilmədim.\n\n"
                "Məsələn: Kartof, yumurta, soğan"
            )
        else:
            added, existing = add_ingredients(user_id, names)

            lines = []

            if added:
                lines.append(
                    "✅ Əlavə olundu:\n" + "\n".join(added)
                )

            if existing:
                lines.append(
                    "ℹ️ Artıq siyahında var:\n"
                    + "\n".join(existing)
                )

            count = len(get_ingredients(user_id))

            lines.append(
                f"🧺 Siyahında ümumilikdə {count} ərzaq var."
            )

            response = "\n\n".join(lines)

    await update.message.reply_text(
        response,
        reply_markup=MENU,
    )


# Sekil funksiyasi helelik hazir deyil
async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "📸 Şəkil tanıma funksiyasını növbəti "
        "mərhələlərdə aktivləşdirəcəyik.\n\n"
        "Hələlik ərzaqları mətnlə göndər.",
        reply_markup=MENU,
    )


def main():
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError("Telegram tokeni tapılmadı!")

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    app.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    print("Bot işləyir! Dayandırmaq üçün Ctrl+C bas.")

    app.run_polling()


if __name__ == "__main__":
    main()
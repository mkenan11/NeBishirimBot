import os
import re
import database as sqlite3
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from basket_ui import basket_click, handle_rename_text, show_basket
from quick_add import STAPLES, quick_add_click
from ai_features import (
    photo_click,
    photo_text,
    handle_photo,
)
from recipes import recipe_start, recipe_click
from session_bridge import install_session_handlers

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

KNOWN = {name.casefold() for name in STAPLES} | {
    name.casefold()
    for name in (
        "Toyuq", "Toyuq filesi", "Mal əti", "Qoyun əti",
        "Ət", "Balıq", "Qiymə", "Kolbasa", "Sosiska",
        "Noxud", "Lobya", "Mərci", "Yaşıl noxud",
        "Qarğıdalı", "Göbələk", "Badımcan", "Bibər",
        "Yaşıl bibər", "Qırmızı bibər", "Xiyar",
        "Kələm", "Gül kələmi", "Brokoli", "İspanaq",
        "Kahı", "Cəfəri", "Şüyüd", "Keşniş", "Nanə",
        "Limon", "Alma", "Banan", "Portağal", "Bal",
        "Mürəbbə", "Qaymaq", "Xama", "Kəsmik",
        "Mozzarella", "Yulaf", "Bulgur", "İrmik",
        "Nişasta", "Sirkə", "Zeytun yağı",
        "Günəbaxan yağı", "Qara istiot", "İstiot",
        "Paprika", "Zirə", "Darçın", "Dəfnə yarpağı",
        "Mayonez", "Ketçup", "Xardal", "Pomidor püresi",
        "Tomat", "Qırmızı soğan", "Yaşıl soğan",
        "Turşu", "Zeytun", "Lavaş", "Yumurta ağı",
    )
}

TYPO_FIXES = {
    "kartf": "Kartof",
    "yumrta": "Yumurta",
    "pomdor": "Pomidor",
    "sogan": "Soğan",
    "sarmisaq": "Sarımsaq",
    "duyu": "Düyü",
    "seker": "Şəkər",
}

NEGATIVE = re.compile(
    r"\b(yoxdur|yoxdu|yox|bitib|qalmayıb|qalmayib)\b",
    re.IGNORECASE,
)

NAME_PATTERN = re.compile(
    r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
    re.UNICODE,
)


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                UNIQUE(user_id, normalized_name)
            )
            """
        )


def register_user(user_id):
    with sqlite3.connect(DB_PATH) as db:
        result = db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )
        return result.rowcount == 1


def get_ingredients(user_id):
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            """
            SELECT name FROM ingredients
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

    return [row[0] for row in rows]


def add_ingredients(user_id, names):
    added = []
    existing = []

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )

        for name in names:
            result = db.execute(
                """
                INSERT OR IGNORE INTO ingredients
                (user_id, name, normalized_name)
                VALUES (?, ?, ?)
                """,
                (user_id, name, name.casefold()),
            )

            if result.rowcount == 1:
                added.append(name)
            else:
                existing.append(name)

    return added, existing


def invalidate_edit_state(context):
    for key in ("undo", "delete_state", "clear_state"):
        context.user_data.pop(key, None)


def clear_photo_state(context):
    for key in ("photo_state", "photo_message_id"):
        context.user_data.pop(key, None)


def parse_ingredients(text):
    if len(text) > 1500:
        return None

    parts = re.split(
        r"[,;\n]|\s+və\s+",
        text,
        flags=re.IGNORECASE,
    )

    if len(parts) > 30:
        return None

    known = []
    unknown = []
    skipped = []
    invalid = []
    corrected = []
    seen = set()

    for part in parts:
        original = part.strip(" \t\r\n.!?،؛")

        if not original:
            continue

        if NEGATIVE.search(original):
            skipped.append(original)
            continue

        name = re.sub(
            r"^(?:evdə|evde|məndə|mende)\s+",
            "",
            original,
            flags=re.IGNORECASE,
        )

        name = re.sub(
            r"\s+(?:var|vardır|vardir)$",
            "",
            name,
            flags=re.IGNORECASE,
        )

        name = re.sub(
            r"^\d+(?:[.,]\d+)?\s*"
            r"(?:(?:ədəd|dənə|qram|q|kq|kg|kilo|litr|ml)\s+)?",
            "",
            name,
            flags=re.IGNORECASE,
        )

        name = " ".join(name.split()).strip(".!? ")

        if (
            not name
            or len(name) > 50
            or not NAME_PATTERN.fullmatch(name)
        ):
            invalid.append(original)
            continue

        raw_name = name[0].upper() + name[1:].lower()
        name = TYPO_FIXES.get(
            raw_name.casefold(),
            raw_name,
        )

        normalized = name.casefold()

        if normalized in seen:
            continue

        seen.add(normalized)

        if name != raw_name:
            corrected.append(f"{raw_name} → {name}")

        if normalized in KNOWN:
            known.append(name)
        else:
            unknown.append(name)

    return known, unknown, skipped, invalid, corrected


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("rename_target", None)
    context.user_data.pop("pending_names", None)
    context.user_data.pop("pending_message_id", None)
    clear_photo_state(context)

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


def pending_view(names, note=""):
    name = names[0]

    text = (
        f"{note}❓ «{name}» ərzaq kimi siyahına əlavə edilsin?\n\n"
        f"Təsdiq gözləyən: {len(names)}\n"
        "Tanımadığım adı sənin təsdiqin olmadan əlavə etmirəm."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Əlavə et",
                callback_data="text:yes",
            )
        ],
        [
            InlineKeyboardButton(
                "⏭️ Keç",
                callback_data="text:skip",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Hamısını ləğv et",
                callback_data="text:cancel",
            )
        ],
    ])

    return text, keyboard


async def pending_click(update, context):
    query = update.callback_query
    names = context.user_data.get("pending_names")

    if (
        not names
        or query.message is None
        or query.message.message_id
        != context.user_data.get("pending_message_id")
    ):
        await query.answer(
            "Bu təsdiq artıq qüvvədə deyil.",
            show_alert=True,
        )
        return

    await query.answer()

    action = query.data.split(":", 1)[1]

    if action == "cancel":
        context.user_data.pop("pending_names", None)
        context.user_data.pop("pending_message_id", None)
        await query.edit_message_text(
            "❌ Qalan təsdiqlər ləğv edildi."
        )
        return

    name = names.pop(0)

    if action == "yes":
        added, _ = add_ingredients(
            query.from_user.id,
            [name],
        )

        if added:
            invalidate_edit_state(context)

        status = (
            "✅ Əlavə olundu"
            if added
            else "ℹ️ Artıq siyahındadır"
        )

        note = f"{status}: {name}\n\n"
    else:
        note = f"⏭️ Keçildi: {name}\n\n"

    if names:
        text, keyboard = pending_view(
            names,
            note,
        )

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
    else:
        context.user_data.pop("pending_names", None)
        context.user_data.pop("pending_message_id", None)

        count = len(
            get_ingredients(query.from_user.id)
        )

        await query.edit_message_text(
            f"{note}✅ Təsdiqlər tamamlandı.\n"
            f"🧺 Siyahında {count} ərzaq var."
        )


async def show_basket_clean(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    clear_photo_state(context)
    await show_basket(update, context)


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    register_user(user_id)

    # AI resept axtarisi
    if text == "🍽️ Nə bişirim?":
        context.user_data.pop("rename_target", None)
        context.user_data.pop("pending_names", None)
        context.user_data.pop("pending_message_id", None)
        clear_photo_state(context)

        await recipe_start(update, context)
        return

    # Sekildeki siyahini metnle duzeltme
    if context.user_data.get("photo_state"):
        handled = await photo_text(update, context)

        if handled:
            return

    # Evvelki ad deyisme funksiyasi
    if context.user_data.get("rename_target") is not None:
        await handle_rename_text(update, context)
        return

    context.user_data.pop("pending_names", None)
    context.user_data.pop("pending_message_id", None)

    parsed = parse_ingredients(text)

    if parsed is None:
        await update.message.reply_text(
            "Bir dəfəyə ən çox 30 ərzaq və 1500 simvol göndər.\n"
            "Ərzaqları vergüllə və ya yeni sətirlə ayır.",
            reply_markup=MENU,
        )
        return

    known, unknown, skipped, invalid, corrected = parsed

    if known:
        added, existing = add_ingredients(
            user_id,
            known,
        )
    else:
        added, existing = [], []

    if added:
        invalidate_edit_state(context)

    lines = []

    if added:
        lines.append(
            "✅ Əlavə olundu:\n"
            + "\n".join(added)
        )

    if existing:
        lines.append(
            "ℹ️ Artıq siyahındadır:\n"
            + "\n".join(existing)
        )

    if corrected:
        lines.append(
            "✍️ Yazılışı düzəldildi:\n"
            + "\n".join(corrected)
        )

    if skipped:
        lines.append(
            "🚫 Mövcud olmayanlar əlavə edilmədi:\n"
            + "\n".join(skipped)
        )

    if invalid:
        lines.append(
            "❗ Anlamadım, əlavə etmədim:\n"
            + "\n".join(invalid)
        )

    if not lines and not unknown:
        lines.append(
            "Ərzaq müəyyən edə bilmədim.\n"
            "Məsələn: Kartof, yumurta, soğan"
        )

    if lines:
        count = len(get_ingredients(user_id))

        lines.append(
            f"🧺 Siyahında {count} ərzaq var."
        )

        await update.message.reply_text(
            "\n\n".join(lines),
            reply_markup=MENU,
        )

    if unknown:
        context.user_data["pending_names"] = unknown

        message, keyboard = pending_view(unknown)

        sent = await update.message.reply_text(
            message,
            reply_markup=keyboard,
        )

        context.user_data["pending_message_id"] = (
            sent.message_id
        )


def help_view(section="main"):
    if section == "usage":
        text = (
            "📖 Necə istifadə olunur?\n\n"
            "1. Ərzaqları yaz, şəkil göndər və ya "
            "«⚡ Tez əlavə et» bölməsindən seç.\n"
            "2. Şəkildən tanınan ərzaqları yoxla və təsdiqlə.\n"
            "3. «🧺 Ərzaqlarım» bölməsində siyahını düzəlt.\n"
            "4. «🍽️ Nə bişirim?» ilə reseptlərə bax."
        )

    elif section == "about":
        text = (
            "🤖 Bot haqqında\n\n"
            "«Nə bişirim?» evdəki ərzaqlara əsasən "
            "yemək ideyaları təklif edən Telegram botudur.\n\n"
            "Şəkildən ərzaq tanımaq və resept hazırlamaq "
            "üçün Gemini AI istifadə olunur.\n\n"
            "AI səhv edə bilər. Ərzaqları və "
            "resept addımlarını yoxlamağını tövsiyə edirik."
        )

    elif section == "privacy":
        text = (
            "🔐 Məxfilik və məlumatlarım\n\n"
            "Bot Telegram istifadəçi ID-ni və təsdiqlənmiş "
            "ərzaq siyahını lokal SQLite bazasında saxlayır.\n\n"
            "Şəkil göndərdikdə foto tanınma üçün, "
            "resept istədikdə isə ərzaq adların "
            "Google-un Gemini API-sinə ötürülür.\n\n"
            "Bot fotoşəkilləri ayrıca lokal fayl kimi saxlamır. "
            "Telegram çatındakı mesajlar isə "
            "«Hamısını sil» düyməsi ilə silinmir.\n\n"
            "«Hamısını sil» yalnız ərzaqlarını silir; "
            "istifadəçi ID-si bazada qalır. "
            "Tam məlumat silmə funksiyası hələ hazırlanmayıb.\n\n"
            "Geri qaytarma yalnız cari bot sessiyasında "
            "və siyahı dəyişməyənədək mümkündür.\n\n"
            "Google API-nin məlumatlardan istifadə qaydaları "
            "hesabının xidmət səviyyəsindən asılıdır. "
            "Şəxsi və həssas məlumatları fotoya daxil etmə."
        )

    else:
        text = (
            "ℹ️ Kömək\n\n"
            "Maraqlandığın bölməni seç:"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📖 Necə istifadə olunur?",
                    callback_data="help:usage",
                )
            ],
            [
                InlineKeyboardButton(
                    "🤖 Bot haqqında",
                    callback_data="help:about",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔐 Məxfilik və məlumatlarım",
                    callback_data="help:privacy",
                )
            ],
        ])

        return text, keyboard

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Kömək menyusu",
                callback_data="help:main",
            )
        ]
    ])

    return text, keyboard


async def show_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("rename_target", None)
    context.user_data.pop("pending_names", None)
    context.user_data.pop("pending_message_id", None)
    clear_photo_state(context)

    text, keyboard = help_view()

    message = await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["help_message_id"] = (
        message.message_id
    )


async def help_click(update, context):
    query = update.callback_query

    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("help_message_id")
    ):
        await query.answer(
            "Kömək menyusunu yenidən aç.",
            show_alert=True,
        )
        return

    await query.answer()

    section = query.data.split(":", 1)[1]

    text, keyboard = help_view(section)

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )


def create_application():
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if not token:
        raise RuntimeError(
            "Telegram tokeni tapılmadı!"
        )

    if not gemini_key:
        raise RuntimeError(
            "GEMINI_API_KEY .env faylında tapılmadı!"
        )


    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(False)
        .build()
    )

    install_session_handlers(app)

    app.add_handler(
        CommandHandler("start", start)
    )

    # Bu handler umumi pantry handlerinden evvel olmalidir.
    app.add_handler(
        CallbackQueryHandler(
            recipe_start,
            pattern=r"^pantry:recipe$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            basket_click,
            pattern=r"^pantry:",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            quick_add_click,
            pattern=r"^quick:",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            photo_click,
            pattern=r"^photo:",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            recipe_click,
            pattern=r"^recipe:",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            pending_click,
            pattern=r"^text:",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            help_click,
            pattern=r"^help:",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(r"^🧺 Ərzaqlarım$"),
            show_basket_clean,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(r"^ℹ️ Kömək$"),
            show_help,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )


    return app



def main():
    # Evvel .env yuklenir, sonra baza hazirlanir.
    app = create_application()
    init_db()

    print("Bot isleyir! Dayandirmaq ucun Ctrl+C bas.")
    app.run_polling()


if __name__ == "__main__":
    main()
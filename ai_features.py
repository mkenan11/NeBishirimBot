import os
import re
import database as sqlite3
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from google import genai
from google.genai import types
from pydantic import BaseModel

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from basket_ui import basket_view, get_rows


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ingredients.db"

# Esas model ve ehtiyat model
MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
)

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_ITEMS = 20

NAME_PATTERN = re.compile(
    r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
    re.UNICODE,
)


# ============================================================
# GEMINI CAVAB FORMATLARI
# ============================================================

class PhotoResult(BaseModel):
    status: Literal["ok", "no_food", "unclear"]
    ingredients: list[str]


class Recipe(BaseModel):
    name: str
    minutes: int
    ingredients: list[str]
    steps: list[str]


class RecipeBatch(BaseModel):
    recipes: list[Recipe]


# ============================================================
# UMUMI KOMEKCI FUNKSIYALAR
# ============================================================

def button(label, action):
    return InlineKeyboardButton(
        label,
        callback_data=action,
    )


def normalize_name(value):
    if not isinstance(value, str):
        return None

    name = " ".join(value.split()).strip()

    if (
        not name
        or len(name) > 50
        or not NAME_PATTERN.fullmatch(name)
    ):
        return None

    return name[0].upper() + name[1:].lower()


def unique_names(values, limit=20):
    result = []
    seen = set()

    for value in values:
        name = normalize_name(value)

        if name is None:
            continue

        normalized = name.casefold()

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(name)

        if len(result) >= limit:
            break

    return result


def api_error_message(error):
    code = getattr(error, "code", None)

    if code == 429:
        return (
            "Gemini istifadə limiti dolub. "
            "Bir qədər sonra yenidən cəhd et."
        )

    if code in (401, 403):
        return (
            "Gemini API açarı və ya icazə ilə bağlı "
            "problem var."
        )

    if code in (500, 502, 503, 504):
        return (
            "Gemini hazırda məşğuldur. "
            "Bir qədər sonra yenidən cəhd et."
        )

    if code == 404:
        return (
            "Seçilmiş Gemini modeli hazırda "
            "istifadə üçün əlçatan deyil."
        )

    return (
        "Gemini sorğusu tamamlanmadı. "
        "Bir qədər sonra yenidən cəhd et."
    )


# ============================================================
# GEMINI: AVTOMATIK EHTIYAT MODEL
# ============================================================

async def ask_gemini(contents, schema, temperature=0.1):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY tapilmadi."
        )

    last_error = None

    for index, model_name in enumerate(MODELS):
        try:
            # SDK-nin uzunmuddetli avtomatik retry
            # dovrunu sondururuk.
            # Model secimini ozumuz idare edirik.
            http_options = types.HttpOptions(
                timeout=30000,
                retry_options=types.HttpRetryOptions(
                    attempts=1,
                ),
            )

            async with genai.Client(
                api_key=api_key,
                http_options=http_options,
            ).aio as client:

                response = await client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=temperature,
                    ),
                )

                # SDK cavabi artiq Pydantic model kimi
                # emal edibse, onu qaytar.
                if isinstance(response.parsed, schema):
                    print(
                        f"Gemini ugurlu model: {model_name}"
                    )
                    return response.parsed

                if not response.text:
                    raise ValueError(
                        "Gemini bos cavab qaytardi."
                    )

                parsed = schema.model_validate_json(
                    response.text
                )

                print(
                    f"Gemini ugurlu model: {model_name}"
                )

                return parsed

        except Exception as error:
            last_error = error
            code = getattr(error, "code", None)

            # Yalniz server xetalari ve modelin
            # elcatan olmamasi zamani fallback et.
            can_fallback = code in (
                404,
                500,
                502,
                503,
                504,
            )

            print(
                f"Gemini model: {model_name} | "
                f"Xeta novu: {type(error).__name__} | "
                f"Kod: {code}"
            )

            # Son modeldirse, xetani yuxariya otur.
            if index == len(MODELS) - 1:
                raise

            # API acar, quota, JSON ve diger
            # xetalarda ikinci modelle davam etme.
            if not can_fallback:
                raise

            print(
                "Esas model cavab vermedi. "
                f"Ehtiyat modele kecilir: {MODELS[index + 1]}"
            )

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "Gemini modeli secilmedi."
    )


# ============================================================
# 1. SEKILDEN ERZAQ TANIMA
# ============================================================

def photo_view(state):
    names = state["names"]
    selected = state["selected"]

    lines = [
        "📸 Şəkildən tanınan ərzaqlar",
        "",
        "Siyahını yoxla: ✅/☐ seçim,",
        "✏️ ad düzəlişi, 🗑️ nəticədən silmə.",
        "Çatışmayan ərzağı əlavə edə bilərsən.",
        "",
    ]

    if names:
        for index, name in enumerate(names):
            mark = "✅" if index in selected else "☐"

            lines.append(
                f"{index + 1}. {mark} {name}"
            )
    else:
        lines.append(
            "Siyahı boşdur. Ərzaq əlavə edə bilərsən."
        )

    lines.extend([
        "",
        f"Təsdiqlənəcək: {len(selected)}",
    ])

    buttons = []

    for index, name in enumerate(names):
        mark = "✅" if index in selected else "☐"

        buttons.append([
            button(
                f"{mark} {name}"[:50],
                f"photo:toggle:{index}",
            ),
            button(
                "✏️",
                f"photo:rename:{index}",
            ),
            button(
                "🗑️",
                f"photo:delete:{index}",
            ),
        ])

    buttons.append([
        button(
            "➕ Çatışmayan ərzağı əlavə et",
            "photo:add",
        )
    ])

    if selected:
        buttons.append([
            button(
                f"✅ Təsdiqlə və əlavə et ({len(selected)})",
                "photo:save",
            )
        ])

    buttons.append([
        button(
            "❌ Ləğv et",
            "photo:cancel",
        )
    ])

    return (
        "\n".join(lines),
        InlineKeyboardMarkup(buttons),
    )


async def handle_photo(update, context):
    # Evvelki yarimciq sekil secimini bagla.
    context.user_data.pop("photo_state", None)
    context.user_data.pop("photo_message_id", None)

    # Evvelki metn tesdiqini bagla.
    context.user_data.pop("pending_names", None)
    context.user_data.pop("pending_message_id", None)

    photo = update.message.photo[-1]

    if (
        photo.file_size
        and photo.file_size > MAX_PHOTO_BYTES
    ):
        await update.message.reply_text(
            "📸 Şəkil çox böyükdür. "
            "8 MB-dan kiçik foto göndər."
        )
        return

    status = await update.message.reply_text(
        "🔍 Şəkildəki ərzaqları analiz edirəm..."
    )

    try:
        telegram_file = await photo.get_file()

        image_bytes = bytes(
            await telegram_file.download_as_bytearray()
        )

        if len(image_bytes) > MAX_PHOTO_BYTES:
            await status.edit_text(
                "📸 Şəkil çox böyükdür. "
                "Daha kiçik foto göndər."
            )
            return

        instruction = (
            "Sən ərzaq tanıma köməkçisisən. "
            "Fotoda aydın görünən ərzaqları "
            "Azərbaycan dilində adlandır. "
            "Yalnız vizual olaraq görünən "
            "ərzaqları qaytar. "
            "Pulqabı, telefon, qab və insan kimi "
            "ərzaq olmayan obyektləri daxil etmə. "
            "Bağlı qablaşdırmanın içini təxmin etmə. "
            "Üzərində məhsulun adı aydın yazılıbsa "
            "ondan istifadə edə bilərsən. "
            "Eyni ərzağı yalnız bir dəfə göstər. "
            "Miqdar və brend yazma. "
            "Əmin olmadığın məhsulu siyahıya salma. "
            "Ərzaq yoxdursa status no_food seç. "
            "Şəkil çox bulanıq və anlaşılmazdırsa "
            "status unclear seç. "
            "Ən çox 20 ərzaq qaytar."
        )

        result = await ask_gemini(
            [
                instruction,
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                ),
            ],
            PhotoResult,
        )

    except Exception as error:
        await status.edit_text(
            "❌ Şəkli analiz etmək mümkün olmadı.\n\n"
            + api_error_message(error)
        )
        return

    if result.status == "no_food":
        await status.edit_text(
            "📸 Bu şəkildə ərzaq müəyyən edə bilmədim.\n\n"
            "Ərzaqların şəklini göndər."
        )
        return

    if result.status == "unclear":
        await status.edit_text(
            "📸 Şəkil kifayət qədər aydın deyil.\n\n"
            "Daha işıqlı və aydın foto göndər."
        )
        return

    names = unique_names(
        result.ingredients,
        MAX_PHOTO_ITEMS,
    )

    if not names:
        await status.edit_text(
            "📸 Şəkildə etibarlı ərzaq adı "
            "müəyyən edə bilmədim.\n\n"
            "Daha aydın foto göndər "
            "və ya ərzaqları mətnlə yaz."
        )
        return

    state = {
        "names": names,
        "selected": set(range(len(names))),
        "mode": None,
        "target": None,
    }

    context.user_data["photo_state"] = state

    text, keyboard = photo_view(state)

    await status.edit_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["photo_message_id"] = (
        status.message_id
    )


# Sekildeki siyahini metnle duzelt.
async def photo_text(update, context):
    state = context.user_data.get(
        "photo_state"
    )

    if not state or not state["mode"]:
        return False

    mode = state["mode"]
    original = update.message.text

    parts = re.split(
        r"[,;\n]",
        original,
    )

    if mode == "rename" and len(parts) != 1:
        await update.message.reply_text(
            "Yalnız bir yeni ərzaq adı yaz.\n"
            "Məsələn: Qırmızı soğan"
        )
        return True

    if len(parts) > 10:
        await update.message.reply_text(
            "Bir dəfəyə ən çox 10 ərzaq əlavə et."
        )
        return True

    names = unique_names(
        parts,
        10,
    )

    nonempty_count = len([
        part
        for part in parts
        if part.strip()
    ])

    if (
        not names
        or len(names) != nonempty_count
    ):
        await update.message.reply_text(
            "❌ Ərzaq adlarını düzgün yaz.\n\n"
            "Məsələn: Kartof, pomidor, yumurta"
        )
        return True

    existing = {
        name.casefold()
        for name in state["names"]
    }

    if mode == "rename":
        target = state["target"]

        if (
            target is None
            or target < 0
            or target >= len(state["names"])
        ):
            state["mode"] = None

            await update.message.reply_text(
                "Seçim dəyişib. Yenidən cəhd et."
            )
            return True

        new_name = names[0]
        old_name = state["names"][target]

        if (
            new_name.casefold() in existing
            and new_name.casefold()
            != old_name.casefold()
        ):
            await update.message.reply_text(
                "Bu ərzaq artıq siyahıdadır. "
                "Başqa ad yaz."
            )
            return True

        state["names"][target] = new_name
        state["selected"].add(target)

        message = (
            f"✅ «{old_name}» → «{new_name}»"
        )

    else:
        added = []

        for name in names:
            if (
                name.casefold() in existing
                or len(state["names"]) >= MAX_PHOTO_ITEMS
            ):
                continue

            index = len(state["names"])

            state["names"].append(name)
            state["selected"].add(index)

            existing.add(name.casefold())
            added.append(name)

        if added:
            message = (
                "✅ Siyahıya əlavə edildi:\n"
                + "\n".join(added)
            )
        else:
            message = (
                "Yeni ərzaq əlavə edilmədi. "
                "Məhsul artıq siyahıda ola bilər "
                "və ya 20 ərzaq limitinə çatmısan."
            )

    state["mode"] = None
    state["target"] = None

    await update.message.reply_text(
        message
    )

    text, keyboard = photo_view(state)

    review = await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["photo_message_id"] = (
        review.message_id
    )

    return True


async def photo_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("photo_message_id")
    ):
        await query.answer(
            "Bu şəkil menyusu köhnəlib. "
            "Yeni şəkil göndər.",
            show_alert=True,
        )
        return

    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    # Tesdiq bitdikden sonra sebete kec.
    if action == "basket":
        text, keyboard = basket_view(user_id)

        context.user_data.pop("photo_state", None)
        context.user_data.pop("photo_message_id", None)

        context.user_data["basket_message_id"] = (
            query.message.message_id
        )

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
        return

    state = context.user_data.get(
        "photo_state"
    )

    if state is None:
        await query.edit_message_text(
            "Bu şəkil təsdiqi artıq tamamlanıb."
        )
        return

    if action == "cancel":
        context.user_data.pop("photo_state", None)
        context.user_data.pop("photo_message_id", None)

        await query.edit_message_text(
            "❌ Şəkil təsdiqi ləğv edildi. "
            "Heç bir ərzaq əlavə olunmadı."
        )
        return

    if action in ("toggle", "rename", "delete"):
        if (
            len(parts) != 3
            or not parts[2].isdigit()
        ):
            return

        index = int(parts[2])

        if not 0 <= index < len(state["names"]):
            return

        if action == "delete":
            # Yalniz sekilden taninmis muveqqeti siyahini deyisir.
            # Sebet bazasina hec bir sorgu gonderilmir.
            state["names"].pop(index)
            state["selected"] = {
                item - 1 if item > index else item
                for item in state["selected"]
                if item != index
            }

            # Gozleyen rename hədəfi indeks deyişməsinə görə
            # yanlış ərzağı dəyişməsin.
            if state["mode"] == "rename":
                state["mode"] = None
                state["target"] = None

            text, keyboard = photo_view(state)
            await query.edit_message_text(
                text,
                reply_markup=keyboard,
            )
            return

        if action == "toggle":
            if index in state["selected"]:
                state["selected"].remove(index)
            else:
                state["selected"].add(index)

            text, keyboard = photo_view(state)

            await query.edit_message_text(
                text,
                reply_markup=keyboard,
            )
            return

        state["mode"] = "rename"
        state["target"] = index

        await query.message.reply_text(
            f"✏️ Seçilən ərzaq: {state['names'][index]}\n\n"
            "Yeni adını adi mesaj kimi yaz.\n"
            "Məsələn: Qırmızı soğan"
        )
        return

    if action == "add":
        state["mode"] = "add"
        state["target"] = None

        await query.message.reply_text(
            "➕ Şəkildə olub tanınmayan ərzaqları yaz.\n\n"
            "Məsələn: Kartof, yumurta\n\n"
            "Bir dəfəyə ən çox 10 ərzaq yaza bilərsən."
        )
        return

    if action != "save":
        return

    selected_names = [
        name
        for index, name in enumerate(state["names"])
        if index in state["selected"]
    ]

    if not selected_names:
        await query.message.reply_text(
            "Əvvəlcə ən azı bir ərzaq seç."
        )
        return

    added = []
    existing = []

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
            """,
            (user_id,),
        )

        for name in selected_names:
            result = db.execute(
                """
                INSERT OR IGNORE INTO ingredients
                (user_id, name, normalized_name)
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    name,
                    name.casefold(),
                ),
            )

            if result.rowcount == 1:
                added.append(name)
            else:
                existing.append(name)

    if added:
        for key in (
            "undo",
            "delete_state",
            "clear_state",
        ):
            context.user_data.pop(key, None)

    context.user_data.pop("photo_state", None)

    count = len(get_rows(user_id))

    lines = [
        "✅ Şəkil üzrə təsdiq tamamlandı.",
        "",
    ]

    if added:
        lines.append(
            "Əlavə olundu:\n"
            + "\n".join(added)
        )

    if existing:
        lines.append(
            "Artıq siyahında var:\n"
            + "\n".join(existing)
        )

    lines.extend([
        "",
        f"🧺 Siyahında {count} ərzaq var.",
    ])

    keyboard = InlineKeyboardMarkup([
        [
            button(
                "🧺 Ərzaqlarım",
                "photo:basket",
            )
        ]
    ])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=keyboard,
    )


# ============================================================
# 2. GEMINI ILE RESEPTLER
# ============================================================

def clean_recipes(batch, basket_names, used_names):
    available = {
        name.casefold()
        for name in basket_names
    }

    found = []
    seen = set(used_names)

    for recipe in batch.recipes:
        name = " ".join(recipe.name.split())

        if (
            not name
            or len(name) > 80
            or name.casefold() in seen
        ):
            continue

        if not 1 <= recipe.minutes <= 240:
            continue

        ingredients = unique_names(
            recipe.ingredients,
            16,
        )

        if (
            not ingredients
            or len(ingredients)
            != len(recipe.ingredients)
        ):
            continue

        steps = [
            " ".join(step.split())
            for step in recipe.steps
            if isinstance(step, str)
            and step.strip()
        ]

        if not 2 <= len(steps) <= 8:
            continue

        if any(
            len(step) > 250
            for step in steps
        ):
            continue

        missing = [
            ingredient
            for ingredient in ingredients
            if ingredient.casefold() not in available
        ]

        # Sade ingredient siyahisina gore yoxlama.
        # Addimlarin menasini AI yazdigi ucun
        # istifadeci yenede resepti yoxlamalidir.
        if len(missing) > 2:
            continue

        seen.add(name.casefold())

        found.append({
            "name": name,
            "minutes": recipe.minutes,
            "ingredients": ingredients,
            "missing": missing,
            "steps": steps,
        })

        if len(found) == 3:
            break

    found.sort(
        key=lambda item: len(item["missing"])
    )

    return found


async def generate_recipes(basket_names, previous):
    prompt = (
        "Sən Azərbaycan dilində resept təklif edən "
        "mətbəx köməkçisisən.\n\n"
        "İstifadəçinin səbətində bu ərzaqlar var:\n"
        + ", ".join(basket_names)
        + "\n\n"
        "Yalnız real və bişirilə bilən yeməklər təklif et. "
        "6 fərqli namizəd resept hazırla. "
        "Hər resept üçün ad, təxmini vaxt, "
        "lazım olan bütün ərzaqların adlarını "
        "və 2-8 qısa hazırlanma addımını ver. "
        "Ərzaq adlarına miqdar əlavə etmə. "
        "Səbətdə olan ərzaqları eyni adlarla yaz. "
        "Hər reseptdə ən çox 2 əlavə ərzaq ola bilər. "
        "Duz, yağ, su, ədviyyat daxil olmaqla "
        "səbətdə olmayan heç bir ərzağı "
        "evdə var hesab etmə. "
        "Addımlarda adı çəkilən bütün ərzaqları "
        "ingredients siyahısında göstər. "
        "Səbətdə miqdar olmadığına görə "
        "məhsulların kifayət etdiyini iddia etmə. "
        "Təhlükəsiz bişirmə addımları yaz. "
        "Uydurma resept təklif etmə."
    )

    if previous:
        prompt += (
            "\n\nBu reseptləri təkrar etmə:\n"
            + ", ".join(sorted(previous))
        )

    batch = await ask_gemini(
        prompt,
        RecipeBatch,
        temperature=0.5,
    )

    return clean_recipes(
        batch,
        basket_names,
        previous,
    )


def recipe_summary(recipes):
    if not recipes:
        return (
            "🍽️ Hazırda uyğun resept tapa bilmədim.",
            InlineKeyboardMarkup([
                [
                    button(
                        "🧺 Ərzaqlarım",
                        "recipe:basket",
                    )
                ]
            ]),
        )

    lines = [
        "🍽️ Nə bişirim?",
        "",
        "Səbətinə əsasən yemək təklifləri:",
        "",
    ]

    exact = [
        (index, recipe)
        for index, recipe in enumerate(recipes)
        if not recipe["missing"]
    ]

    partial = [
        (index, recipe)
        for index, recipe in enumerate(recipes)
        if recipe["missing"]
    ]

    if exact:
        lines.append("✅ Evdəki ərzaqlarla")

        for index, recipe in exact:
            lines.append(
                f"{index + 1}. {recipe['name']} "
                f"— {recipe['minutes']} dəq"
            )

        lines.append("")

    if partial:
        lines.append("🛒 Əlavə ərzaqla")

        for index, recipe in partial:
            lines.append(
                f"{index + 1}. {recipe['name']} "
                f"— {recipe['minutes']} dəq"
            )

            lines.append(
                "   Lazımdır: "
                + ", ".join(recipe["missing"])
            )

        lines.append("")

    lines.append(
        "Qeyd: Ərzaqların miqdarı səbətdə qeyd edilmir. "
        "Bişirməzdən əvvəl miqdarları yoxla."
    )

    buttons = [
        [
            button(
                f"📖 {index + 1}. {recipe['name']}"[:55],
                f"recipe:open:{index}",
            )
        ]
        for index, recipe in enumerate(recipes)
    ]

    buttons.extend([
        [
            button(
                "🔄 Başqa reseptlər",
                "recipe:more",
            )
        ],
        [
            button(
                "🧺 Ərzaqlarım",
                "recipe:basket",
            )
        ],
    ])

    return (
        "\n".join(lines),
        InlineKeyboardMarkup(buttons),
    )


def recipe_detail(recipe):
    lines = [
        f"📖 {recipe['name']}",
        "",
        f"⏱️ Təxmini vaxt: {recipe['minutes']} dəqiqə",
        "",
        "🥬 Lazım olan ərzaqlar:",
    ]

    missing_keys = {
        name.casefold()
        for name in recipe["missing"]
    }

    for ingredient in recipe["ingredients"]:
        suffix = (
            " 🛒"
            if ingredient.casefold() in missing_keys
            else ""
        )

        lines.append(
            f"• {ingredient}{suffix}"
        )

    lines.extend([
        "",
        "👨‍🍳 Hazırlanması:",
    ])

    for index, step in enumerate(
        recipe["steps"],
        1,
    ):
        lines.append(
            f"{index}. {step}"
        )

    if recipe["missing"]:
        lines.extend([
            "",
            "🛒 Çatışmayan ərzaqlar: "
            + ", ".join(recipe["missing"]),
        ])

    lines.extend([
        "",
        "Qeyd: Səbətdə miqdarlar yoxdur. "
        "Bişirməzdən əvvəl ərzaqların "
        "kifayət etdiyini yoxla.",
    ])

    youtube_url = (
        "https://www.youtube.com/results?search_query="
        + quote(
            recipe["name"] + " resepti"
        )
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Video axtar",
                url=youtube_url,
            )
        ],
        [
            button(
                "⬅️ Reseptlər",
                "recipe:back",
            )
        ],
        [
            button(
                "🧺 Ərzaqlarım",
                "recipe:basket",
            )
        ],
    ])

    return (
        "\n".join(lines),
        keyboard,
    )


async def recipe_start(update, context):
    query = update.callback_query

    if query:
        if (
            query.message is None
            or query.message.message_id
            != context.user_data.get("basket_message_id")
        ):
            await query.answer(
                "Ərzaqlarım bölməsini yenidən aç.",
                show_alert=True,
            )
            return

        await query.answer()
        reply_to = query.message

    else:
        reply_to = update.message

    user_id = update.effective_user.id
    rows = get_rows(user_id)

    if not rows:
        await reply_to.reply_text(
            "🧺 Siyahın boşdur.\n\n"
            "Əvvəlcə evində olan ərzaqları əlavə et."
        )
        return

    basket_names = [
        row[1]
        for row in rows
    ]

    context.user_data.pop(
        "recipe_state",
        None,
    )

    status = await reply_to.reply_text(
        "🍽️ Səbətinə uyğun reseptləri hazırlayıram..."
    )

    context.user_data["recipe_message_id"] = (
        status.message_id
    )

    try:
        recipes = await generate_recipes(
            basket_names,
            set(),
        )

    except Exception as error:
        await status.edit_text(
            "❌ Resept hazırlamaq mümkün olmadı.\n\n"
            + api_error_message(error)
        )
        return

    if get_rows(user_id) != rows:
        await status.edit_text(
            "Siyahın dəyişib. "
            "«Nə bişirim?» düyməsinə yenidən bas."
        )
        return

    if not recipes:
        await status.edit_text(
            "🍽️ Səbətinə uyğun, ən çox iki əlavə "
            "ərzaq tələb edən resept tapa bilmədim.\n\n"
            "Başqa ərzaq əlavə edib yenidən yoxla."
        )
        return

    history = {
        recipe["name"].casefold()
        for recipe in recipes
    }

    context.user_data["recipe_state"] = {
        "recipes": recipes,
        "basket": tuple(rows),
        "history": history,
    }

    text, keyboard = recipe_summary(
        recipes
    )

    await status.edit_text(
        text,
        reply_markup=keyboard,
    )


async def recipe_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("recipe_message_id")
    ):
        await query.answer(
            "Bu resept menyusu köhnəlib. "
            "Yenidən «Nə bişirim?» seç.",
            show_alert=True,
        )
        return

    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    # Reseptden sebete geri kecid
    if action == "basket":
        text, keyboard = basket_view(user_id)

        context.user_data["basket_message_id"] = (
            query.message.message_id
        )

        context.user_data.pop(
            "recipe_message_id",
            None,
        )

        context.user_data.pop(
            "recipe_state",
            None,
        )

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
        return

    state = context.user_data.get(
        "recipe_state"
    )

    if state is None:
        await query.message.reply_text(
            "Resept axtarışı bitib. "
            "«Nə bişirim?» düyməsinə yenidən bas."
        )
        return

    if tuple(get_rows(user_id)) != state["basket"]:
        context.user_data.pop(
            "recipe_state",
            None,
        )

        await query.edit_message_text(
            "🧺 Səbətin dəyişib.\n\n"
            "Yeni reseptlər üçün "
            "«Nə bişirim?» düyməsinə yenidən bas."
        )
        return

    # Reseptin detallarini ac
    if action == "open":
        if (
            len(parts) != 3
            or not parts[2].isdigit()
        ):
            return

        index = int(parts[2])

        if not 0 <= index < len(state["recipes"]):
            return

        text, keyboard = recipe_detail(
            state["recipes"][index]
        )

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
        return

    # Resept siyahisina geri don
    if action == "back":
        text, keyboard = recipe_summary(
            state["recipes"]
        )

        await query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
        return

    if action != "more":
        return

    # Ferqli reseptler axtar
    old_recipes = state["recipes"]

    await query.edit_message_text(
        "🔄 Fərqli reseptlər axtarıram..."
    )

    basket_names = [
        row[1]
        for row in state["basket"]
    ]

    try:
        recipes = await generate_recipes(
            basket_names,
            state["history"],
        )

    except Exception as error:
        text, keyboard = recipe_summary(
            old_recipes
        )

        await query.edit_message_text(
            "❌ Yeni resept axtarışı alınmadı.\n\n"
            + api_error_message(error)
            + "\n\n"
            + text,
            reply_markup=keyboard,
        )
        return

    if tuple(get_rows(user_id)) != state["basket"]:
        context.user_data.pop(
            "recipe_state",
            None,
        )

        await query.edit_message_text(
            "Siyahın dəyişib. "
            "Resept axtarışını yenidən başlat."
        )
        return

    if not recipes:
        text, keyboard = recipe_summary(
            old_recipes
        )

        await query.edit_message_text(
            "Bu şərtlərlə yeni fərqli resept "
            "tapa bilmədim.\n\n"
            + text,
            reply_markup=keyboard,
        )
        return

    state["recipes"] = recipes

    state["history"].update(
        recipe["name"].casefold()
        for recipe in recipes
    )

    text, keyboard = recipe_summary(
        recipes
    )

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )
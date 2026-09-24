import database as sqlite3
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from basket_ui import basket_view, get_rows
from ingredient_names import ingredient_key
from pantry_store import add_ingredients
from command_controls import clear_pending_operations


DB_PATH = Path(__file__).resolve().parent / "ingredients.db"

PAGE_SIZE = 10

# 20 hazir erzaq
STAPLES = (
    "Duz",
    "Bitki yağı",
    "Kərə yağı",
    "Su",
    "Şəkər",
    "Un",
    "Düyü",
    "Makaron",
    "Çörək",
    "Tomat pastası",
    "Kartof",
    "Soğan",
    "Sarımsaq",
    "Pomidor",
    "Kök",
    "Yumurta",
    "Süd",
    "Qatıq",
    "Pendir",
    "Qarabaşaq",
)


# Tez elave et ekranini hazirla
def quick_view(user_id, selected, page=0):
    rows = get_rows(user_id)

    # Artıq movcud olan erzaqlar
    existing = {
        ingredient_key(row[1]) for row in rows
    }

    # Movcud mehsullari secimden cixar
    available = {
        name for name in STAPLES
        if ingredient_key(name) not in existing
    }

    selected.intersection_update(available)

    total_pages = (
        len(STAPLES) + PAGE_SIZE - 1
    ) // PAGE_SIZE

    page = max(
        0,
        min(page, total_pages - 1),
    )

    start = page * PAGE_SIZE

    visible = STAPLES[
        start:start + PAGE_SIZE
    ]

    if page == 0:
        title = "🥫 Əsas ərzaqlar"
    else:
        title = "🥔 Digər gündəlik ərzaqlar"

    text = (
        "⚡ Tez əlavə et\n\n"
        f"{title}\n\n"
        "Evində olan ərzaqları seç.\n"
        "Artıq siyahında olan məhsullar "
        "təkrar əlavə edilməyəcək.\n\n"
        f"Seçilib: {len(selected)}\n"
        f"Səhifə: {page + 1}/{total_pages}"
    )

    buttons = []

    # Sehifedeki 10 mehsulu goster
    for index in range(
        start,
        start + len(visible),
    ):
        name = STAPLES[index]

        if ingredient_key(name) in existing:
            label = f"✅ {name} — siyahındadır"

        elif name in selected:
            label = f"☑️ {name}"

        else:
            label = f"☐ {name}"

        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"quick:toggle:{index}",
            )
        ])

    # Sehifeler arasinda kecid
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Əvvəlki",
                callback_data=f"quick:page:{page - 1}",
            )
        )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "Növbəti ➡️",
                callback_data=f"quick:page:{page + 1}",
            )
        )

    if navigation:
        buttons.append(navigation)

    # Secilenleri yadda saxla
    if selected:
        buttons.append([
            InlineKeyboardButton(
                f"✅ Seçilənləri əlavə et ({len(selected)})",
                callback_data="quick:save",
            )
        ])

    # Sebete qayit
    buttons.append([
        InlineKeyboardButton(
            "⬅️ Ərzaqlarım",
            callback_data="quick:back",
        )
    ])

    return text, InlineKeyboardMarkup(buttons)


# Tez elave et duymelerinin idaresi
async def quick_add_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    # Kohne menyulari blokla
    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("basket_message_id")
    ):
        await query.answer(
            "Bu menyu köhnəlib. "
            "Ərzaqlarım bölməsini yenidən aç.",
            show_alert=True,
        )
        return

    parts = query.data.split(":")
    action = parts[1]

    # Tez elave et menyusunu ac
    if action == "open":
        clear_pending_operations(context, keep=("basket_message_id",))
        selected = set()

        context.user_data["quick_selected"] = selected
        context.user_data["quick_page"] = 0

        # Evvelki yarimciq silme secimini bagla
        context.user_data.pop("delete_state", None)

        text, keyboard = quick_view(
            user_id,
            selected,
            0,
        )

    # Sebete geri qayit
    elif action == "back":
        context.user_data.pop(
            "quick_selected",
            None,
        )

        context.user_data.pop(
            "quick_page",
            None,
        )

        text, keyboard = basket_view(user_id)

    else:
        selected = context.user_data.get(
            "quick_selected"
        )

        if selected is None:
            await query.answer(
                "Seçim bitib. "
                "Tez əlavə et menyusunu yenidən aç.",
                show_alert=True,
            )
            return

        page = context.user_data.get(
            "quick_page",
            0,
        )

        # Sehife deyis
        if action == "page":
            if (
                len(parts) != 3
                or not parts[2].isdigit()
                or int(parts[2]) not in (0, 1)
            ):
                await query.answer(
                    "Yanlış səhifə."
                )
                return

            page = int(parts[2])

            context.user_data["quick_page"] = page

            text, keyboard = quick_view(
                user_id,
                selected,
                page,
            )

        # Erzaq sec veya secimden cixar
        elif action == "toggle":
            if (
                len(parts) != 3
                or not parts[2].isdigit()
            ):
                await query.answer(
                    "Yanlış seçim."
                )
                return

            index = int(parts[2])

            if (
                index < 0
                or index >= len(STAPLES)
            ):
                await query.answer(
                    "Yanlış seçim."
                )
                return

            name = STAPLES[index]

            existing = {
                ingredient_key(row[1])
                for row in get_rows(user_id)
            }

            if ingredient_key(name) in existing:
                await query.answer(
                    "Bu ərzaq artıq siyahındadır."
                )
                return

            if name in selected:
                selected.remove(name)
            else:
                selected.add(name)

            text, keyboard = quick_view(
                user_id,
                selected,
                page,
            )

        # Butun secimleri yadda saxla
        elif action == "save":
            # Secilmis mehsullari ilkin
            # siyahi sirasinda yadda saxla
            names = [
                name for name in STAPLES
                if name in selected
            ]

            if not names:
                await query.answer(
                    "Əvvəlcə ərzaq seç."
                )
                return

            added, _ = add_ingredients(user_id, names)

            context.user_data.pop(
                "quick_selected",
                None,
            )

            context.user_data.pop(
                "quick_page",
                None,
            )

            # Sebet deyisibse kohne geri
            # qaytarma imkanini legv et
            if added:
                context.user_data.pop(
                    "undo",
                    None,
                )

            count = len(get_rows(user_id))

            if added:
                added_lines = "\n".join(
                    f"• {name}"
                    for name in added
                )

                text = (
                    f"✅ {len(added)} ərzaq əlavə olundu!\n\n"
                    f"{added_lines}\n\n"
                    f"🧺 Siyahında {count} ərzaq var."
                )

            else:
                text = (
                    "Seçilən ərzaqlar artıq siyahındadır.\n\n"
                    f"🧺 Siyahında {count} ərzaq var."
                )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🧺 Ərzaqlarım",
                        callback_data="quick:back",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⚡ Yenə tez əlavə et",
                        callback_data="quick:open",
                    )
                ],
            ])

        else:
            await query.answer(
                "Yanlış əməliyyat."
            )
            return

    await query.answer()

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )
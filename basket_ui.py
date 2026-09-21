import sqlite3
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


DB_PATH = Path(__file__).resolve().parent / "ingredients.db"
PAGE_SIZE = 10


# Istifadecinin erzaqlarini getir
def get_rows(user_id):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            """
            SELECT id, name, normalized_name
            FROM ingredients
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()


# Siyahinin deyisib-deyismediyini yoxlamaq ucun
def snapshot(rows):
    return tuple(
        (row[0], row[1], row[2])
        for row in rows
    )


# Sehife nomresini duzgun araliqda saxla
def page_number(page, total):
    maximum = max(0, (total - 1) // PAGE_SIZE)
    return max(0, min(page, maximum))


# Erzaqlarim ekrani
def basket_view(user_id, page=0):
    rows = get_rows(user_id)

    # Bos siyahi
    if not rows:
        text = (
            "🧺 Ərzaqlarım\n\n"
            "Siyahın hələ boşdur.\n\n"
            "Ərzaqları yaz və ya ⚡ Tez əlavə et "
            "bölməsindən seç."
        )

        buttons = [
            [
                InlineKeyboardButton(
                    "➕ Ərzaq əlavə et",
                    callback_data="pantry:add",
                )
            ],
            [
                InlineKeyboardButton(
                    "⚡ Tez əlavə et",
                    callback_data="quick:open",
                )
            ],
        ]

        return text, InlineKeyboardMarkup(buttons)

    # Dolu siyahi
    page = page_number(page, len(rows))

    start = page * PAGE_SIZE
    visible = rows[start:start + PAGE_SIZE]

    lines = [
        f"{start + i + 1}. {row[1]}"
        for i, row in enumerate(visible)
    ]

    total_pages = (
        len(rows) + PAGE_SIZE - 1
    ) // PAGE_SIZE

    text = (
        f"🧺 Ərzaqlarım ({len(rows)})\n\n"
        + "\n".join(lines)
        + f"\n\nSəhifə: {page + 1}/{total_pages}"
    )

    buttons = []
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Əvvəlki",
                callback_data=f"pantry:page:{page - 1}",
            )
        )

    if start + PAGE_SIZE < len(rows):
        navigation.append(
            InlineKeyboardButton(
                "Növbəti ➡️",
                callback_data=f"pantry:page:{page + 1}",
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🍽️ Nə bişirim?",
            callback_data="pantry:recipe",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "➕ Əlavə et",
            callback_data="pantry:add",
        ),
        InlineKeyboardButton(
            "➖ Ərzaq sil",
            callback_data="pantry:delete",
        ),
    ])

    # Yeni duymemiz
    buttons.append([
        InlineKeyboardButton(
            "⚡ Tez əlavə et",
            callback_data="quick:open",
        )
    ])

    return text, InlineKeyboardMarkup(buttons)


# Erzaq silme secimi
def delete_view(user_id, state):
    rows = get_rows(user_id)

    page = page_number(
        state["page"],
        len(rows),
    )
    state["page"] = page

    start = page * PAGE_SIZE
    visible = rows[start:start + PAGE_SIZE]

    text = (
        "➖ Ərzaq sil\n\n"
        "Silmək istədiyin məhsulları seç.\n"
        f"Seçilib: {len(state['selected'])}"
    )

    buttons = []

    for row in visible:
        item_id, name, _ = row

        mark = (
            "✅" if item_id in state["selected"]
            else "☐"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{mark} {name}",
                callback_data=f"pantry:toggle:{item_id}",
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=f"pantry:deletepage:{page - 1}",
            )
        )

    if start + PAGE_SIZE < len(rows):
        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"pantry:deletepage:{page + 1}",
            )
        )

    if navigation:
        buttons.append(navigation)

    if state["selected"]:
        buttons.append([
            InlineKeyboardButton(
                f"🗑️ Seçilənləri sil ({len(state['selected'])})",
                callback_data="pantry:confirm",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Ləğv et",
            callback_data="pantry:cancel",
        )
    ])

    return text, InlineKeyboardMarkup(buttons)


# Esas menyudan Erzaqlarim acildiqda
async def show_basket(update, context):
    user_id = update.effective_user.id

    # Evvelki yarimciq secimleri bagla
    context.user_data.pop("delete_state", None)
    context.user_data.pop("quick_selected", None)
    context.user_data.pop("quick_page", None)

    text, keyboard = basket_view(user_id)

    message = await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["basket_message_id"] = message.message_id


# Sebetdeki duymeleri idare et
async def basket_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    # Kohne menyulari blokla
    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("basket_message_id")
    ):
        await query.answer(
            "Bu menyu köhnəlib. Ərzaqlarım bölməsini yenidən aç.",
            show_alert=True,
        )
        return

    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    rows = get_rows(user_id)

    # Sehife deyis
    if action == "page":
        page = int(parts[2])
        text, keyboard = basket_view(user_id, page)

    # Erzaq elave et
    elif action == "add":
        await query.message.reply_text(
            "➕ Ərzaqları yaz və ya şəkil göndər.\n\n"
            "Məsələn: Kartof, yumurta, pomidor"
        )
        return

    # Resept helelik hazir deyil
    elif action == "recipe":
        if rows:
            await query.message.reply_text(
                "🍽️ Resept sistemi hələ hazırlanır.\n"
                f"Siyahındakı {len(rows)} ərzaq yadda saxlanılıb."
            )
        else:
            await query.message.reply_text(
                "Əvvəlcə siyahına ərzaq əlavə et."
            )
        return

    # Silme ekranini ac
    elif action == "delete":
        if not rows:
            text, keyboard = basket_view(user_id)
        else:
            state = {
                "page": 0,
                "selected": set(),
                "snapshot": snapshot(rows),
                "confirm": False,
            }

            context.user_data["delete_state"] = state

            text, keyboard = delete_view(
                user_id,
                state,
            )

    # Son silinmeni geri qaytar
    elif action == "undo":
        undo = context.user_data.get("undo")

        if (
            not undo
            or snapshot(rows) != undo["after"]
        ):
            context.user_data.pop("undo", None)

            await query.message.reply_text(
                "Bu silmə əməliyyatını artıq "
                "geri qaytarmaq olmur."
            )
            return

        with sqlite3.connect(DB_PATH) as db:
            for item_id, name, normalized in undo["removed"]:
                db.execute(
                    """
                    INSERT INTO ingredients
                    (id, user_id, name, normalized_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        user_id,
                        name,
                        normalized,
                    ),
                )

        context.user_data.pop("undo", None)

        text, keyboard = basket_view(user_id)

    # Silme funksiyasinin diger emeliyyatlari
    else:
        state = context.user_data.get("delete_state")

        # Siyahi basqa yerde deyisibse secimi sifirla
        if (
            not state
            or snapshot(rows) != state["snapshot"]
        ):
            context.user_data.pop("delete_state", None)

            await query.message.reply_text(
                "Siyahı dəyişib. Zəhmət olmasa, "
                "silmə əməliyyatına yenidən başla."
            )

            text, keyboard = basket_view(user_id)

        # Silme ekraninin sehifesi
        elif action == "deletepage":
            state["page"] = int(parts[2])

            text, keyboard = delete_view(
                user_id,
                state,
            )

        # Erzaq secimini deyis
        elif action == "toggle":
            item_id = int(parts[2])

            valid_ids = {
                row[0] for row in rows
            }

            if item_id in valid_ids:
                if item_id in state["selected"]:
                    state["selected"].remove(item_id)
                else:
                    state["selected"].add(item_id)

            state["confirm"] = False

            text, keyboard = delete_view(
                user_id,
                state,
            )

        # Silinmeden evvel tesdiq
        elif action == "confirm":
            selected = state["selected"]

            if not selected:
                text, keyboard = delete_view(
                    user_id,
                    state,
                )
            else:
                names = [
                    row[1] for row in rows
                    if row[0] in selected
                ]

                preview = "\n".join(names[:15])

                if len(names) > 15:
                    preview += (
                        f"\n... və daha {len(names) - 15} ərzaq"
                    )

                text = (
                    f"🗑️ {len(names)} ərzaq silinsin?\n\n"
                    f"{preview}"
                )

                state["confirm"] = True

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Bəli, sil",
                            callback_data="pantry:apply",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Geri",
                            callback_data="pantry:back",
                        )
                    ],
                ])

        # Tesdiq ekranindan geri
        elif action == "back":
            state["confirm"] = False

            text, keyboard = delete_view(
                user_id,
                state,
            )

        # Silmeni legv et
        elif action == "cancel":
            context.user_data.pop("delete_state", None)

            text, keyboard = basket_view(user_id)

        # Tesdiqlenmis silme
        elif action == "apply":
            if (
                not state["confirm"]
                or not state["selected"]
            ):
                text, keyboard = delete_view(
                    user_id,
                    state,
                )
            else:
                removed = [
                    row for row in rows
                    if row[0] in state["selected"]
                ]

                with sqlite3.connect(DB_PATH) as db:
                    for item_id, _, _ in removed:
                        db.execute(
                            """
                            DELETE FROM ingredients
                            WHERE id = ? AND user_id = ?
                            """,
                            (item_id, user_id),
                        )

                after = snapshot(get_rows(user_id))

                context.user_data["undo"] = {
                    "removed": removed,
                    "after": after,
                }

                context.user_data.pop(
                    "delete_state",
                    None,
                )

                text = (
                    f"✅ {len(removed)} ərzaq silindi.\n\n"
                    f"Siyahında {len(after)} ərzaq qaldı."
                )

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "↩️ Geri qaytar",
                            callback_data="pantry:undo",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🧺 Siyahımı göstər",
                            callback_data="pantry:page:0",
                        )
                    ],
                ])

        else:
            text, keyboard = basket_view(user_id)

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )
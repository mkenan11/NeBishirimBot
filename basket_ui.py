import re
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


# Siyahinin deyisib-deyismediyini yoxla
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

    # Yeni ad deyisme duymesi
    buttons.append([
        InlineKeyboardButton(
            "✏️ Adını dəyiş",
            callback_data="pantry:rename",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⚡ Tez əlavə et",
            callback_data="quick:open",
        )
    ])

    return text, InlineKeyboardMarkup(buttons)


# Silme secimi ekrani
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


# Adi deyisilecek mehsulu secme ekrani
def rename_view(user_id, page=0):
    rows = get_rows(user_id)

    if not rows:
        return basket_view(user_id)

    page = page_number(page, len(rows))
    start = page * PAGE_SIZE
    visible = rows[start:start + PAGE_SIZE]

    total_pages = (
        len(rows) + PAGE_SIZE - 1
    ) // PAGE_SIZE

    text = (
        "✏️ Ərzağın adını dəyiş\n\n"
        "Adını dəyişmək istədiyin ərzağı seç.\n\n"
        f"Səhifə: {page + 1}/{total_pages}"
    )

    buttons = []

    for item_id, name, _ in visible:
        buttons.append([
            InlineKeyboardButton(
                f"✏️ {name}",
                callback_data=f"pantry:renamepick:{item_id}",
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Əvvəlki",
                callback_data=f"pantry:renamepage:{page - 1}",
            )
        )

    if start + PAGE_SIZE < len(rows):
        navigation.append(
            InlineKeyboardButton(
                "Növbəti ➡️",
                callback_data=f"pantry:renamepage:{page + 1}",
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Ərzaqlarım",
            callback_data="pantry:renamecancel",
        )
    ])

    return text, InlineKeyboardMarkup(buttons)


# Esas menyudan Erzaqlarim acildiqda
async def show_basket(update, context):
    user_id = update.effective_user.id

    # Yarimciq emeliyyatlari legv et
    context.user_data.pop("delete_state", None)
    context.user_data.pop("rename_target", None)
    context.user_data.pop("quick_selected", None)
    context.user_data.pop("quick_page", None)

    text, keyboard = basket_view(user_id)

    message = await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["basket_message_id"] = message.message_id


# Ad deyisme ucun yeni metni qebul et
async def handle_rename_text(update, context):
    user_id = update.effective_user.id

    state = context.user_data.get("rename_target")

    if state is None:
        return

    new_name = " ".join(update.message.text.split())

    # Bir mesaja yalniz bir erzaq adi qebul et
    if (
        not new_name
        or len(new_name) > 50
        or "," in new_name
        or ";" in new_name
        or "\n" in update.message.text
        or re.search(r"\d", new_name)
        or re.search(
            r"\b(evdə|evde|var|yoxdur|yoxdu|bitib|qalmayıb)\b",
            new_name,
            re.IGNORECASE,
        )
        or not re.fullmatch(
            r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
            new_name,
            re.UNICODE,
        )
    ):
        await update.message.reply_text(
            "❌ Yalnız bir ərzağın adını yaz.\n\n"
            "Məsələn: Qırmızı soğan\n\n"
            "Yenidən yaz və ya yuxarıdakı "
            "«Ləğv et» düyməsinə bas."
        )
        return

    new_name = (
        new_name[0].upper()
        + new_name[1:].lower()
    )

    normalized = new_name.casefold()

    # Yalniz bu istifadecinin secdiyi setri deyis
    with sqlite3.connect(DB_PATH) as db:
        current = db.execute(
            """
            SELECT name, normalized_name
            FROM ingredients
            WHERE id = ? AND user_id = ?
            """,
            (state["id"], user_id),
        ).fetchone()

        if (
            current is None
            or current[0] != state["name"]
            or current[1] != state["normalized"]
        ):
            context.user_data.pop("rename_target", None)

            await update.message.reply_text(
                "Siyahı dəyişib. Ad dəyişmə əməliyyatını "
                "yenidən başlat."
            )

            await show_basket(update, context)
            return

        # Eyni adli basqa erzaq varsa, deyisme
        duplicate = db.execute(
            """
            SELECT id
            FROM ingredients
            WHERE user_id = ?
              AND normalized_name = ?
              AND id != ?
            """,
            (user_id, normalized, state["id"]),
        ).fetchone()

        if duplicate is not None:
            await update.message.reply_text(
                f"ℹ️ «{new_name}» artıq siyahındadır.\n\n"
                "Başqa ad yaz və ya «Ləğv et» düyməsinə bas."
            )
            return

        try:
            db.execute(
                """
                UPDATE ingredients
                SET name = ?, normalized_name = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    new_name,
                    normalized,
                    state["id"],
                    user_id,
                ),
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "Bu ad artıq siyahındadır. "
                "Zəhmət olmasa, başqa ad yaz."
            )
            return

    old_name = state["name"]

    context.user_data.pop("rename_target", None)

    # Siyahi deyisibse kohne undo ve silme
    # secimi artiq istifade edilmesin
    if new_name != old_name:
        context.user_data.pop("undo", None)
        context.user_data.pop("delete_state", None)

    await update.message.reply_text(
        f"✅ Ərzağın adı dəyişdirildi!\n\n"
        f"Əvvəl: {old_name}\n"
        f"İndi: {new_name}"
    )

    # Yeni siyahini goster
    await show_basket(update, context)


# Erzaqlarim duymelerini idare et
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
            "Bu menyu köhnəlib. "
            "Ərzaqlarım bölməsini yenidən aç.",
            show_alert=True,
        )
        return

    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    rows = get_rows(user_id)

    # Sebetin sehifesini deyis
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

    # Ad deyisme secimini ac
    elif action == "rename":
        context.user_data.pop("delete_state", None)
        context.user_data.pop("rename_target", None)

        text, keyboard = rename_view(user_id)

    # Ad deyisme seciminin sehifesini deyis
    elif action == "renamepage":
        page = int(parts[2])
        text, keyboard = rename_view(user_id, page)

    # Adi deyisilecek erzaq secildi
    elif action == "renamepick":
        item_id = int(parts[2])

        target = next(
            (row for row in rows if row[0] == item_id),
            None,
        )

        if target is None:
            await query.message.reply_text(
                "Bu ərzaq artıq siyahıda yoxdur. "
                "Ərzaqlarım bölməsini yenidən aç."
            )

            text, keyboard = basket_view(user_id)
        else:
            context.user_data["rename_target"] = {
                "id": target[0],
                "name": target[1],
                "normalized": target[2],
            }

            text = (
                "✏️ Ərzağın adını dəyiş\n\n"
                f"Seçilən ərzaq: {target[1]}\n\n"
                "İndi yeni adını adi mesaj kimi yaz.\n\n"
                "Məsələn: Qırmızı soğan"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Ləğv et",
                        callback_data="pantry:renamecancel",
                    )
                ]
            ])

    # Ad deyismeni legv et
    elif action == "renamecancel":
        context.user_data.pop("rename_target", None)
        text, keyboard = basket_view(user_id)

    # Silme ekranini ac
    elif action == "delete":
        context.user_data.pop("rename_target", None)

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
            text, keyboard = delete_view(user_id, state)

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

    # Silme emeliyyatlari
    else:
        state = context.user_data.get("delete_state")

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

        # Silme sehifesini deyis
        elif action == "deletepage":
            state["page"] = int(parts[2])
            text, keyboard = delete_view(user_id, state)

        # Erzaq sec ve ya secimden cixar
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
            text, keyboard = delete_view(user_id, state)

        # Silinmeden evvel tesdiq ekrani
        elif action == "confirm":
            selected = state["selected"]

            if not selected:
                text, keyboard = delete_view(user_id, state)
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

        # Tesdiq ekranindan geri don
        elif action == "back":
            state["confirm"] = False
            text, keyboard = delete_view(user_id, state)

        # Silmeni legv et
        elif action == "cancel":
            context.user_data.pop("delete_state", None)
            text, keyboard = basket_view(user_id)

        # Secilmis erzaqlari sil
        elif action == "apply":
            if (
                not state["confirm"]
                or not state["selected"]
            ):
                text, keyboard = delete_view(user_id, state)
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

                context.user_data.pop("delete_state", None)

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
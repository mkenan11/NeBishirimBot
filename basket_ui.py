from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from command_controls import clear_pending_operations
import pantry_store

PAGE_SIZE = 10


def get_rows(user_id):
    return pantry_store.list_rows(user_id)


snapshot = pantry_store.snapshot


def page_number(page, total):
    return max(0, min(page, max(0, (total - 1) // PAGE_SIZE)))


def button(label, action):
    return InlineKeyboardButton(label, callback_data=action)


def basket_view(user_id, page=0):
    rows = get_rows(user_id)
    if not rows:
        return (
            "🧺 Ərzaqlarım\n\nSiyahın hələ boşdur.\n\n"
            "Ərzaqları yaz və ya şəklini göndər. "
            "İstəsən ⚡ Tez əlavə et bölməsindən də seç.",
            InlineKeyboardMarkup([
                [button("➕ Ərzaq əlavə et", "pantry:add")],
                [button("⚡ Tez əlavə et", "quick:open")],
            ]),
        )

    page = page_number(page, len(rows))
    start = page * PAGE_SIZE
    lines = [f"{start + i + 1}. {row[1]}" for i, row in enumerate(rows[start:start + PAGE_SIZE])]
    pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    navigation = []
    if page > 0:
        navigation.append(button("⬅️ Əvvəlki", f"pantry:page:{page - 1}"))
    if start + PAGE_SIZE < len(rows):
        navigation.append(button("Növbəti ➡️", f"pantry:page:{page + 1}"))
    buttons = []
    if navigation:
        buttons.append(navigation)
    buttons.extend([
        [button("🍽️ Nə bişirim?", "pantry:recipe")],
        [button("➕ Əlavə et", "pantry:add")],
        [button("✏️ Siyahını düzəlt", "pantry:edit")],
        [button("⚡ Tez əlavə et", "quick:open")],
        [button("🗑️ Hamısını sil", "pantry:clear")],
    ])
    return (
        f"🧺 Ərzaqlarım ({len(rows)})\n\n" + "\n".join(lines)
        + f"\n\nSəhifə: {page + 1}/{pages}",
        InlineKeyboardMarkup(buttons),
    )


def edit_view(user_id):
    if not get_rows(user_id):
        return basket_view(user_id)
    return (
        "✏️ Siyahını düzəlt\n\nNə etmək istəyirsən?",
        InlineKeyboardMarkup([
            [button("✏️ Ərzağın adını dəyiş", "pantry:rename")],
            [button("➖ Seçilən ərzaqları sil", "pantry:delete")],
            [button("⬅️ Ərzaqlarım", "pantry:editback")],
        ]),
    )


def rename_view(user_id, page=0):
    rows = get_rows(user_id)
    if not rows:
        return basket_view(user_id)
    page = page_number(page, len(rows))
    start = page * PAGE_SIZE
    buttons = [
        [button(f"✏️ {row[1]}", f"pantry:renamepick:{row[0]}")]
        for row in rows[start:start + PAGE_SIZE]
    ]
    navigation = []
    if page > 0:
        navigation.append(button("⬅️ Əvvəlki", f"pantry:renamepage:{page - 1}"))
    if start + PAGE_SIZE < len(rows):
        navigation.append(button("Növbəti ➡️", f"pantry:renamepage:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([button("❌ Ləğv et", "pantry:renamecancel")])
    pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    return (
        f"✏️ Ərzağın adını dəyiş\n\nMəhsulu seç.\n\nSəhifə: {page + 1}/{pages}",
        InlineKeyboardMarkup(buttons),
    )


def delete_view(user_id, state):
    rows = get_rows(user_id)
    page = page_number(state["page"], len(rows))
    state["page"] = page
    start = page * PAGE_SIZE
    buttons = [
        [button(
            f"{'✅' if row[0] in state['selected'] else '☐'} {row[1]}",
            f"pantry:toggle:{row[0]}",
        )]
        for row in rows[start:start + PAGE_SIZE]
    ]
    navigation = []
    if page > 0:
        navigation.append(button("⬅️", f"pantry:deletepage:{page - 1}"))
    if start + PAGE_SIZE < len(rows):
        navigation.append(button("➡️", f"pantry:deletepage:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    if state["selected"]:
        buttons.append([button(
            f"🗑️ Seçilənləri sil ({len(state['selected'])})", "pantry:confirm"
        )])
    buttons.append([button("❌ Ləğv et", "pantry:cancel")])
    return (
        f"➖ Ərzaq sil\n\nSilmək istədiyin məhsulları seç.\n"
        f"Seçilib: {len(state['selected'])}",
        InlineKeyboardMarkup(buttons),
    )


async def show_basket(update, context):
    user_id = update.effective_user.id
    clear_pending_operations(context, keep=("undo",))
    text, keyboard = basket_view(user_id)
    message = await update.message.reply_text(text, reply_markup=keyboard)
    context.user_data["basket_message_id"] = message.message_id


async def handle_rename_text(update, context):
    state = context.user_data.get("rename_target")
    if state is None:
        return
    user_id = update.effective_user.id
    new_name, problem = pantry_store.parse_single_name(update.message.text)
    if problem == "format":
        await update.message.reply_text(
            "❌ Yalnız bir ərzağın adını yaz.\nMəsələn: Qırmızı soğan\n"
            "Başqa ad yaz və ya «Ləğv et» düyməsinə bas."
        )
        return
    if problem == "name":
        await update.message.reply_text("Ərzaq adını yenidən yaz.")
        return
    try:
        changed = pantry_store.rename_ingredient(
            user_id, state["id"], new_name, expected=(state["name"], state["normalized"]),
        )
    except pantry_store.PantryChanged:
        context.user_data.pop("rename_target", None)
        await update.message.reply_text("Siyahı dəyişib. Ad dəyişməni yenidən başlat.")
        await show_basket(update, context)
        return
    except pantry_store.DuplicateName:
        await update.message.reply_text(
            f"ℹ️ «{new_name}» artıq siyahındadır. Başqa ad yaz və ya ləğv et."
        )
        return

    context.user_data.pop("rename_target", None)
    if changed:
        for key in ("undo", "delete_state", "clear_state"):
            context.user_data.pop(key, None)
    await update.message.reply_text(
        f"✅ Ərzağın adı dəyişdirildi!\n\nƏvvəl: {state['name']}\nİndi: {new_name}"
    )
    await show_basket(update, context)


async def basket_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    if (
        query.message is None
        or query.message.message_id != context.user_data.get("basket_message_id")
    ):
        await query.answer("Bu menyu köhnəlib. Ərzaqlarım bölməsini yenidən aç.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    rows = get_rows(user_id)
    if action not in ("clearapply", "clearcancel"):
        context.user_data.pop("clear_state", None)

    if action == "page":
        text, keyboard = basket_view(user_id, int(parts[2]))
    elif action == "add":
        clear_pending_operations(context, keep=("basket_message_id",))
        await query.message.reply_text(
            "➕ Ərzaqlarını əlavə et\n\n"
            "Adlarını vergüllə ayıraraq yaz və ya şəklini göndər. 📸\n"
            "Birdən çox şəkil varsa, hər birini ayrıca göndər "
            "və tanınan ərzaqları təsdiqlə.\n\n"
            "Məsələn: Kartof, yumurta, pomidor"
        )
        return
    elif action == "recipe":
        await query.message.reply_text(
            f"🍽️ Resept sistemi hələ hazırlanır.\nSiyahında {len(rows)} ərzaq var."
            if rows else "Əvvəlcə siyahına ərzaq əlavə et."
        )
        return
    elif action == "edit":
        context.user_data.pop("delete_state", None)
        context.user_data.pop("rename_target", None)
        text, keyboard = edit_view(user_id)
    elif action == "editback":
        text, keyboard = basket_view(user_id)
    elif action == "rename":
        context.user_data.pop("delete_state", None)
        context.user_data.pop("rename_target", None)
        text, keyboard = rename_view(user_id)
    elif action == "renamepage":
        text, keyboard = rename_view(user_id, int(parts[2]))
    elif action == "renamepick":
        item_id = int(parts[2])
        target = next((row for row in rows if row[0] == item_id), None)
        if target is None:
            text, keyboard = rename_view(user_id)
        else:
            context.user_data["rename_target"] = {
                "id": target[0], "name": target[1], "normalized": target[2]
            }
            text = (
                f"✏️ Ərzağın adını dəyiş\n\nSeçilən: {target[1]}\n\n"
                "Yeni adı adi mesaj kimi yaz. Məsələn: Qırmızı soğan"
            )
            keyboard = InlineKeyboardMarkup([
                [button("❌ Ləğv et", "pantry:renamecancel")]
            ])
    elif action == "renamecancel":
        context.user_data.pop("rename_target", None)
        text, keyboard = edit_view(user_id)
    elif action == "delete":
        context.user_data.pop("rename_target", None)
        if rows:
            state = {"page": 0, "selected": set(), "snapshot": snapshot(rows), "confirm": False}
            context.user_data["delete_state"] = state
            text, keyboard = delete_view(user_id, state)
        else:
            text, keyboard = basket_view(user_id)
    elif action == "clear":
        context.user_data.pop("delete_state", None)
        context.user_data.pop("rename_target", None)
        if not rows:
            text, keyboard = basket_view(user_id)
        else:
            context.user_data["clear_state"] = {"snapshot": snapshot(rows)}
            text = (
                f"🗑️ Bütün ərzaqlar silinsin?\n\n"
                f"Siyahındakı {len(rows)} ərzağın hamısı silinəcək."
            )
            keyboard = InlineKeyboardMarkup([
                [button("🗑️ Bəli, hamısını sil", "pantry:clearapply")],
                [button("❌ Xeyr, ləğv et", "pantry:clearcancel")],
            ])
    elif action == "clearcancel":
        context.user_data.pop("clear_state", None)
        text, keyboard = basket_view(user_id)
    elif action == "clearapply":
        state = context.user_data.pop("clear_state", None)
        if state is None:
            text, keyboard = basket_view(user_id)
        else:
            try:
                removed = pantry_store.clear_ingredients(user_id, state["snapshot"])
            except pantry_store.PantryChanged:
                removed = None
            if removed is None:
                text = "Siyahı dəyişib. Hamısını sil əməliyyatını yenidən başlat."
                keyboard = InlineKeyboardMarkup([
                    [button("🧺 Ərzaqlarım", "pantry:page:0")]
                ])
            else:
                context.user_data["undo"] = {"removed": removed, "after": ()}
                text = f"✅ {len(removed)} ərzaq silindi.\n\nSiyahın indi boşdur."
                keyboard = InlineKeyboardMarkup([
                    [button("↩️ Hamısını geri qaytar", "pantry:undo")],
                    [button("🧺 Ərzaqlarım", "pantry:page:0")],
                ])
    elif action == "undo":
        undo = context.user_data.get("undo")
        if not undo:
            text, keyboard = basket_view(user_id)
        else:
            restored = pantry_store.restore_ingredients(user_id, undo["removed"], undo["after"])
            context.user_data.pop("undo", None)
            text, keyboard = basket_view(user_id)
            if not restored:
                await query.message.reply_text(
                    "Siyahı dəyişib. Bu silməni artıq geri qaytarmaq olmur."
                )
    else:
        state = context.user_data.get("delete_state")
        if not state or snapshot(rows) != state["snapshot"]:
            context.user_data.pop("delete_state", None)
            text, keyboard = basket_view(user_id)
            await query.message.reply_text("Siyahı dəyişib. Silməni yenidən başlat.")
        elif action == "deletepage":
            state["page"] = int(parts[2])
            text, keyboard = delete_view(user_id, state)
        elif action == "toggle":
            item_id = int(parts[2])
            if item_id in {row[0] for row in rows}:
                if item_id in state["selected"]:
                    state["selected"].remove(item_id)
                else:
                    state["selected"].add(item_id)
            state["confirm"] = False
            text, keyboard = delete_view(user_id, state)
        elif action == "confirm":
            names = [row[1] for row in rows if row[0] in state["selected"]]
            if not names:
                text, keyboard = delete_view(user_id, state)
            else:
                preview = "\n".join(names[:15])
                if len(names) > 15:
                    preview += f"\n... və daha {len(names) - 15} ərzaq"
                state["confirm"] = True
                text = f"🗑️ {len(names)} ərzaq silinsin?\n\n{preview}"
                keyboard = InlineKeyboardMarkup([
                    [button("✅ Bəli, sil", "pantry:apply")],
                    [button("⬅️ Geri", "pantry:back")],
                ])
        elif action == "back":
            state["confirm"] = False
            text, keyboard = delete_view(user_id, state)
        elif action == "cancel":
            context.user_data.pop("delete_state", None)
            text, keyboard = edit_view(user_id)
        elif action == "apply":
            if not state["confirm"] or not state["selected"]:
                text, keyboard = delete_view(user_id, state)
            else:
                try:
                    removed = pantry_store.delete_ingredients(user_id, state["selected"], state["snapshot"])
                except pantry_store.PantryChanged:
                    removed = None
                context.user_data.pop("delete_state", None)
                if removed is None:
                    text, keyboard = basket_view(user_id)
                    await query.message.reply_text("Siyahı dəyişib. Silməni yenidən başlat.")
                else:
                    after = snapshot(get_rows(user_id))
                    context.user_data["undo"] = {"removed": removed, "after": after}
                    text = (
                        f"✅ {len(removed)} ərzaq silindi.\n\n"
                        f"Siyahında {len(after)} ərzaq qaldı."
                    )
                    keyboard = InlineKeyboardMarkup([
                        [button("↩️ Geri qaytar", "pantry:undo")],
                        [button("🧺 Siyahımı göstər", "pantry:page:0")],
                    ])
        else:
            text, keyboard = basket_view(user_id)

    await query.edit_message_text(text, reply_markup=keyboard)

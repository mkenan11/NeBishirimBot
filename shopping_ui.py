"""Shopping list screens and explicit, idempotent purchased flags."""

import asyncio
import logging
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup
from command_controls import clear_pending_operations
from shopping_store import list_items, set_bought, remove_bought

LOG = logging.getLogger(__name__)
PAGE_SIZE = 8


async def shopping_view(user_id, page=0):
    items = await asyncio.to_thread(list_items, user_id)
    page = max(0, min(page, max(0, (len(items) - 1) // PAGE_SIZE)))
    lines = ["🛒 Alış-veriş siyahısı", "", "Alınanları ✅ işarələ. Bu, ərzağı səbətə əlavə etmir.",
             "Miqdarları seçdiyin reseptdə yoxla.", ""]
    buttons = []
    for item_id, name, bought in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        label = ("✅ " if bought else "☐ ") + name
        lines.append(label)
        buttons.append([Button(label, callback_data=f"shop:set:{item_id}:{int(not bought)}:{page}")])
    if not items:
        lines.append("Siyahı boşdur. Reseptdə «🛒 Alınacaqları siyahıya əlavə et» düyməsinə bas.")
    nav = []
    if page:
        nav.append(Button("⬅️ Əvvəlki", callback_data=f"shop:page:{page-1}"))
    if (page + 1) * PAGE_SIZE < len(items):
        nav.append(Button("Növbəti ➡️", callback_data=f"shop:page:{page+1}"))
    if nav:
        buttons.append(nav)
    if any(item[2] for item in items):
        buttons.append([Button("🧹 Alınanları təmizlə", callback_data="shop:clearask")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None


async def show_shopping(update, context):
    clear_pending_operations(context)
    text, keyboard = await shopping_view(update.effective_user.id)
    sent = await update.effective_message.reply_text(text, reply_markup=keyboard)
    context.user_data["shopping_message_id"] = sent.message_id


async def shopping_click(update, context):
    q = update.callback_query
    if not q.message or q.message.message_id != context.user_data.get("shopping_message_id"):
        await q.answer("Alış-veriş siyahısını yenidən aç.", show_alert=True)
        return
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    user_id = q.from_user.id
    try:
        if action == "clearask":
            import secrets
            token = secrets.token_hex(6)
            context.user_data["shopping_clear_token"] = token
            await q.answer()
            await q.edit_message_text("✅ Alınmış kimi işarələdiyin ərzaqlar siyahıdan silinsin?",
                reply_markup=InlineKeyboardMarkup([
                    [Button("Bəli, təmizlə", callback_data=f"shop:clear:{token}")],
                    [Button("Ləğv et", callback_data="shop:page:0")],
                ]))
            return
        page = 0
        if action == "clear" and len(parts) == 3:
            if parts[2] != context.user_data.get("shopping_clear_token"):
                await q.answer("Bu təsdiq köhnəlib.", show_alert=True)
                return
            await asyncio.to_thread(remove_bought, user_id)
        elif action == "set" and len(parts) == 5 and parts[3] in ("0", "1"):
            item_id, page = int(parts[2]), int(parts[4])
            if item_id <= 0 or page < 0:
                raise ValueError
            await asyncio.to_thread(set_bought, user_id, item_id, parts[3] == "1")
        elif action == "page" and len(parts) == 3:
            page = int(parts[2])
        else:
            raise ValueError
        context.user_data.pop("shopping_clear_token", None)
        text, keyboard = await shopping_view(user_id, page)
        await q.answer()
        from ui_utils import edit_query
        await edit_query(q, text, keyboard)
    except ValueError:
        await q.answer("Düymə düzgün deyil. Siyahını yenidən aç.", show_alert=True)

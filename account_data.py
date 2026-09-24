"""Explicit deletion of this bot's user data, with anonymous delivery receipts."""

import asyncio
import os
import secrets
import time
import psycopg
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup, ReplyKeyboardRemove
from command_controls import clear_pending_operations


def delete_account(user_id, update_id=None):
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, prepare_threshold=None) as db:
        # Same short-transaction lock as pantry mutations.
        from database import LOCK_ID
        db.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("shopping:" + str(user_id),))
        for table in ("ingredients", "favorite_recipes", "shopping_items", "bot_sessions", "users"):
            # The identifiers are fixed in source, never supplied by the user.
            db.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
        db.execute("UPDATE processed_updates SET user_id = NULL WHERE user_id = %s", (user_id,))
        if update_id is not None:
            db.execute("""INSERT INTO processed_updates (update_id, user_id, delivery_status)
                VALUES (%s, NULL, 'completed') ON CONFLICT (update_id)
                DO UPDATE SET user_id = NULL, delivery_status = 'completed'""", (update_id,))


async def request_deletion(update, context):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Məlumatlarını silmək üçün botla şəxsi söhbətdə /delete_my_data yaz.")
        return
    clear_pending_operations(context)
    token = secrets.token_hex(8)
    sent = await update.effective_message.reply_text(
        "🗑️ Bütün məlumatlarım silinsin?\n\n"
        "Səbətin, seçilmiş reseptlərin, alış-veriş siyahın, seçimlərin və sessiyan silinəcək. "
        "Bu əməliyyatı geri qaytarmaq olmur.\n\n"
        "Telegram söhbət tarixçəsi və xarici xidmətlərin qeydləri bu düymə ilə silinmir. "
        "Təkrar çatdırılmanı bloklamaq üçün istifadəçi ID-si olmayan texniki yeniləmə qeydi qalır.",
        reply_markup=InlineKeyboardMarkup([
            [Button("Bəli, bütün məlumatlarımı sil", callback_data=f"account:delete:{token}")],
            [Button("Ləğv et", callback_data=f"account:cancel:{token}")],
        ]))
    context.user_data["delete_account_state"] = {
        "token": token, "message_id": sent.message_id,
        "expires": time.time() + 300, "user_id": update.effective_user.id,
    }


async def account_click(update, context):
    q = update.callback_query
    state = context.user_data.get("delete_account_state")
    parts = q.data.split(":")
    if (not state or not q.message or len(parts) != 3
        or q.message.message_id != state["message_id"]
        or q.from_user.id != state["user_id"] or parts[2] != state["token"]
        or time.time() > state["expires"] or update.effective_chat.type != "private"):
        await q.answer("Təsdiq köhnəlib. /delete_my_data ilə yenidən başlat.", show_alert=True)
        return
    if parts[1] == "cancel":
        context.user_data.pop("delete_account_state", None)
        await q.answer()
        await q.edit_message_text("Məlumatların silinməsi ləğv edildi.")
        return
    if parts[1] != "delete":
        await q.answer("Yanlış əməliyyat.")
        return
    await asyncio.to_thread(delete_account, q.from_user.id, update.update_id)
    context.user_data.clear()
    context.application.bot_data["_account_deleted_update"] = update.update_id
    await q.answer()
    await q.edit_message_text("✅ Botda saxlanmış hesab məlumatların silindi.")
    await q.message.reply_text("Yenidən başlamaq üçün /start yaz.", reply_markup=ReplyKeyboardRemove())

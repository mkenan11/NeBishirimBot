"""Do not expose provider credentials or raw exceptions in Telegram messages."""

import logging

LOG = logging.getLogger(__name__)


async def report_error(update, context):
    context.application.bot_data["_worker_error"] = True
    LOG.error("Handler failed for update %s: %s", getattr(update, "update_id", None), type(context.error).__name__)
    if update is None or context.application.bot_data.get("_account_deleted_update") == update.update_id:
        return
    text = ("Əməliyyat tamamlana bilmədi. Dəyişiklik qismən saxlanmış ola bilər. "
            "Menyunu yenidən açıb nəticəni yoxla; lazım olsa yenidən cəhd et.")
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
    except Exception as error:
        LOG.warning("Error notice unavailable: %s", type(error).__name__)

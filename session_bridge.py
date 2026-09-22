"""Telegram sessiyalarını Neon ilə əlaqələndirir."""

import asyncio
import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, TypeHandler

from session_store import load_session, save_session
from command_controls import install_command_controls


LOG = logging.getLogger(__name__)


async def load_state(update, context):
    """Telegram əməliyyatından əvvəl sessiyanı oxu."""

    user = update.effective_user

    if user is None or context.user_data is None:
        return

    try:
        saved = await asyncio.to_thread(
            load_session,
            user.id,
        )

    except Exception:
        LOG.exception("Sessiya oxunmadı.")

        context.application.bot_data["_session_failed"] = True

        if update.callback_query:
            await update.callback_query.answer(
                "Bağlantı problemi. Bir az sonra yenidən cəhd et.",
                show_alert=True,
            )

        elif update.effective_message:
            await update.effective_message.reply_text(
                "Bağlantı problemi. Bir az sonra yenidən cəhd et."
            )

        raise ApplicationHandlerStop

    context.user_data.clear()
    context.user_data.update(saved)


async def save_state(update, context):
    """Telegram əməliyyatından sonra sessiyanı saxla."""

    user = update.effective_user

    if user is None or context.user_data is None:
        return

    try:
        await asyncio.to_thread(
            save_session,
            user.id,
            dict(context.user_data),
        )

    except Exception:
        LOG.exception("Sessiya saxlanmadı.")

        context.application.bot_data["_session_failed"] = True

        if update.effective_message:
            await update.effective_message.reply_text(
                "Sessiya yadda saxlanmadı. "
                "Bir az sonra yenidən cəhd et."
            )


def install_session_handlers(application):
    """Sessiyanı oxu, komandaları icra et, sonra saxla."""

    # İlk olaraq Neon-dan vəziyyəti oxuyuruq.
    application.add_handler(
        TypeHandler(Update, load_state),
        group=-2,
    )

    # Fasilə yoxlaması və komandalar.
    install_command_controls(application)

    # Əməliyyatdan sonra Neon-a yazırıq.
    application.add_handler(
        TypeHandler(Update, save_state),
        group=1,
    )
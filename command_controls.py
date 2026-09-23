"""NeBishirim - Telegram komandaları və fasilə rejimi."""

import re

from telegram import ReplyKeyboardRemove
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    TypeHandler,
)


PAUSED_KEY = "bot_paused_v2"

# Yalnız yarımçıq əməliyyatların vəziyyətini təmizləyirik.
# İstifadəçinin Neon-dakı ərzaqları bu siyahıya daxil deyil.
TEMPORARY_KEYS = (
    "photo_state",
    "photo_message_id",
    "pending_names",
    "pending_message_id",
    "rename_target",
    "basket_message_id",
    "delete_state",
    "clear_state",
    "undo",
    "quick_selected",
    "quick_page",
    "recipe_state",
    "recipe_message_id",
    "help_message_id",
    "favorites_message_id",
)


def clear_pending_operations(context):
    """Yarımçıq əməliyyatları ləğv edir, bazaya toxunmur."""

    if context.user_data is None:
        return

    for key in TEMPORARY_KEYS:
        context.user_data.pop(key, None)


async def stop_command(update, context):
    """İstifadəçi üçün botu fasilə rejiminə keçirir."""

    if context.user_data is None:
        return

    clear_pending_operations(context)

    context.user_data[PAUSED_KEY] = True

    await update.effective_message.reply_text(
        "⏸️ Bot fasiləyə keçirildi.\n\n"
        "Ərzaqların və saxlanmış məlumatların silinməyib.\n\n"
        "Yenidən istifadə etmək üçün /start yaz.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cancel_command(update, context):
    """Cari əməliyyatı ləğv edir."""

    clear_pending_operations(context)

    # Mövcud əsas menyunu təkrar göstəririk.
    from bot import MENU

    await update.effective_message.reply_text(
        "✅ Cari əməliyyat ləğv edildi.\n\n"
        "Ərzaq siyahın dəyişdirilmədi. "
        "Əsas menyudan davam edə bilərsən.",
        reply_markup=MENU,
    )


async def help_command(update, context):
    """Mövcud Kömək bölməsini açır."""

    from bot import show_help

    await show_help(update, context)


async def pause_guard(update, context):
    """
    Fasilə rejimində adi mesajları və düymələri bloklayır.

    /start fasiləni bitirir.
    /stop fasilə rejimində də işləyə bilər.
    """

    user = update.effective_user

    if user is None or context.user_data is None:
        return

    if not context.user_data.get(PAUSED_KEY, False):
        return

    message = update.effective_message

    text = (
        message.text.strip()
        if message is not None and message.text
        else ""
    )

    is_start = bool(
        re.match(
            r"^/start(?:@\w+)?(?:\s|$)",
            text,
            flags=re.IGNORECASE,
        )
    )

    is_stop = bool(
        re.match(
            r"^/stop(?:@\w+)?(?:\s|$)",
            text,
            flags=re.IGNORECASE,
        )
    )

    if is_start:
        # Sonrakı qrupdakı mövcud /start handler-i
        # menyunu açacaq. Sessiya isə Neon-da saxlanacaq.
        context.user_data.pop(PAUSED_KEY, None)
        return

    if is_stop:
        return

    if update.callback_query:
        await update.callback_query.answer(
            "Bot fasilədədir. Davam etmək üçün /start yaz.",
            show_alert=True,
        )

    elif message is not None:
        await message.reply_text(
            "⏸️ Bot hazırda fasilədədir.\n\n"
            "Davam etmək üçün /start yaz."
        )

    # Botun digər handler-lərinin işləməsinə icazə vermirik.
    raise ApplicationHandlerStop


def install_command_controls(application):
    """Fasilə yoxlamasını və komandaları qeydiyyata alır."""

    # Sessiya yükləndikdən sonra, əsas handler-lərdən əvvəl.
    application.add_handler(
        TypeHandler(object, pause_guard),
        group=-1,
    )

    # Bu handler-lər bot.py-dəki ümumi mesaj
    # handler-lərindən əvvəl qeydiyyata alınır.
    application.add_handler(
        CommandHandler("help", help_command),
        group=0,
    )

    application.add_handler(
        CommandHandler("cancel", cancel_command),
        group=0,
    )

    application.add_handler(
        CommandHandler("stop", stop_command),
        group=0,
    )

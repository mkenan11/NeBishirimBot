"""NeBishirim - secilmis reseptlerin Telegram interfeysi."""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from favorites_store import (
    count_favorites,
    delete_favorite,
    get_favorite,
    list_favorites,
)
from recipes import detail_text, video_url, key
from ui_utils import with_current_missing
from basket_ui import get_rows
from command_controls import clear_pending_operations


LOG = logging.getLogger(__name__)

PAGE_SIZE = 5


def button(label, callback):
    return InlineKeyboardButton(
        label,
        callback_data=callback,
    )


def favorites_keyboard(items, page, total):
    """Secilmis reseptlerin siyahi duymeleri."""

    rows = []

    for item in items:
        rows.append([
            button(
                "📖 " + item["name"][:45],
                f"favorite:open:{item['id']}:{page}",
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            button(
                "⬅️ Əvvəlki",
                f"favorite:page:{page - 1}",
            )
        )

    if (page + 1) * PAGE_SIZE < total:
        navigation.append(
            button(
                "Növbəti ➡️",
                f"favorite:page:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    return InlineKeyboardMarkup(rows) if rows else None


async def favorites_view(user_id, page=0):
    """Neon-dan secilmis reseptleri oxuyur."""

    total = await asyncio.to_thread(
        count_favorites,
        user_id,
    )

    if total == 0:
        return (
            "⭐ Seçilmiş reseptlər\n\n"
            "Hələ heç bir resept yadda saxlamamısan.\n\n"
            "Bəyəndiyin resepti açıb "
            "«⭐ Seçilmişlərə əlavə et» düyməsinə basa bilərsən.",
            None,
        )

    max_page = (total - 1) // PAGE_SIZE
    page = max(0, min(int(page), max_page))

    items = await asyncio.to_thread(
        list_favorites,
        user_id,
        PAGE_SIZE,
        page * PAGE_SIZE,
    )

    lines = [
        "⭐ Seçilmiş reseptlər",
        "",
        f"Cəmi: {total} resept",
        f"Səhifə: {page + 1}/{max_page + 1}",
        "",
    ]

    for index, item in enumerate(
        items,
        start=page * PAGE_SIZE + 1,
    ):
        lines.append(
            f"{index}. {item['name']} "
            f"— təx. {item['total']} dəq · {item.get('servings', 2)} nəfər"
            + (f" · {item['species']}" if item.get("species") else "")
        )

    lines.extend([
        "",
        "Ətraflı baxmaq üçün resepti seç.",
    ])

    return (
        "\n".join(lines),
        favorites_keyboard(items, page, total),
    )


async def show_favorites(update, context):
    """Esas menyudan ve /favorites komandasindan acilir."""

    clear_pending_operations(context)
    user_id = update.effective_user.id

    try:
        text, keyboard = await favorites_view(user_id)

    except Exception:
        LOG.exception("Secilmis reseptler yuklenmedi.")

        await update.effective_message.reply_text(
            "❌ Seçilmiş reseptlər açılmadı. "
            "Bir az sonra yenidən cəhd et."
        )
        return

    message = await update.effective_message.reply_text(
        text,
        reply_markup=keyboard,
    )

    context.user_data["favorites_message_id"] = (
        message.message_id
    )


def favorite_detail_keyboard(recipe_id, page, recipe):
    """Saxlanmis tam reseptin duymeleri."""

    rows = []
    rows.extend([
        [
            InlineKeyboardButton(
                "▶️ YouTube-da hazırlanmasına bax",
                url=video_url(recipe["name"]),
            )
        ],
        [
            button(
                "🗑️ Seçilmişlərdən sil",
                f"favorite:deleteask:{recipe_id}:{page}",
            )
        ],
        [
            button(
                "⬅️ Seçilmiş reseptlər",
                f"favorite:page:{page}",
            )
        ],
    ])
    return InlineKeyboardMarkup(rows)


async def favorite_click(update, context):
    """Seçilmiş reseptlerin duymelerini idare edir."""

    query = update.callback_query
    user_id = query.from_user.id

    if (
        query.message is None
        or query.message.message_id
        != context.user_data.get("favorites_message_id")
    ):
        await query.answer(
            "Bu menyu köhnəlib. "
            "Seçilmiş reseptləri yenidən aç.",
            show_alert=True,
        )
        return

    parts = query.data.split(":")

    if len(parts) < 3:
        await query.answer(
            "Düymə məlumatı yanlışdır.",
            show_alert=True,
        )
        return

    action = parts[1]

    if action == "shop":
        await query.answer("Alış-veriş bölməsi menyudan çıxarılıb. Çatışmayan ərzaqlar reseptdə göstərilir.", show_alert=True)
        return

    try:
        if action == "page" and len(parts) == 3:
            page = int(parts[2])

            text, keyboard = await favorites_view(
                user_id,
                page,
            )

            await query.answer()

            await query.edit_message_text(
                text,
                reply_markup=keyboard,
            )
            return

        if action not in (
            "open",
            "deleteask",
            "deleteconfirm",
        ) or len(parts) != 4:
            await query.answer(
                "Düymə məlumatı yanlışdır.",
                show_alert=True,
            )
            return

        recipe_id = int(parts[2])
        page = int(parts[3])

        if recipe_id <= 0 or page < 0:
            await query.answer(
                "Düymə məlumatı yanlışdır.",
                show_alert=True,
            )
            return

        if action == "deleteconfirm":
            deleted = await asyncio.to_thread(
                delete_favorite,
                user_id,
                recipe_id,
            )

            text, keyboard = await favorites_view(
                user_id,
                page,
            )

            if deleted:
                text = "✅ Resept seçilmişlərdən silindi.\n\n" + text
            else:
                text = "Bu resept artıq siyahıda yoxdur.\n\n" + text

            await query.answer()

            await query.edit_message_text(
                text,
                reply_markup=keyboard,
            )
            return

        recipe = await asyncio.to_thread(
            get_favorite,
            user_id,
            recipe_id,
        )

        if recipe is None:
            text, keyboard = await favorites_view(
                user_id,
                page,
            )

            await query.answer(
                "Resept tapılmadı.",
                show_alert=True,
            )

            await query.edit_message_text(
                text,
                reply_markup=keyboard,
            )
            return

        if action == "open":
            rows = await asyncio.to_thread(get_rows, user_id)
            recipe = with_current_missing(recipe, rows)
            await query.answer()
            text = detail_text(recipe)
            if len(text) > 3900:
                text = detail_text(recipe, compact_missing=True)

            await query.edit_message_text(
                text,
                reply_markup=favorite_detail_keyboard(
                    recipe_id,
                    page,
                    recipe,
                ),
            )
            return

        if action == "deleteask":
            await query.answer()

            await query.edit_message_text(
                "🗑️ Bu resept seçilmişlərdən silinsin?\n\n"
                f"📖 {recipe['name']}\n\n"
                "Bu əməliyyat səbətdəki ərzaqlara "
                "toxunmayacaq.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        button(
                            "🗑️ Bəli, sil",
                            f"favorite:deleteconfirm:{recipe_id}:{page}",
                        )
                    ],
                    [
                        button(
                            "❌ Ləğv et",
                            f"favorite:open:{recipe_id}:{page}",
                        )
                    ],
                ]),
            )
            return

    except (ValueError, TypeError):
        await query.answer(
            "Düymə məlumatı yanlışdır.",
            show_alert=True,
        )

    except Exception:
        LOG.exception("Secilmis resept emeliyyati ugursuz oldu.")

        await query.answer(
            "Əməliyyat alınmadı. "
            "Bir az sonra yenidən cəhd et.",
            show_alert=True,
        )

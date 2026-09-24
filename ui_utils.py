"""Telegram idempotent screen updates and shared recipe projections."""

from telegram.error import BadRequest
from ingredient_names import ingredient_key


async def edit_query(query, text, keyboard=None):
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


def with_current_missing(recipe, rows):
    available = {ingredient_key(row[1]) for row in rows} | {"su"}
    return dict(recipe, missing=[item["name"] for item in recipe["ingredients"]
        if ingredient_key(item["name"]) not in available])


async def edit_markup(query, keyboard):
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

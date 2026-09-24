"""Shared ingredient spelling rules; do not equate different food varieties."""

import re
import unicodedata

NAME_PATTERN = re.compile(r"[^\W\d_]+(?:[ -][^\W\d_]+)*", re.UNICODE)
ALIASES = {
    "kartf": "kartof", "yumrta": "yumurta", "pomdor": "pomidor",
    "sogan": "soğan", "sarmisaq": "sarımsaq", "duyu": "düyü",
    "seker": "şəkər", "ispanaq": "ispanaq", "ıspanaq": "ispanaq",
}
WATER = {"su", "adi su", "içməli su", "təmiz su", "isti su", "soyuq su", "qaynar su"}


def fold(value):
    return " ".join(unicodedata.normalize("NFC", value).replace("İ", "i").casefold().replace("i\u0307", "i").split())


def ingredient_key(value):
    value = fold(value)
    value = ALIASES.get(value, value)
    return "su" if value in WATER else value


def normalize_name(value):
    if not isinstance(value, str):
        return None
    value = ingredient_key(value)
    if not value or len(value) > 50 or not NAME_PATTERN.fullmatch(value):
        return None
    return ("İ" if value[0] == "i" else value[0].upper()) + value[1:]


def split_ingredients(text):
    # A decimal comma is part of a quantity, not a new ingredient.
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.split(r"[,;\n]|\s+və\s+", text, flags=re.IGNORECASE)

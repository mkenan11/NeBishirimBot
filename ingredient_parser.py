"""Mətndən ərzaq siyahısının çıxarılması: Telegram və web eyni qaydaları işlədir."""

import re

from ingredient_names import ingredient_key, normalize_name, split_ingredients
from quick_add import STAPLES

KNOWN = {ingredient_key(name) for name in STAPLES} | {
    ingredient_key(name)
    for name in (
        "Toyuq", "Toyuq filesi", "Mal əti", "Qoyun əti",
        "Ət", "Balıq", "Qiymə", "Kolbasa", "Sosiska",
        "Noxud", "Lobya", "Mərci", "Yaşıl noxud",
        "Qarğıdalı", "Göbələk", "Badımcan", "Bibər",
        "Yaşıl bibər", "Qırmızı bibər", "Xiyar",
        "Kələm", "Gül kələmi", "Brokoli", "İspanaq",
        "Kahı", "Cəfəri", "Şüyüd", "Keşniş", "Nanə",
        "Limon", "Alma", "Banan", "Portağal", "Bal",
        "Mürəbbə", "Qaymaq", "Xama", "Kəsmik",
        "Mozzarella", "Yulaf", "Bulgur", "İrmik",
        "Nişasta", "Sirkə", "Zeytun yağı",
        "Günəbaxan yağı", "Qara istiot", "İstiot",
        "Paprika", "Zirə", "Darçın", "Dəfnə yarpağı",
        "Mayonez", "Ketçup", "Xardal", "Pomidor püresi",
        "Tomat", "Qırmızı soğan", "Yaşıl soğan",
        "Turşu", "Zeytun", "Lavaş", "Yumurta ağı",
    )
}

TYPO_FIXES = {
    "kartf": "Kartof",
    "yumrta": "Yumurta",
    "pomdor": "Pomidor",
    "sogan": "Soğan",
    "sarmisaq": "Sarımsaq",
    "duyu": "Düyü",
    "seker": "Şəkər",
}

NEGATIVE = re.compile(
    r"\b(yoxdur|yoxdu|yox|bitib|qalmayıb|qalmayib)\b",
    re.IGNORECASE,
)

NAME_PATTERN = re.compile(
    r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
    re.UNICODE,
)


def parse_ingredients(text):
    if len(text) > 1500:
        return None

    parts = split_ingredients(text)

    if len(parts) > 30:
        return None

    known = []
    unknown = []
    skipped = []
    invalid = []
    corrected = []
    seen = set()

    for part in parts:
        original = part.strip(" \t\r\n.!?،؛")

        if not original:
            continue

        if NEGATIVE.search(original):
            skipped.append(original)
            continue

        name = re.sub(
            r"^(?:evdə|evde|məndə|mende)\s+",
            "",
            original,
            flags=re.IGNORECASE,
        )

        name = re.sub(
            r"\s+(?:var|vardır|vardir)$",
            "",
            name,
            flags=re.IGNORECASE,
        )

        name = re.sub(
            r"^\d+(?:[.,]\d+)?\s*"
            r"(?:(?:ədəd|dənə|qram|q|kq|kg|kilo|litr|ml)\s+)?",
            "",
            name,
            flags=re.IGNORECASE,
        )

        name = " ".join(name.split()).strip(".!? ")

        if (
            not name
            or len(name) > 50
            or not NAME_PATTERN.fullmatch(name)
        ):
            invalid.append(original)
            continue

        raw_name = name[0].upper() + name[1:].lower()
        name = normalize_name(raw_name)
        if name is None:
            invalid.append(original)
            continue
        normalized = ingredient_key(name)

        if normalized in seen:
            continue

        seen.add(normalized)

        if name != raw_name:
            corrected.append(f"{raw_name} → {name}")

        if normalized in KNOWN:
            known.append(name)
        else:
            unknown.append(name)

    return known, unknown, skipped, invalid, corrected

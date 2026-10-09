"""Mətndən ərzaq siyahısının çıxarılması: Telegram və web eyni qaydaları işlədir."""

import re

from ingredient_names import fold, ingredient_key, normalize_name, split_ingredients
from quick_add import STAPLES

# Tanınan ərzaqlar: bunlar təsdiqsiz əlavə olunur və yazılış düzəlişi üçün lüğətdir.
VOCABULARY = tuple(STAPLES) + (
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
    # Gündəlik ərzaqlar (çox vaxt Azərbaycan hərfləri olmadan yazılanlar daxil).
    "Yağ", "Sosis", "Vetçina", "Toyuq budu", "Toyuq qanadı", "Hind toyuğu",
    "Qızılbalıq", "Losos", "Tuna", "Skumbriya", "Krevet",
    "Kərəviz", "Turp", "Çuğundur", "Balqabaq", "Qabaq", "Yerkökü",
    "Brüssel kələmi", "Qırmızı kələm", "Bamya", "Qarğıdalı unu",
    "Reyhan", "Tərxun", "Kəklikotu", "Sumaq", "Zəfəran", "Mixək",
    "Zəncəfil", "Zərdəçal", "Qırmızı istiot", "Acı bibər",
    "Ərik", "Şaftalı", "Gilas", "Albalı", "Üzüm", "Nar", "Heyva", "Armud",
    "Kivi", "Qovun", "Qarpız", "Çiyələk", "Moruq", "Mandarin", "Naringi",
    "Qreypfrut", "Ananas", "Avokado", "Gavalı", "Alça", "Xurma", "Əncir",
    "Qoz", "Fındıq", "Badam", "Fıstıq", "Kişmiş", "Quru ərik", "Küncüt",
    "Şəhriyyə", "Vermişel", "Kuskus", "Yarma", "Qarabaşaq yarması",
    "Xəmir", "Yufka", "Maya", "Soda", "Qabartma tozu", "Kakao", "Şokolad",
    "Çay", "Qəhvə", "Şəkər tozu", "Ayran", "Brınza",
    "Motal pendiri", "Suluguni", "Krem pendir", "Qatılaşdırılmış süd",
)

KNOWN = {ingredient_key(name) for name in VOCABULARY}

NEGATIVE = re.compile(
    r"\b(yoxdur|yoxdu|yox|bitib|qalmayıb|qalmayib)\b",
    re.IGNORECASE,
)

NAME_PATTERN = re.compile(
    r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
    re.UNICODE,
)

# Azərbaycan hərfləri olmayan klaviaturada yazılış: «kələm» → «kelem».
PLAIN = str.maketrans("əıöüğşç", "eiougsc")
VOWELS = set("aeiou")
KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


def plain(value):
    return fold(value).translate(PLAIN)


def _unique_index(pairs):
    """plain forma → düzgün forma. İki fərqli düzgün forması olan açar qeyri-müəyyəndir (None)."""
    index = {}
    for key, value in pairs:
        index[key] = value if index.get(key, value) == value else None
    return index


NAMES_BY_PLAIN = _unique_index((plain(name), normalize_name(name)) for name in VOCABULARY)
WORDS_BY_PLAIN = _unique_index(
    (plain(word), word)
    for name in VOCABULARY
    for word in fold(name).split()
)


def _one_edit_apart(a, b):
    """İki söz arasında ən çox bir hərf əlavəsi, silinməsi və ya dəyişməsi var."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            edits += 1
            if edits > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
            continue
        i += 1
        j += 1
    return edits + (len(b) - j) <= 1


def correct_spelling(name):
    """Tanınan ərzağın düzgün yazılışını tapır. Tapmasa, adın içindəki tanınan sözləri düzəldir.

    Qeyri-müəyyən halda (bir neçə mümkün düzgün forma) heç nəyi dəyişmir.
    """
    key = plain(name)
    exact = NAMES_BY_PLAIN.get(key)
    if exact:
        return exact

    if len(key) >= 5:
        close = {
            value for candidate, value in NAMES_BY_PLAIN.items()
            if value and len(candidate) >= 5 and _one_edit_apart(candidate, key)
        }
        if len(close) == 1:
            return close.pop()

    parts = re.split(r"([ -])", fold(name))
    fixed = "".join(WORDS_BY_PLAIN.get(plain(part)) or part for part in parts)
    return normalize_name(fixed) or name


def looks_like_word(name):
    """Klaviaturadan təsadüfi yığılmış mətni (məs. «hjhfbskaj») real addan ayırır."""
    for word in re.split(r"[ -]", plain(name)):
        if not word:
            continue
        if not VOWELS & set(word) or len(word) > 20:
            return False
        if re.search(r"(.)\1\1", word) or re.search(r"[^aeiou]{5,}", word):
            return False
        if len(word) >= 6 and sum(ch in VOWELS for ch in word) / len(word) < 0.25:
            return False
        # «asdasd», «abab» kimi təkrar; «asdf», «qwer» kimi klaviatura sırası.
        repeated = re.fullmatch(r"(.{2,})\1+", word)
        if repeated and (len(repeated[1]) == 2 or any(repeated[1] in row for row in KEYBOARD_ROWS)):
            return False
        if any(word[i:i + 4] in row for row in KEYBOARD_ROWS for i in range(len(word) - 3)):
            return False
    return True


def capitalize(value):
    return ("İ" if value[:1] in ("i", "İ") else value[:1].upper()) + value[1:].lower()


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

        raw_name = capitalize(name)
        name = normalize_name(raw_name)
        if name is None:
            invalid.append(original)
            continue
        name = correct_spelling(name)
        normalized = ingredient_key(name)

        # Tanınmayan və real sözə oxşamayan mətn əlavə olunmur, təsdiq də soruşulmur.
        if normalized not in KNOWN and not looks_like_word(name):
            invalid.append(original)
            continue

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

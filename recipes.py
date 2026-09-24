"""Nə bişirim? — reseptlər. Səbət və foto modullarına toxunmur."""

import asyncio
import logging
import re
import secrets
from urllib.parse import quote

from pydantic import BaseModel
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from ai_features import ask_gemini, api_error_message
from basket_ui import basket_view, get_rows
from favorites_store import is_favorite, save_favorite
from command_controls import clear_pending_operations
from ingredient_names import ingredient_key, normalize_name
from shopping_store import add_items
from ui_utils import edit_query, edit_markup


LOG = logging.getLogger(__name__)


PAGE_SIZE = 5
MAX_PAGES = 10
MAX_REFILL = 2

MODE_ALL, MODE_HOME, MODE_SHOP = "all", "owned", "extra"

MODE_NAMES = {
    MODE_ALL: "Hamısı",
    MODE_HOME: "Evdəkilərlə",
    MODE_SHOP: "1–2 ərzaq əlavə etsəm",
}

WATER = {
    "su",
    "adi su",
    "içməli su",
    "təmiz su",
    "isti su",
    "soyuq su",
    "qaynar su",
}

NAME_RE = re.compile(
    r"[^\W\d_]+(?:[ -][^\W\d_]+)*",
    re.UNICODE,
)

TIME_RE = re.compile(
    r"\b\d+\s*(?:[-–]\s*\d+\s*)?"
    r"(?:dəq(?:iqə)?|saniyə|san)\b"
)


# ============================================================
# AI MODELLƏRİ
# ============================================================

class ShortRecipe(BaseModel):
    name: str
    minutes: int
    method: str
    ingredients: list[str]


class ShortBatch(BaseModel):
    recipes: list[ShortRecipe]


class Ingredient(BaseModel):
    name: str
    quantity: str


class Step(BaseModel):
    instruction: str
    uses: list[str]


class FullRecipe(BaseModel):
    name: str
    method: str
    servings: int
    prep_minutes: int
    cook_minutes: int
    finish_minutes: int
    ingredients: list[Ingredient]
    steps: list[Step]
    important_note: str


# ============================================================
# KÖMƏKÇİ FUNKSİYALAR
# ============================================================

def norm(value):
    return " ".join(
        value.replace("İ", "i").casefold().split()
    )


def key(value):
    return ingredient_key(value)

def valid(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 50
        and bool(NAME_RE.fullmatch(value))
    )


def btn(text, callback):
    return InlineKeyboardButton(
        text,
        callback_data=callback,
    )


def video_url(name):
    # Konkret yoxlanmış video deyil, YouTube axtarışıdır.
    search = name + " resepti hazırlanması "

    return (
        "https://www.youtube.com/results?search_query="
        + quote(search)
    )


def signature(item):
    return (
        norm(item["method"]),
        frozenset(
            key(x)
            for x in item["ingredients"]
        ),
    )


def oils(name):
    return "yağ" in key(name)


def core_missing(title, ingredients):
    rules = {
        "sendviç": ("çörək", "lavaş"),
        "omlet": ("yumurta",),
        "makaron": ("makaron",),
        "plov": ("düyü",),
        "piroq": ("un", "xəmir"),
    }

    names = " ".join(
        key(x)
        for x in ingredients
    )

    return any(
        word in norm(title)
        and not any(
            required in names
            for required in requirements
        )
        for word, requirements in rules.items()
    )


# ============================================================
# QISA RESEPTLƏRİN YOXLAMASI
# ============================================================

def clean_short(batch, basket, history, signatures):
    available = {
        key(x)
        for x in basket
    } | {"su"}

    names = set(history)
    sigs = set(signatures)

    output = []

    for raw in batch.recipes[:20]:
        title = " ".join(raw.name.split())
        method = " ".join(raw.method.split())

        if (
            not title
            or len(title) > 80
            or not method
            or len(method) > 45
            or not 5 <= raw.minutes <= 240
        ):
            continue

        if (
            not 2 <= len(raw.ingredients) <= 16
            or norm(title) in names
        ):
            continue

        title_words = set(
            norm(title).split()
        )

        if any(
            title_words
            and len(
                title_words & set(old.split())
            ) / len(
                title_words | set(old.split())
            ) >= 0.84
            for old in names
        ):
            continue

        items = [
            "Su"
            if key(x) == "su"
            else " ".join(x.split())
            for x in raw.ingredients
        ]

        if (
            any(not valid(x) for x in items)
            or len(set(map(key, items))) != len(items)
        ):
            continue

        if core_missing(title, items):
            continue

        missing = [
            x
            for x in items
            if key(x) not in available
        ]

        if len(missing) > 2:
            continue

        # Sadə qaynatmada lazımsız yağ təkliflərini çıxar.
        if (
            any(
                x in norm(method)
                for x in (
                    "qaynat",
                    "buxar",
                    "suda bişir",
                )
            )
            and any(
                oils(x)
                for x in missing
            )
            and not any(
                x in norm(method)
                for x in (
                    "qovur",
                    "qızart",
                    "sote",
                )
            )
        ):
            continue

        candidate = {
            "name": title,
            "method": method,
            "minutes": raw.minutes,
            "ingredients": items,
            "missing": missing,
        }

        sign = signature(candidate)

        if sign in sigs:
            continue

        names.add(norm(title))
        sigs.add(sign)

        output.append(candidate)

    return output


# ============================================================
# SƏHİFƏLƏR
# ============================================================

def pick(pool, mode):
    home = [
        x
        for x in pool
        if not x["missing"]
    ]

    shop = [
        x
        for x in pool
        if x["missing"]
    ]

    if mode == MODE_HOME:
        choices = home

    elif mode == MODE_SHOP:
        choices = shop

    else:
        choices = home[:3] + shop[:2]

        choices += [
            x
            for x in pool
            if x not in choices
        ]

    selected = []
    methods = {}

    for item in choices:
        method = norm(item["method"])

        if methods.get(method, 0) < 2:
            selected.append(item)

            methods[method] = (
                methods.get(method, 0) + 1
            )

        if len(selected) == PAGE_SIZE:
            break

    for item in choices:
        if len(selected) == PAGE_SIZE:
            break

        if item not in selected:
            selected.append(item)

    remainder = [
        x
        for x in pool
        if x not in selected
    ]

    return selected, remainder


def lacking(pool, mode):
    home = sum(
        not x["missing"]
        for x in pool
    )

    shop = sum(
        bool(x["missing"])
        for x in pool
    )

    if mode == MODE_HOME:
        return home < 5

    if mode == MODE_SHOP:
        return shop < 5

    return (
        len(pool) < 5
        or home < 3
        or shop < 2
    )


def focus(basket, page, attempt):
    themes = [
        "Tərəvəz və şorba",
        "Toyuq, balıq və ət (yalnız səbətdəki növlər)",
        "Düyü, makaron və kartof",
        "Səhər yeməyi və un məmulatları",
        "Meyvə, salat və uyğun şirniyyat",
    ]

    return themes[
        (page + attempt) % len(themes)
    ]


# ============================================================
# QISA RESEPTLƏRİN GENERASİYASI
# ============================================================

async def candidates(
    basket,
    history,
    signatures,
    page,
    attempt,
    target,
):
    if target == MODE_HOME:
        goal = (
            "Hamısı səbətdəki ərzaqlarla və adi su ilə "
            "hazırlansın. Əlavə məhsul olmasın."
        )

    elif target == MODE_SHOP:
        goal = (
            "Hər reseptdə səbətdə olmayan TAM 1–2 "
            "real əlavə məhsul olsun. "
            "Yeni məhsullar yemək imkanlarını həqiqətən "
            "artırsın; həmişə yalnız yağ təklif etmə. "
            "Məsələn səbətə uyğun göbələk, qatıq, "
            "qaymaq, limon və s. müxtəlif istiqamətləri "
            "nəzərdən keçir, amma yalnız yeməyə "
            "həqiqətən lazımdırsa əlavə et."
        )

    else:
        goal = (
            "Təxminən yarısı evdəki ərzaqlarla, "
            "qalanları 1–2 əlavə ərzaqla olsun."
        )

    prompt = (
        "Sən «Nə bişirim?» üçün Azərbaycan dilində "
        "yemək ideyaları verirsən.\n"

        "SƏBƏT: "
        + ", ".join(basket)
        + "\n"

        "TƏKRARLAMA: "
        + (
            ", ".join(sorted(history)[-60:])
            or "Yoxdur"
        )
        + "\n"

        "İSTİQAMƏT: "
        + focus(basket, page, attempt)
        + "\n"

        "ŞƏRT: "
        + goal
        + "\n"

        "14 müxtəlif REAL yemək namizədi təklif et; "
        "uydurma yeməklərlə say artırma. "

        "Fərqli yemək növü və hazırlama üsulu seç. "
        "Ərzaqların tam adlarını yaz. "

        "Adi içməli Su həmişə var, lakin istifadə "
        "olunursa ingredients siyahısında Su yaz. "

        "Duz, yağ, şəkər, ədviyyat və digər ərzaqları "
        "səbətdə yoxdursa mövcud sayma. "

        "Su xaric hər reseptdə maksimum 2 əlavə "
        "ərzaq ola bilər. "

        "Yağ məcburi deyilsə suda/buxarda yeməyə "
        "əlavə etmə. "

        "Yağ lazımdırsa konkret növü seç "
        "(Bitki yağı, Kərə yağı və s.), "
        "əsassız əvəz etmə. "

        "Bütün ərzaqlar ingredients daxilində olsun; "
        "əsası olmayan sendviç, plov, piroq təklif etmə. "

        "Səbətdə olan adları eynilə yaz. "

        "name = ad, method = bişirmə üsulu, "

        "minutes = ümumi təxmini vaxt "
        "(hazırlıq, bişirmə, məcburi soyutma daxil), "

        "ingredients = yalnız ərzaq adları. "

        "Hazırlanma addımlarını İNDİ YAZMA."
    )

    batch = await ask_gemini(
        prompt,
        ShortBatch,
        temperature=0.6,
    )

    return clean_short(
        batch,
        basket,
        history,
        signatures,
    )


async def fill(
    basket,
    pool,
    history,
    signatures,
    page,
    mode,
):
    pool = list(pool)
    history = set(history)
    signatures = set(signatures)

    for attempt in range(MAX_REFILL):
        if not lacking(pool, mode):
            break

        home = sum(
            not x["missing"]
            for x in pool
        )

        shop = sum(
            bool(x["missing"])
            for x in pool
        )

        if mode != MODE_ALL:
            target = mode

        elif home < 3 and pool:
            target = MODE_HOME

        elif shop < 2 and pool:
            target = MODE_SHOP

        else:
            target = MODE_ALL

        try:
            fresh = await candidates(
                basket,
                history,
                signatures,
                page,
                attempt,
                target,
            )

        except Exception:
            if not pool:
                raise

            break

        pool.extend(fresh)

        history.update(
            norm(x["name"])
            for x in fresh
        )

        signatures.update(
            signature(x)
            for x in fresh
        )

    selected, remainder = pick(
        pool,
        mode,
    )

    return (
        selected,
        remainder,
        history,
        signatures,
    )


# ============================================================
# TƏHLÜKƏSİZLİK
# ============================================================

def meat_choice(recipe):
    return any(
        key(x) == "ət"
        for x in recipe["ingredients"]
    )


def forbidden_washing(text):
    """
    Məlum çiy ət və balıq yuma göstərişlərini bloklayır.
    Bütün təhlükələri yoxlamır.
    """

    clauses = re.split(
        r"[.!?;,\n]",
        norm(text),
    )

    for clause in clauses:
        animal = re.search(
            r"(?:toyu[qğ]\w*|hinduşka\w*|"
            r"balı[qğ]\w*|\bət\w*|"
            r"mal əti|qoyun əti)",
            clause,
        )

        if not animal:
            continue

        # «Toyuğu yumayın» qadağa göstərişidir.
        if (
            "yumayın" in clause
            or "yumamaq" in clause
            or "yumağı tapşırma" in clause
        ):
            continue

        washing = re.search(
            r"\b(yuyun|yuyub|yuyaraq|"
            r"yuyuruq|yuyulmalıdır|"
            r"yaxalayın|yaxalayıb)\b",
            clause,
        )

        if washing:
            return True

    return False


# ============================================================
# TAM RESEPTİN YOXLAMASI
# ============================================================

def validate_full(raw, short, basket, species, servings=2):
    if (
        norm(raw.name) != norm(short["name"])
        or norm(raw.method) != norm(short["method"])
    ):
        raise ValueError(
            "Yeməyin adı və ya üsulu dəyişib."
        )

    if (
        meat_choice(short)
        and species not in (
            "Mal əti",
            "Qoyun əti",
        )
    ):
        raise ValueError(
            "Ətin növü məlum deyil."
        )

    if (
        servings not in (1, 2, 4)
        or raw.servings != servings
        or not 0 <= raw.prep_minutes <= 180
        or not 0 <= raw.cook_minutes <= 240
    ):
        raise ValueError(
            "Vaxt və ya porsiya uyğun deyil."
        )

    if not 0 <= raw.finish_minutes <= 120:
        raise ValueError(
            "Soyutma/dincəltmə vaxtı uyğun deyil."
        )

    total = (
        raw.prep_minutes
        + raw.cook_minutes
        + raw.finish_minutes
    )

    if (
        not 1 <= total <= 300
        or not 2 <= len(raw.ingredients) <= 16
        or not (3 if raw.cook_minutes == 0 else 6) <= len(raw.steps) <= 10
    ):
        raise ValueError(
            "Reseptin strukturu düzgün deyil."
        )

    ingredients = []
    seen = set()

    for item in raw.ingredients:
        name = normalize_name(item.name) or ""

        quantity = " ".join(
            item.quantity.split()
        )

        if (
            not valid(name)
            or not quantity
            or len(quantity) > 45
            or key(name) in seen
        ):
            raise ValueError(
                "Ərzaq adı və ya miqdarı yanlışdır."
            )

        if key(name) == "yağ":
            raise ValueError(
                "Yağın konkret növü göstərilməlidir."
            )

        seen.add(key(name))

        ingredients.append({
            "name": name,
            "quantity": quantity,
        })

    expected = {
        key(x)
        for x in short["ingredients"]
    }

    missing_expected = expected - seen

    # Qısa siyahıdakı ümumi «Yağ» dəqiq növlə
    # əvəz edilə bilər.
    if (
        missing_expected - {"yağ"}
        or (
            "yağ" in missing_expected
            and not any(
                oils(x)
                for x in seen
            )
        )
    ):
        raise ValueError(
            "İlk siyahının əsas ərzaqları dəyişib."
        )

    available = {
        key(x)
        for x in basket
    } | {"su"}

    missing = [
        item["name"]
        for item in ingredients
        if key(item["name"]) not in available
    ]

    if len(missing) > 2:
        raise ValueError(
            "Əlavə ərzaq sayı ikidən çoxdur."
        )

    if short["missing"] and not missing:
        raise ValueError(
            "İlk siyahıdakı alış-veriş ehtiyacı dəyişib."
        )

    steps = []
    usage = set()
    timing = 0

    for number, step in enumerate(raw.steps, 1):
        instruction = " ".join(
            step.instruction.split()
        )

        if not 25 <= len(instruction) <= 300:
            raise ValueError(
                f"{number}-ci addım çox qısadır/uzundur."
            )

        uses = [
            key(x)
            for x in step.uses
        ]

        if (
            len(uses) != len(set(uses))
            or not set(uses).issubset(seen)
        ):
            raise ValueError(
                f"{number}-ci addımda naməlum ərzaq var."
            )

        usage.update(uses)

        timing += bool(
            TIME_RE.search(norm(instruction))
        )

        steps.append(instruction)

    if usage != seen:
        raise ValueError("Bəzi ərzaqlar addımlarda istifadə olunmur: " + ", ".join(sorted(seen - usage)))
    if raw.cook_minutes == 0 and (any(
        word in name for name in seen
        for word in ("toyuq", "hinduşka", "balıq", "əti", "qiymə", "yumurta")
    ) or "ət" in seen):
        raise ValueError("Bu ərzaqlar üçün bişirmə mərhələsi dəqiqləşdirilməlidir.")

    if timing == 0 and raw.cook_minutes >= 15:
        raise ValueError(
            "Bişirmə addımlarında müddət göstərilməyib."
        )

    all_text = " ".join(steps)

    if forbidden_washing(all_text):
        raise ValueError(
            "Çiy ət və ya balıq yuma göstərişi var."
        )

    if (
        re.search(
            r"\bsu(?:yu|yun|ya|dan)?\b",
            norm(all_text),
        )
        and "su" not in seen
    ):
        raise ValueError(
            "Addımda su var, siyahıda yoxdur."
        )

    if (
        re.search(
            r"\byağ\w*\b",
            norm(all_text),
        )
        and not any(
            oils(x)
            for x in seen
        )
    ):
        raise ValueError(
            "Addımda yağ var, siyahıda yoxdur."
        )

    # Sac yeməyinin hazırlanma üsulu qorunsun.
    if "sac" in norm(
        short["name"] + " " + short["method"]
    ):
        if not any(
            word in norm(all_text)
            for word in (
                "qovur",
                "qızart",
                "sac",
            )
        ):
            raise ValueError(
                "Sac üsulu dəyişib."
            )

    poultry = any(
        "toyuq" in x or "hinduşka" in x
        for x in seen
    )

    fish = any(
        "balıq" in x
        for x in seen
    )

    # Çiy toyuqdan sonra gigiyena addımı.
    if poultry:
        for i, step in enumerate(steps[:-1]):
            s = norm(step)

            cutting = re.search(
                r"(?:toyu[qğ]\w*|hinduşka\w*)"
                r".{0,65}"
                r"(?:doğra|kəs|tikələ)",
                s,
            )

            if not cutting:
                continue

            next_step = norm(
                steps[i] + " " + steps[i + 1]
            )

            hygiene = re.search(
                r"(?:əllər|əlləri|bıçaq|taxta|səth)"
                r".{0,95}"
                r"(?:yu|təmiz)",
                next_step,
            )

            if not hygiene:
                raise ValueError(
                    "Çiy toyuqdan sonra "
                    "gigiyena mərhələsi yoxdur."
                )

    note = " ".join(
        raw.important_note.split()
    )

    if len(note) > 200:
        note = ""

    optional_words = (
        "darçın",
        "qaymaq",
        "limon",
        "şəkər",
        "süd",
        "yağ",
        "bal",
        "qatıq",
    )

    if any(
        word in norm(note)
        and not any(
            word in x
            for x in seen
        )
        for word in optional_words
    ):
        note = ""

    if forbidden_washing(note):
        raise ValueError(
            "Məsləhətdə çiy ət/balıq yuma göstərişi var."
        )

    if any(
        x in norm(note)
        for x in (
            "termometr",
            "bakteriya",
            "çiy toyuq",
            "74°",
            "63°",
        )
    ):
        note = ""

    # Məcburi soyutma vaxtı cəmdə göstərilsin.
    if (
        raw.finish_minutes == 0
        and re.search(
            r"\b\d+\s*(?:dəq|dəqiqə)\b"
            r".{0,35}(?:soy\w*|dincəl\w*)",
            norm(all_text),
        )
    ):
        raise ValueError(
            "Məcburi soyutma vaxtı cəmə daxil deyil."
        )

    detail = {
        "name": short["name"],
        "method": short["method"],
        "prep": raw.prep_minutes,
        "cook": raw.cook_minutes,
        "finish": raw.finish_minutes,
        "total": total,
        "ingredients": ingredients,
        "missing": missing,
        "steps": steps,
        "note": note,
        "poultry": poultry,
        "fish": fish,
        "species": species,
        "servings": servings,
    }

    if len(detail_text(detail)) > 3900:
        raise ValueError(
            "Resept Telegram mesajına sığmır."
        )

    return detail


# ============================================================
# TAM RESEPTİN GENERASİYASI
# ============================================================

async def make_full(short, basket, species=None, servings=2):
    if (
        meat_choice(short)
        and species not in (
            "Mal əti",
            "Qoyun əti",
        )
    ):
        raise ValueError(
            "Ətin növünü seç."
        )

    if species:
        kind = (
            f"İstifadəçi «Ət» üçün {species} seçib, "
            'ingredients adında «Ət» saxla. '
        )
    else:
        kind = (
            "Ətin növünü özündən uydurma. "
        )

    prompt = (
        "Azərbaycan dilində yeni başlayan üçün "
        "seçilmiş yeməyin tam reseptini hazırla.\n"

        "SƏBƏT: "
        + ", ".join(basket)
        + "\n"

        "YEMƏK: "
        + short["name"]
        + "\n"

        "ÜSUL: "
        + short["method"]
        + "\n"

        "İLK ƏRZAQLAR: "
        + ", ".join(short["ingredients"])
        + "\n"

        "İLK ÜMUMİ VAXT: "
        + str(short["minutes"])
        + " dəqiqə.\n"

        + kind +

        "name və method sahələrini eynilə saxla. "
        "Yeməyi başqa üsula çevirmə. "

        f"{servings} nəfərlik real təxmini miqdarlar; "
        "isti yeməklərdə 6–10, bişirilməyən salatlarda 3–6 konkret addım yaz. "
        "Bişirilməyən yemək üçün cook_minutes=0 ola bilər. "
        "Porsiya sayına uyğun həm miqdarları, həm addımlardakı sayları uyğunlaşdır. "
        "Temperaturu və müddəti porsiya sayına vurma. "
        "Hər ərzaq ən azı bir addımın uses siyahısında istifadə olunsun. "

        "Miqdarın səbətdə kifayət etdiyini iddia etmə. "
        "İlk ərzaqların HAMISI qalsın. "

        "Qısa siyahıda ümumi «Yağ» varsa onu uyğun "
        "DƏQİQ növlə dəyiş: Bitki yağı, Kərə yağı və s. "

        "Yağ növü heç vaxt sadəcə «Yağ» kimi qalmasın. "

        "Yalnız zəruridirsə əlavə yağ istifadə et; "
        "su ilə qaynatmada özbaşına yağ əlavə etmə. "

        "Su avtomatik mövcuddur, istifadə olunursa "
        "Su adıyla miqdarı göstər. "

        "Su xaric ən çox 2 əlavə məhsul, "
        "yağ və duz da səbətdə yoxdursa sayılır. "

        "Addımlarda işlənən BÜTÜN məhsulları "
        "ingredients daxilinə yaz. "

        "uses siyahısındakı adlar ingredients "
        "ilə tam eyni olsun. "

        "prep_minutes hazırlıq, cook_minutes aktiv "
        "bişirmə, finish_minutes MƏCBURİ soyutma/"
        "dincəltmə müddətidir (yoxdursa 0). "

        "Ümumi vaxt bunların cəmidir. "

        "Tövsiyə etdiyin məcburi soyutma/dincəltmə "
        "vaxtını finish_minutes daxilində göstər, "
        "istəyə bağlı gecikməni məsləhətə əlavə etmə. "

        "Çiy toyuğu/əti/balığı YUMA. "

        "Əvvəl tərəvəzi hazırla, sonra çiy əti "
        "ayrıca doğra, dərhal əlləri və "
        "avadanlığı təmizlə. "

        "Balıq növünü uydurma. "

        "Toyuq (qiymə daxil) daxili 74°C, "
        "balıq 63°C, qida termometri ilə yoxlanılır. "

        "Yalnız rəngə, şəffaf suya və dəqiqə "
        "sayına güvənmə. "

        "Ət üçün də növünə və kəsimə uyğun "
        "termometr yoxlamasını yaz. "

        "Hər addımda lazım olduqda dəqiqə, "
        "temperatur, oddərəcəsi və məntiqli "
        "ardıcıllıq ver. "

        "Kotlet üçün porsiyaya uyğun kiçik "
        "kotlet sayı seç, qiyməyə toxunduqdan "
        "sonra əlləri yu. "

        "important_note yalnız mövcud ərzaqlarla "
        "dad/tekstura barədə qısa məsləhətdir; "
        "yeni ərzaq, əlavə vaxt və təhlükəsizlik "
        "mətni yazma. JSON cavabı qaytar."
    )

    problem = None

    for _ in range(2):
        if problem is None:
            correction = ""
        else:
            correction = (
                "\nƏvvəlki cavab xətalı idi: "
                + str(problem)
                + ". Sıfırdan düzəlt."
            )

        result = await ask_gemini(
            prompt + correction,
            FullRecipe,
            temperature=0.2,
        )

        try:
            return validate_full(
                result,
                short,
                basket,
                species,
                servings,
            )

        except ValueError as error:
            problem = error

    raise problem


# ============================================================
# ƏVVƏLKİ SADƏ SİYAHI DİZAYNI
# ============================================================

def visible_recipes(view):
    limit = view.get("time_limit", 0)
    return [(i, recipe) for i, recipe in enumerate(view["pages"][view["page"]])
            if not limit or recipe["minutes"] <= limit]


def summary_text(view):
    recipes = view["pages"][view["page"]]

    lines = [
        "🍽️ Nə bişirim?",
        "",
        "Seçim: " + MODE_NAMES[view["mode"]],
        f"👥 {view.get('servings', 2)} nəfərlik · ⏱️ " + (f"{view['time_limit']} dəq-dək" if view.get('time_limit') else "Vaxt limiti yoxdur"),
        (
            f"Səhifə {view['page'] + 1}/"
            f"{len(view['pages'])} · "
            f"{len(visible_recipes(view))} təklif"
        ),
        "",
    ]

    groups = (
        ("✅ Evdəki ərzaqlarla", False),
        ("🛒 1–2 ərzaq əlavə etsən", True),
    )

    for header, has_missing in groups:
        group = [
            (i, recipe)
            for i, recipe in visible_recipes(view)
            if bool(recipe["missing"]) == has_missing
        ]

        if not group:
            continue

        lines.append(header)

        for i, recipe in group:
            lines.append(
                f"{i + 1}. {recipe['name']} "
                f"— təx. {recipe['minutes']} dəq"
            )

            lines.append(
                f"   Üsul: {recipe['method']}"
            )

            if recipe["missing"]:
                lines.append(
                    "   Alınacaq: "
                    + ", ".join(recipe["missing"])
                )

        lines.append("")

    if not visible_recipes(view):
        lines.append("Bu səhifədə vaxt limitinə uyğun təklif yoxdur. Limiti dəyiş və ya başqa reseptlərə bax.")
    lines.append(
        "ℹ️ Vaxt filtri mövcud təkliflərə tətbiq olunur. Səbətdə miqdar yoxdur. "
        "Reseptdə yazılan miqdarları evdə yoxla."
    )

    return "\n".join(lines)


def summary_keyboard(view):
    """
    Bir yemək = bir tam enli düymə.
    YouTube yalnız tam reseptin içində görünür.
    """

    recipes = view["pages"][view["page"]]

    rows = [
        [
            btn(
                (
                    "● "
                    if view["mode"] == MODE_ALL
                    else ""
                ) + "🍽️ Hamısı",
                "recipe:mode:all",
            ),

            btn(
                (
                    "● "
                    if view["mode"] == MODE_HOME
                    else ""
                ) + "✅ Evdəkilərlə",
                "recipe:mode:owned",
            ),
        ],

        [
            btn(
                (
                    "● "
                    if view["mode"] == MODE_SHOP
                    else ""
                ) + "🛒 1–2 ərzaq əlavə etsəm",
                "recipe:mode:extra",
            )
        ],
    ]

    rows.append([
        btn(("● " if view.get("time_limit", 0) == value else "") + label, f"recipe:time:{value}")
        for value, label in ((0, "⏱️ Hamısı"), (30, "≤30 dəq"), (60, "≤60 dəq"))
    ])
    rows.append([
        btn(("● " if view.get("servings", 2) == value else "") + f"👥 {value} nəfər", f"recipe:servings:{value}")
        for value in (1, 2, 4)
    ])
    for i, recipe in visible_recipes(view):
        rows.append([
            btn(
                f"📖 {i + 1}. {recipe['name']}"[:55],
                f"recipe:open:{i}",
            )
        ])

    navigation = []

    if view["page"] > 0:
        navigation.append(
            btn(
                "⬅️ Əvvəlki 5",
                "recipe:prev",
            )
        )

    if view["page"] < len(view["pages"]) - 1:
        navigation.append(
            btn(
                "Növbəti 5 ➡️",
                "recipe:next",
            )
        )

    if navigation:
        rows.append(navigation)

    if (
        view["page"] == len(view["pages"]) - 1
        and len(view["pages"]) < MAX_PAGES
        and not view["exhausted"]
    ):
        rows.append([
            btn(
                "🔄 Başqa reseptlər",
                "recipe:more",
            )
        ])

    rows.append([
        btn(
            "🧺 Ərzaqlarım",
            "recipe:basket",
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# TAM RESEPT EKRANI
# ============================================================

def detail_text(recipe, compact_missing=False):
    r = recipe

    time_text = (
        f"⏱️ Ümumi: {r['total']} dəq "
        f"(hazırlıq {r['prep']} "
        f"+ bişirmə {r['cook']}"
    )

    if r["finish"]:
        time_text += (
            f" + soyutma/dincəltmə {r['finish']}"
        )

    time_text += ")"

    lines = [
        "📖 " + r["name"],
        "",
        f"👥 {r.get('servings', 2)} nəfərlik · miqdarlar təxminidir",
        time_text,
        "🔥 Üsul: " + r["method"],
        "",
        "🥬 Lazım olan ərzaqlar:",
    ]

    for item in r["ingredients"]:
        suffix = (
            " 🛒"
            if item["name"] in r["missing"]
            else ""
        )

        lines.append(
            f"• {item['name']} "
            f"— {item['quantity']}{suffix}"
        )

    if r["missing"]:
        lines.extend([
            "",
            "🛒 İşarəli ərzaqlar səbətində yoxdur."
            if compact_missing
            else "🛒 Alınacaq: " + ", ".join(r["missing"]),
        ])

    lines.extend([
        "",
        "👨‍🍳 Hazırlanması:",
    ])

    for i, step in enumerate(r["steps"], 1):
        lines.append(
            f"{i}. {step}"
        )

    if r["note"]:
        lines.extend([
            "",
            "💡 " + r["note"],
        ])

    if r["poultry"] or r["fish"]:
        steps_text = norm(
            " ".join(r["steps"])
        )

        animal = (
            "quş ətini"
            if r["poultry"]
            else "balığı"
        )

        required = (
            "74°C"
            if r["poultry"]
            else "63°C"
        )

        advice = [
            f"Çiy {animal} yumayın."
        ]

        if not (
            "əllər" in steps_text
            and (
                "yuy" in steps_text
                or "təmiz" in steps_text
            )
        ):
            advice.append(
                "Çiy məhsulla təmasdan sonra "
                "əlləri və alətləri yuyun."
            )

        if norm(required) not in steps_text:
            advice.append(
                "İç temperaturu termometrlə "
                f"ən azı {required} yoxlayın."
            )

        lines.extend([
            "",
            "🛡️ " + " ".join(advice),
        ])

    elif r["species"]:
        lines.extend([
            "",
            "🛡️ Bütöv mal/qoyun əti tikələri üçün "
            "63°C və 3 dəq dincəltmə; qiymə üçün "
            "71°C daxili temperatur lazımdır. "
            "Termometrlə yoxlayın.",
        ])

    lines.extend([
        "",
        "ℹ️ Səbətdə miqdar məlum deyil. "
        "AI reseptində və videolarda olan ərzaqları, "
        "allergenləri və təhlükəsizlik qaydalarını "
        "özün yoxla.",
    ])

    return "\n".join(lines)


def detail_keyboard(recipe, save_token=None, saved=False, shopping_added=False):
    rows = []
    if save_token is not None:
        rows.append([
            btn(
                "✅ Seçilmişlərdədir" if saved else "⭐ Seçilmişlərə əlavə et",
                f"recipe:save:{save_token}",
            )
        ])
    if save_token is not None and recipe["missing"]:
        rows.append([btn(
            "✅ Alış-veriş siyahısındadır" if shopping_added else "🛒 Alınacaqları siyahıya əlavə et",
            f"recipe:shop:{save_token}",
        )])
    rows.extend([
        [
            InlineKeyboardButton(
                "▶️ YouTube-da hazırlanmasına bax",
                url=video_url(recipe["name"]),
            )
        ],

        [
            btn(
                "⬅️ Reseptlər",
                "recipe:back",
            )
        ],

        [
            btn(
                "🧺 Ərzaqlarım",
                "recipe:basket",
            )
        ],
    ])
    return InlineKeyboardMarkup(rows)


# ============================================================
# İSTİFADƏÇİ SESSİYASI
# ============================================================

def new_view(
    mode,
    recipes,
    overflow,
    history,
    signatures,
):
    return {
        "mode": mode,
        "pages": [recipes],
        "page": 0,
        "pool": overflow,
        "history": history,
        "signatures": signatures,
        "details": {},
        "exhausted": False,
        "empty_runs": 0,
        "time_limit": 0,
        "servings": 2,
    }


# ============================================================
# RESEPT AXINININ BAŞLAMASI
# ============================================================

async def recipe_start(update, context):
    query = update.callback_query

    if query:
        if (
            not query.message
            or query.message.message_id
            != context.user_data.get("basket_message_id")
        ):
            await query.answer(
                "Səbət menyusunu yenidən aç.",
                show_alert=True,
            )
            return

        await query.answer()

    source = (
        query.message
        if query
        else update.message
    )

    clear_pending_operations(context)
    user_id = update.effective_user.id

    basket_rows = tuple(
        get_rows(user_id)
    )

    if not basket_rows:
        await source.reply_text(
            "🧺 Əvvəl ərzaqlarını əlavə et."
        )
        return

    context.user_data.pop(
        "recipe_state",
        None,
    )

    status = await source.reply_text(
        "🍽️ Reseptlər hazırlanır..."
    )

    context.user_data["recipe_message_id"] = (
        status.message_id
    )

    try:
        (
            recipes,
            pool,
            history,
            signatures,
        ) = await fill(
            [
                row[1]
                for row in basket_rows
            ],
            [],
            set(),
            set(),
            0,
            MODE_ALL,
        )

    except Exception as error:
        await status.edit_text(
            "❌ Resept axtarışı alınmadı. "
            + api_error_message(error)
        )
        return

    if tuple(get_rows(user_id)) != basket_rows:
        await status.edit_text(
            "Səbət dəyişib. Yenidən «Nə bişirim?» seç."
        )
        return

    if not recipes:
        await status.edit_text(
            "Uyğun resept tapılmadı. "
            "Başqa ərzaq əlavə et."
        )
        return

    view = new_view(
        MODE_ALL,
        recipes,
        pool,
        history,
        signatures,
    )

    view.update(context.user_data.get("recipe_preferences", {}))

    context.user_data["recipe_state"] = {
        "basket": basket_rows,
        "mode": MODE_ALL,
        "views": {
            MODE_ALL: view,
        },
    }

    await status.edit_text(
        summary_text(view),
        reply_markup=summary_keyboard(view),
    )


# ============================================================
# RESEPT DÜYMƏLƏRİ
# ============================================================

async def recipe_click(update, context):
    q = update.callback_query
    user_id = q.from_user.id

    if (
        not q.message
        or q.message.message_id
        != context.user_data.get("recipe_message_id")
    ):
        await q.answer(
            "Bu menyu köhnəlib. «Nə bişirim?» seç.",
            show_alert=True,
        )
        return

    parts = q.data.split(":")

    action = (
        parts[1]
        if len(parts) > 1
        else ""
    )

    if action not in ("save", "shop"):
        await q.answer()

    # --------------------------------------------------------
    # SƏBƏT
    # --------------------------------------------------------

    if action == "basket":
        text, keyboard = basket_view(user_id)

        context.user_data["basket_message_id"] = (
            q.message.message_id
        )

        context.user_data.pop(
            "recipe_message_id",
            None,
        )

        clear_pending_operations(context, keep=("basket_message_id",))

        await q.edit_message_text(
            text,
            reply_markup=keyboard,
        )

        return

    state = context.user_data.get(
        "recipe_state"
    )

    if not state:
        if action in ("save", "shop"):
            await q.answer("Resepti yenidən aç.", show_alert=True)
        await q.edit_message_text(
            "Resept axtarışını yenidən başlat."
        )
        return

    if (
        tuple(get_rows(user_id))
        != state["basket"]
    ):
        if action in ("save", "shop"):
            await q.answer("Səbət dəyişib. Reseptləri yenidən axtar.", show_alert=True)
        context.user_data.pop(
            "recipe_state",
            None,
        )

        await q.edit_message_text(
            "🧺 Səbət dəyişib. "
            "Reseptləri yenidən axtar."
        )
        return

    view = state["views"][
        state["mode"]
    ]

    if action in ("save", "shop"):
        active = state.get("active_detail")
        if (
            len(parts) != 3
            or not active
            or parts[2] != active["token"]
            or active["mode"] != state["mode"]
            or active["page"] != view["page"]
        ):
            await q.answer(
                "Bu resept düyməsi köhnəlib. Resepti yenidən aç.", show_alert=True,
            )
            return

        full = view["details"].get(active.get("cache_key", (active["page"], active["index"])))
        if full is None:
            await q.answer("Resepti yenidən açıb yadda saxla.", show_alert=True)
            return

        if action == "shop":
            try:
                await asyncio.to_thread(add_items, user_id, full["missing"])
            except ValueError as error:
                await q.answer(str(error), show_alert=True)
                return
            await q.answer()
            await edit_markup(q, detail_keyboard(
                full, active["token"], saved=active.get("saved", False), shopping_added=True))
            active["shopping_added"] = True
            return

        if active.get("saved"):
            await q.answer("Bu resept artıq seçilmişlərindədir.")
            return

        try:
            await asyncio.to_thread(save_favorite, user_id, full)
        except Exception:
            LOG.exception("Resept secilmislere elave olunmadi.")
            await q.answer(
                "Resept yadda saxlanmadı. Bir az sonra yenidən cəhd et.",
                show_alert=True,
            )
            return

        await q.answer()
        try:
            await q.edit_message_reply_markup(
                reply_markup=detail_keyboard(full, active["token"], saved=True, shopping_added=active.get("shopping_added", False)),
            )
        except BadRequest as error:
            # Telegram dəyişib, sessiya yazılmayıbsa retry eyni düyməni göstərə bilər.
            if "message is not modified" not in str(error).lower():
                raise
        active["saved"] = True
        return

    # Köhnə düymə sonradan açılan başqa resepti saxlamamalıdır.
    state.pop("active_detail", None)

    if action in ("time", "servings"):
        allowed = (0, 30, 60) if action == "time" else (1, 2, 4)
        if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) not in allowed:
            return
        setting = "time_limit" if action == "time" else "servings"
        value = int(parts[2])
        for other_view in state["views"].values():
            other_view[setting] = value
        context.user_data.setdefault("recipe_preferences", {})[setting] = value
        state.pop("pending_meat", None)
        await edit_query(q, summary_text(view), summary_keyboard(view))
        return

    # --------------------------------------------------------
    # REJİM DƏYİŞMƏ
    # --------------------------------------------------------

    if action == "mode":
        if (
            len(parts) != 3
            or parts[2] not in MODE_NAMES
            or parts[2] == state["mode"]
        ):
            return

        mode = parts[2]

        if mode in state["views"]:
            state["mode"] = mode

            cached = state["views"][mode]

            await q.edit_message_text(
                summary_text(cached),
                reply_markup=summary_keyboard(cached),
            )
            return

        await q.edit_message_text(
            "🔍 Seçiminə uyğun reseptlər axtarılır..."
        )

        try:
            (
                recipes,
                pool,
                history,
                signatures,
            ) = await fill(
                [
                    row[1]
                    for row in state["basket"]
                ],
                [],
                set(),
                set(),
                0,
                mode,
            )

        except Exception as error:
            await q.edit_message_text(
                "❌ Axtarış alınmadı. "
                + api_error_message(error)
                + "\n\n"
                + summary_text(view),
                reply_markup=summary_keyboard(view),
            )
            return

        if (
            tuple(get_rows(user_id))
            != state["basket"]
        ):
            context.user_data.pop(
                "recipe_state",
                None,
            )

            await q.edit_message_text(
                "Səbət dəyişib. Yenidən axtar."
            )
            return

        if not recipes:
            await q.edit_message_text(
                "Bu seçimdə resept tapılmadı.\n\n"
                + summary_text(view),
                reply_markup=summary_keyboard(view),
            )
            return

        state["views"][mode] = new_view(
            mode,
            recipes,
            pool,
            history,
            signatures,
        )

        state["mode"] = mode

        chosen = state["views"][mode]
        chosen.update(context.user_data.get("recipe_preferences", {}))

        await q.edit_message_text(
            summary_text(chosen),
            reply_markup=summary_keyboard(chosen),
        )
        return

    # --------------------------------------------------------
    # SİYAHIYA QAYIT
    # --------------------------------------------------------

    if action == "back":
        state.pop(
            "pending_meat",
            None,
        )

        await q.edit_message_text(
            summary_text(view),
            reply_markup=summary_keyboard(view),
        )
        return

    # --------------------------------------------------------
    # SƏHİFƏLƏR
    # --------------------------------------------------------

    if action in ("prev", "next"):
        page = view["page"] + (
            -1 if action == "prev" else 1
        )

        if 0 <= page < len(view["pages"]):
            view["page"] = page

        await q.edit_message_text(
            summary_text(view),
            reply_markup=summary_keyboard(view),
        )
        return

    # --------------------------------------------------------
    # RESEPTİN AÇILMASI / ƏT NÖVÜ
    # --------------------------------------------------------

    if action in ("open", "meat"):
        species = None

        if action == "open":
            if (
                len(parts) != 3
                or not parts[2].isdigit()
            ):
                return

            index = int(parts[2])

            state.pop(
                "pending_meat",
                None,
            )

        else:
            pending = state.get(
                "pending_meat"
            )

            if (
                len(parts) != 3
                or parts[2] not in (
                    "beef",
                    "lamb",
                    "unknown",
                )
                or not pending
                or pending[:2] != (
                    state["mode"],
                    view["page"],
                )
            ):
                await q.edit_message_text(
                    "Ət seçimi köhnəlib.\n\n"
                    + summary_text(view),
                    reply_markup=summary_keyboard(view),
                )
                return

            if parts[2] == "unknown":
                state.pop(
                    "pending_meat",
                    None,
                )

                await q.edit_message_text(
                    "Ətin növünü «Ərzaqlarım» "
                    "bölməsində dəqiqləşdir.\n\n"
                    + summary_text(view),
                    reply_markup=summary_keyboard(view),
                )
                return

            index = pending[2]

            species = {
                "beef": "Mal əti",
                "lamb": "Qoyun əti",
            }[parts[2]]

            state.pop(
                "pending_meat",
                None,
            )

        recipes = view["pages"][
            view["page"]
        ]

        if not 0 <= index < len(recipes):
            return

        servings = view.get("servings", 2)
        cache_key = (view["page"], index) if servings == 2 else (view["page"], index, servings)

        full = view["details"].get(
            cache_key
        )

        if full is None:
            if (
                meat_choice(recipes[index])
                and not species
            ):
                state["pending_meat"] = (
                    state["mode"],
                    view["page"],
                    index,
                )

                await q.edit_message_text(
                    "🥩 Səbətdəki «Ət» hansı növdür?",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            btn(
                                "🐄 Mal əti",
                                "recipe:meat:beef",
                            )
                        ],
                        [
                            btn(
                                "🐑 Qoyun əti",
                                "recipe:meat:lamb",
                            )
                        ],
                        [
                            btn(
                                "❔ Bilmirəm",
                                "recipe:meat:unknown",
                            )
                        ],
                        [
                            btn(
                                "⬅️ Reseptlər",
                                "recipe:back",
                            )
                        ],
                    ]),
                )
                return

            await q.edit_message_text(
                "👨‍🍳 Ətraflı resept hazırlanır..."
            )

            try:
                full = await make_full(
                    recipes[index],
                    [
                        row[1]
                        for row in state["basket"]
                    ],
                    species,
                    servings,
                )

            except ValueError:
                await q.edit_message_text(
                    "⚠️ Bu AI resepti yoxlamadan keçmədi. "
                    "Başqa yemək seçə bilərsən.\n\n"
                    + summary_text(view),
                    reply_markup=summary_keyboard(view),
                )
                return

            except Exception as error:
                await q.edit_message_text(
                    "❌ Resept açıla bilmədi. "
                    + api_error_message(error)
                    + "\n\n"
                    + summary_text(view),
                    reply_markup=summary_keyboard(view),
                )
                return

            if (
                tuple(get_rows(user_id))
                != state["basket"]
            ):
                context.user_data.pop(
                    "recipe_state",
                    None,
                )

                await q.edit_message_text(
                    "Səbət dəyişib. Yenidən axtar."
                )
                return

            if (
                view["mode"] == MODE_HOME
                and full["missing"]
            ):
                await q.edit_message_text(
                    "⚠️ Əlavə ərzaq aşkarlandı. "
                    "Evdəkilərlə rejimində "
                    "bu resepti göstərmirəm.\n\n"
                    + summary_text(view),
                    reply_markup=summary_keyboard(view),
                )
                return

            # Tam reseptdə dəqiqləşən ərzaqları
            # və vaxtı qısa siyahıya da yaz.
            recipes[index]["ingredients"] = [
                item["name"]
                for item in full["ingredients"]
            ]

            recipes[index]["missing"] = (
                full["missing"]
            )

            recipes[index]["minutes"] = (
                full["total"]
            )

            # Eyni resept təkrar açılanda
            # Gemini-yə yeni sorğu göndərilmir.
            view["details"][cache_key] = full

        recipes[index]["minutes"] = full["total"]
        if view.get("time_limit") and full["total"] > view["time_limit"]:
            await edit_query(q, "Bu porsiya üçün dəqiqləşən vaxt limitdən uzundur.\n\n" + summary_text(view), summary_keyboard(view))
            return
        save_token = secrets.token_hex(8)
        try:
            saved = await asyncio.to_thread(is_favorite, user_id, full)
        except Exception:
            LOG.exception("Reseptin secilmis veziyyeti yoxlanmadi.")
            saved = False  # Saxlama yenə bazanın unikal məhdudiyyəti ilə qorunur.
        await q.edit_message_text(
            detail_text(full),
            reply_markup=detail_keyboard(full, save_token, saved=saved),
        )
        state["active_detail"] = {
            "token": save_token,
            "mode": state["mode"],
            "page": view["page"],
            "index": index,
            "saved": saved,
            "cache_key": cache_key,
        }
        return

    # --------------------------------------------------------
    # BAŞQA RESEPTLƏR
    # --------------------------------------------------------

    if (
        action != "more"
        or view["exhausted"]
        or view["page"] != len(view["pages"]) - 1
    ):
        return

    if len(view["pages"]) >= MAX_PAGES:
        return

    await q.edit_message_text(
        "🔄 Başqa reseptlər axtarılır..."
    )

    try:
        (
            recipes,
            pool,
            history,
            signatures,
        ) = await fill(
            [
                row[1]
                for row in state["basket"]
            ],
            view["pool"],
            view["history"],
            view["signatures"],
            len(view["pages"]),
            view["mode"],
        )

    except Exception as error:
        await q.edit_message_text(
            "❌ Axtarış alınmadı. "
            + api_error_message(error)
            + "\n\n"
            + summary_text(view),
            reply_markup=summary_keyboard(view),
        )
        return

    if (
        tuple(get_rows(user_id))
        != state["basket"]
    ):
        context.user_data.pop(
            "recipe_state",
            None,
        )

        await q.edit_message_text(
            "Səbət dəyişib. Yenidən axtar."
        )
        return

    if not recipes:
        view["empty_runs"] += 1

        view["exhausted"] = (
            view["empty_runs"] >= 2
        )

        await q.edit_message_text(
            "Yeni uyğun resept tapılmadı.\n\n"
            + summary_text(view),
            reply_markup=summary_keyboard(view),
        )
        return

    view["empty_runs"] = 0

    view["pages"].append(recipes)
    view["page"] += 1

    view["pool"] = pool
    view["history"] = history
    view["signatures"] = signatures

    await q.edit_message_text(
        summary_text(view),
        reply_markup=summary_keyboard(view),
    )

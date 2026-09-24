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
from ui_utils import edit_query


LOG = logging.getLogger(__name__)


PAGE_SIZE = 5
MAX_PAGES = 10
MAX_REFILL = 4

MODE_ALL, MODE_HOME, MODE_SHOP = "all", "owned", "extra"

MODE_NAMES = {
    MODE_ALL: "Bütün təkliflər",
    MODE_HOME: "Yalnız evdəkilərlə",
    MODE_SHOP: "Əlavə 1–2 ərzaqla",
}

TIME_LABELS = {0: "Hamısı", 45: "≤45 dəq", 90: "46–90 dəq"}

METHOD_LABELS = {"fry": "Qovurma", "boil": "Qaynatma", "oven": "Sobada bişirmə",
                 "steam": "Buxarda bişirmə", "stew": "Pörtlətmə", "mix": "Qarışdırma",
                 "grill": "Qrildə bişirmə"}


def method_label(value):
    return re.sub(r"\b(?:fry|boil|oven|steam|stew|mix|grill)\b",
                  lambda match: METHOD_LABELS[match.group().lower()], value, flags=re.I).replace("+", " və ")


def time_range(value):
    # Keep older session values and buttons usable after the boundary change.
    value = {30: 45, 60: 90}.get(value, value)
    return value if value in TIME_LABELS else 0


def matches_time(minutes, value):
    value = time_range(value)
    return value == 0 or (minutes <= 45 if value == 45 else 45 < minutes <= 90)


def method_key(value):
    text = norm(value)
    families = (
        ("oven", ("soba", "sobada", "firin", "fırın")),
        ("fry", ("qızart", "qovur", "sote")),
        ("boil", ("qaynat", "qaynad", "suda biş")),
        ("steam", ("buxar",)),
        ("stew", ("pörtlət", "pörtmə", "öz suyunda")),
        ("mix", ("qarışdır", "bişirmədən", "çiy")),
        ("grill", ("qril", "manqal",)),
    )
    return "+".join(code for code, words in families if any(word in text for word in words)) or text

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
        method_key(item["method"]),
        frozenset(
            key(x)
            for x in item["ingredients"]
        ),
    )


def duplicate_signature(sign, signatures):
    # Incidental seasoning changes do not make a new recipe.
    incidental = {"su", "duz", "istiot", "qara istiot"}
    method, ingredients = sign
    core = ingredients - incidental
    return any(method_key(old_method) == method and core == old_items - incidental
               for old_method, old_items in signatures)


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
        method = method_label(" ".join(raw.method.split()))

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

        if duplicate_signature(sign, sigs):
            continue

        names.add(norm(title))
        sigs.add(sign)

        output.append(candidate)

    return output


# ============================================================
# SƏHİFƏLƏR
# ============================================================

def quotas(mode):
    return {0: 5} if mode == MODE_HOME else {1: 3, 2: 2} if mode == MODE_SHOP else {0: 3, 1: 1, 2: 1}


def deficits(pool, mode):
    return {count: max(0, needed - sum(len(x["missing"]) == count for x in pool))
            for count, needed in quotas(mode).items()}


def pick(pool, mode):
    selected, methods = [], {}
    for count, needed in quotas(mode).items():
        choices = [x for x in pool if len(x["missing"]) == count]
        for _ in range(min(needed, len(choices))):
            chosen = min(choices, key=lambda x: methods.get(method_key(x["method"]), 0))
            choices.remove(chosen)
            selected.append(chosen)
            method = method_key(chosen["method"])
            methods[method] = methods.get(method, 0) + 1
    return selected, [x for x in pool if x not in selected]


def lacking(pool, mode):
    return any(deficits(pool, mode).values())


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
    time_limit=0,
    servings=2,
    missing_target=None,
):
    if target == MODE_HOME:
        goal = (
            "Hamısı səbətdəki ərzaqlarla və adi su ilə "
            "hazırlansın. Əlavə məhsul olmasın."
        )

    elif target == MODE_SHOP:
        if missing_target == 1:
            goal = (
                "Hər reseptdə səbətdə olmayan dəqiq 1 "
                "real əlavə məhsul olsun. "
            )
        elif missing_target == 2:
            goal = (
                "Hər reseptdə səbətdə olmayan dəqiq 2 "
                "real əlavə məhsul olsun. Hər iki məhsul "
                "reseptə həqiqətən lazım olmalıdır; sırf "
                "sayı tamamlamaq üçün məhsul əlavə etmə. "
            )
        else:
            goal = (
                "Hər reseptdə səbətdə olmayan TAM 1–2 "
                "real əlavə məhsul olsun. "
            )

        goal += (
            "Yeni məhsullar yemək imkanlarını həqiqətən "
            "artırsın; həmişə yalnız yağ təklif etmə. "
            "Məsələn səbətə uyğun göbələk, qatıq, "
            "qaymaq, limon və s. müxtəlif istiqamətləri "
            "nəzərdən keçir, amma yalnız yeməyə "
            "həqiqətən lazımdırsa əlavə et."
        )

    else:
        goal = (
            "Təxminən 3 resept yalnız evdəki ərzaqlarla, "
            "1 resept 1 əlavə ərzaqla, 1 resept isə "
            "2 əlavə ərzaqla olsun. Bu bölgü prioritetdir, "
            "uyğun resept yoxdursa məcburi deyil."
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
        + ". Bu yalnız əlavə ilhamdır, siyahını bu yemək növü ilə məhdudlaşdırma.\n"

        "ŞƏRT: "
        + goal
        + "\n"

        + f"PORSİYA: {servings} nəfər.\n"
        + ("VAXT: hazırlıq, bişirmə və məcburi gözləmə daxil 45 dəqiqədən çox olmasın.\n"
           if time_range(time_limit) == 45 else
           "VAXT: hazırlıq, bişirmə və məcburi gözləmə daxil 46–90 dəqiqə olsun.\n"
           if time_range(time_limit) == 90 else "VAXT: məhdudiyyət yoxdur; qısa və uzun yeməkləri qarışıq seç.\n")
        + "ƏVVƏLKİ ÜSUL VƏ ƏRZAQ BİRLƏŞMƏLƏRİ: "
        + "; ".join(method_label(method) + ": " + ", ".join(sorted(items))
                    for method, items in sorted(signatures, key=lambda s: (s[0], sorted(s[1])))[:60])
        + "\n"

        + "Ən çox 14 müxtəlif REAL yemək namizədi təklif et; "
        "uydurma yeməklərlə say artırma. "

        "Namizədləri balanslı seç: təxminən 2 sadə və etibarlı, "
        "2 orta zənginlikdə, 1 daha yaradıcı variant olsun. "
        "Mümkün olduqda şorba və ya sulu yemək, əsas qazan yeməyi, "
        "soba və ya tava yeməyi, salat/lavaş və fərqli bir yemək "
        "ailəsi arasında müxtəliflik yarat. Bu kateqoriyalar sərt "
        "kvota deyil; səbət uyğun deyilsə məcbur etmə. "

        "Sadəcə qaynadılmış və qızardılmış tək ərzaq variantları ilə siyahını doldurma. "
        "Uyğun olduqda əsas yemək, şorba, soba yeməyi, içlikli yemək, salat və "
        "xəmir yeməkləri arasından fərqli real variantlar seç. Bunlar məcburi kvota deyil. "
        "Namizədləri seçməzdən əvvəl səbətlə hazırlana bilən fərqli yemək ailələrini nəzərdən keçir; "
        "hamısı omlet, püre, qaynatma və qovurma olmasın. "
        "Azərbaycan və digər mətbəxlərin səbətə uyğun tanınan yeməklərini nəzərdən keçir; "
        "əsas ərzağı çatmayan klassik yeməyin adını istifadə etmə. "
        "Eyni yeməyi adını, doğrama formasını, duzunu və ya bir ədviyyatını dəyişərək təkrarlama. "
        "Qısa vaxt maraqsız, uzun vaxt isə mütləq mürəkkəb demək deyil. "
        "Vaxt aralığına düşmək üçün müddəti süni uzatma və ya qısaltma. "
        "Uyğun müxtəlif yemək azdırsa, daha az namizəd qaytar. "

        "Fərqli yemək növü və hazırlama üsulu seç. "
        "Ərzaqların tam adlarını yaz. "

        "Uyğun olduqda səbətdəki 4-8 əsas ərzağı məqsədli şəkildə "
        "bir reseptdə birləşdir. Sadəcə iki məhsulu birləşdirib "
        "resept yaratma; amma uyğun olmayan məhsulları da zorla "
        "eyni yeməyə əlavə etmə. "

        "Eyni əsas məhsullardan istifadə edən reseptlərdə belə "
        "hazırlama üsulunu və yemək ailəsini dəyiş. "
        "Əvvəlki reseptlərə oxşarlığı azalt, amma bütün namizədləri "
        "sərt şəkildə fərqli etməyə çalışıb keyfiyyəti aşağı salma. "

        "Adi içməli Su həmişə var, lakin istifadə "
        "olunursa ingredients siyahısında Su yaz. "

        "Duz, yağ, şəkər, ədviyyat və digər ərzaqları "
        "səbətdə yoxdursa mövcud sayma. "

        "Çatışmayan məhsul yalnız yeməyin hazırlanması üçün "
        "həqiqətən zəruri olan əsas ingredient olsun. Könüllü "
        "yağlama, bəzək, servis, dadlandırma və ya əvəz edilə "
        "bilən məhsulu çatışmayan kimi yazma. Çatışmayan hər "
        "məhsul reseptin ingredient siyahısında real komponent "
        "kimi istifadə olunmalıdır. "

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
        "method Azərbaycan dilində yazılsın, texniki və ingiliscə adlar işlətmə. "

        "minutes = ümumi təxmini vaxt "
        "(hazırlıq, bişirmə, məcburi soyutma daxil), "

        "ingredients = yalnız ərzaq adları. "

        "Hazırlanma addımlarını İNDİ YAZMA."
    )

    batch = await ask_gemini(
        prompt,
        ShortBatch,
        temperature=0.7,
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
    time_limit=0,
    servings=2,
):
    deferred = [x for x in pool if not matches_time(x["minutes"], time_limit)]
    pool = [x for x in pool if matches_time(x["minutes"], time_limit)]
    history = set(history)
    signatures = set(signatures)

    attempts = {0: 0, 1: 0, 2: 0}
    deadline = asyncio.get_running_loop().time() + 120
    for attempt in range(MAX_REFILL):
        missing = {count: n for count, n in deficits(pool, mode).items() if n}
        if not missing:
            break
        if attempt == 0 and not pool and mode == MODE_ALL:
            target, missing_target = MODE_ALL, None
        else:
            count = min(missing, key=lambda n: (attempts[n], -missing[n], n))
            attempts[count] += 1
            target = MODE_HOME if count == 0 else MODE_SHOP
            missing_target = count if count else None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break

        try:
            fresh = await asyncio.wait_for(candidates(
                basket,
                history,
                signatures,
                page,
                attempt,
                target,
                time_limit,
                servings,
                missing_target=missing_target,
            ), timeout=remaining)

        except Exception:
            if not pool:
                raise

            break

        pool.extend(x for x in fresh if matches_time(x["minutes"], time_limit))
        deferred.extend(x for x in fresh if not matches_time(x["minutes"], time_limit))

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
    remainder.extend(deferred)

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

    if {key(x) for x in short["missing"]} != {key(x) for x in missing}:
        raise ValueError(
            "Çatışmayan ərzaqlar ilkin siyahı ilə eyni qalmalıdır."
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

        + ("Bu yemək yalnız evdəki ərzaqlarla seçilib: su xaric səbətdə olmayan heç bir məhsul əlavə etmə.\n"
           if not short["missing"] else "Çatışmayan məhsullar yalnız bunlar olsun, adları və sayı dəyişməsin: " + ", ".join(short["missing"]) + ".\n")

        + kind +

        "name və method sahələrini eynilə saxla. "
        "Yeməyi başqa üsula çevirmə və seçilmiş yeməyi sadə qaynatma variantına endirmə. "
        "Yeməyin fərqləndirici teksturasını və hazırlanma mərhələlərini qoru. "
        "Təlimat aydın olsun, addım sayını doldurmaq üçün mənasız mərhələ əlavə etmə. "

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
    limit = time_range(view.get("time_limit", 0))
    return [(i, recipe) for i, recipe in enumerate(view["pages"][view["page"]])
            if matches_time(recipe["minutes"], limit)]


def display_recipes(view):
    """One presentation order, retaining stable cache/callback indices."""
    return sorted(visible_recipes(view), key=lambda pair: len(pair[1]["missing"]))


def summary_text(view):
    lines = [
        "🍽️ Nə bişirim?",
        "",
        "Seçim: " + MODE_NAMES[view["mode"]],
        f"👥 {view.get('servings', 2)} nəfərlik · ⏱️ " + TIME_LABELS[time_range(view.get("time_limit", 0))],
        (
            f"Səhifə {view['page'] + 1}/"
            f"{len(view['pages'])} · "
            f"{len(visible_recipes(view))} təklif"
        ),
        "",
    ]

    groups = (
        ("✅ Yalnız evdəkilərlə", 0),
        ("➕ Əlavə 1 ərzaqla", 1),
        ("➕ Əlavə 2 ərzaqla", 2),
    )
    display_number = 0

    for header, has_missing in groups:
        group = [
            (i, recipe)
            for i, recipe in display_recipes(view)
            if len(recipe["missing"]) == has_missing
        ]

        if not group:
            continue

        lines.append(header)

        for i, recipe in group:
            display_number += 1
            lines.append(
                f"{display_number}. {recipe['name']} "
                f"— təx. {recipe['minutes']} dəq"
            )

            lines.append(
                f"   Üsul: {method_label(recipe['method'])}"
            )

            if recipe["missing"]:
                lines.append(
                    "   Çatışmayan: "
                    + ", ".join(recipe["missing"])
                )

        lines.append("")

    if not visible_recipes(view):
        lines.append("Bu səhifədə seçilmiş vaxt aralığına uyğun təklif yoxdur. Digər səhifələrə bax və ya bu vaxta uyğun reseptlər axtar."
                     if time_range(view.get("time_limit", 0)) else
                     "Bu seçimdə uyğun təklif tapılmadı. Başqa təkliflər axtar və ya ərzaq seçimini dəyiş.")
    shortfalls = deficits([recipe for _, recipe in display_recipes(view)], view["mode"])
    if any(shortfalls.values()):
        labels = {0: "evdəkilərlə", 1: "1 əlavə ərzaqla", 2: "2 əlavə ərzaqla"}
        lines.append("Bölgünü tamamlamaq üçün çatmır: " + "; ".join(
            f"{n} resept {labels[count]}" for count, n in shortfalls.items() if n) +
            ". Bu axtarışda tapılmadı; başqa təkliflər axtara və ya filtri dəyişə bilərsən.")
    lines.append(
        "ℹ️ Vaxt hazırlıq, bişirmə və gözləmə daxil təxminidir. Səbətdə miqdar yoxdur. "
        "Reseptdə yazılan miqdarları evdə yoxla."
    )

    return "\n".join(lines)


def summary_keyboard(view):
    """
    Bir yemək = bir tam enli düymə.
    YouTube yalnız tam reseptin içində görünür.
    """

    rows = [
        [
            btn(
                (
                    "● "
                    if view["mode"] == MODE_ALL
                    else ""
                ) + "🍽️ Bütün təkliflər",
                "recipe:mode:all",
            ),

            btn(
                (
                    "● "
                    if view["mode"] == MODE_HOME
                    else ""
                ) + "✅ Yalnız evdəkilərlə",
                "recipe:mode:owned",
            ),
        ],

        [
            btn(
                (
                    "● "
                    if view["mode"] == MODE_SHOP
                    else ""
                ) + "➕ Əlavə 1–2 ərzaqla",
                "recipe:mode:extra",
            )
        ],
    ]

    rows.append([
        btn(("● " if time_range(view.get("time_limit", 0)) == value else "") + label, f"recipe:time:{value}")
        for value, label in TIME_LABELS.items()
    ])
    rows.append([
        btn(("● " if view.get("servings", 2) == value else "") + f"👥 {value} nəfər", f"recipe:servings:{value}")
        for value in (1, 2, 4)
    ])
    for display_number, (i, recipe) in enumerate(display_recipes(view), 1):
        rows.append([btn(
            f"{display_number}. {recipe['name']}"[:55],
            f"recipe:open:{i}",
        )])

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

    if not visible_recipes(view) and time_range(view.get("time_limit", 0)) and len(view["pages"]) < MAX_PAGES:
        rows.append([btn("🔍 Bu vaxta uyğun reseptlər tap", "recipe:findtime")])

    if (
        view["page"] == len(view["pages"]) - 1
        and len(view["pages"]) < MAX_PAGES
        and not view["exhausted"]
    ):
        rows.append([
            btn(
                "🔄 Başqa təkliflər",
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
        "🔥 Üsul: " + method_label(r["method"]),
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


def detail_keyboard(recipe, save_token=None, saved=False):
    rows = []
    if save_token is not None:
        rows.append([
            btn(
                "✅ Seçilmişlərdədir" if saved else "⭐ Seçilmişlərə əlavə et",
                f"recipe:save:{save_token}",
            )
        ])
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
    # Choices apply within a search; every new search starts from these defaults.
    preferences = {"time_limit": 0, "servings": 2}
    context.user_data["recipe_preferences"] = preferences

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
            time_range(preferences.get("time_limit", 0)),
            preferences.get("servings", 2),
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

    if action == "shop":
        await q.answer("Alış-veriş bölməsi menyudan çıxarılıb. Çatışmayan ərzaqları reseptdə görə bilərsən.", show_alert=True)
        return

    if action != "save":
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
        if action == "save":
            await q.answer("Resepti yenidən aç.", show_alert=True)
        await q.edit_message_text(
            "Resept axtarışını yenidən başlat."
        )
        return

    if (
        tuple(get_rows(user_id))
        != state["basket"]
    ):
        if action == "save":
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

    if action == "save":
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
                reply_markup=detail_keyboard(full, active["token"], saved=True),
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
        allowed = (0, 30, 45, 60, 90) if action == "time" else (1, 2, 4)
        if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) not in allowed:
            return
        setting = "time_limit" if action == "time" else "servings"
        value = int(parts[2])
        if action == "time":
            value = time_range(value)
        for other_view in state["views"].values():
            other_view[setting] = value
            other_view["exhausted"] = False
            other_view["empty_runs"] = 0
            other_view.pop("search_note", None)
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
                time_range(view.get("time_limit", 0)),
                view.get("servings", 2),
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

            if {key(x) for x in full["missing"]} != {key(x) for x in recipes[index]["missing"]}:
                await q.edit_message_text(
                    "⚠️ Tam reseptdə çatışmayan ərzaqlar ilkin seçimə uyğun gəlmədi. "
                    "Başqa təklif seç və ya yenidən cəhd et.\n\n"
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

        if {key(x) for x in full["missing"]} != {key(x) for x in recipes[index]["missing"]}:
            view["details"].pop(cache_key, None)
            await edit_query(q, "Saxlanmış reseptin ərzaq bölgüsü dəyişib. Resepti yenidən aç.\n\n" + summary_text(view), summary_keyboard(view))
            return
        recipes[index]["minutes"] = full["total"]
        if not matches_time(full["total"], view.get("time_limit", 0)):
            await edit_query(q, "Bu porsiya üçün dəqiqləşən vaxt seçilmiş aralığa uyğun deyil.\n\n" + summary_text(view), summary_keyboard(view))
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
        action not in ("more", "findtime")
        or (action == "more" and (view["exhausted"] or view["page"] != len(view["pages"]) - 1))
    ):
        return

    if action == "findtime" and (visible_recipes(view) or not time_range(view.get("time_limit", 0))):
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
            time_range(view.get("time_limit", 0)),
            view.get("servings", 2),
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

    view["pool"] = pool
    view["history"] = history
    view["signatures"] = signatures
    if not recipes:
        view["empty_runs"] += 1

        view["exhausted"] = (
            view["empty_runs"] >= 2
        )

        await q.edit_message_text(
            "Bu şərtlərlə yeni və fərqli resept tapılmadı. Vaxt və ya ərzaq seçimini dəyişə bilərsən.\n\n"
            + summary_text(view),
            reply_markup=summary_keyboard(view),
        )
        return

    view["empty_runs"] = 0

    view["pages"].append(recipes)
    view["page"] = len(view["pages"]) - 1

    await q.edit_message_text(
        summary_text(view),
        reply_markup=summary_keyboard(view),
    )

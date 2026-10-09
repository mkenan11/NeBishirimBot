"""Telegramdan asılı olmayan resept axtarışı sessiyası.

Telegram (recipes.recipe_click) və web API eyni state-i və eyni keçidləri istifadə edir.
State strukturu: {"basket": rows, "mode": str, "views": {mode: view}, ...}.

AI və baza funksiyaları `recipes` modulu üzərindən çağırılır ki, testlərdəki
patch nöqtələri (recipes.fill, recipes.make_full) dəyişməsin.
"""

import recipes as core


class BasketChanged(Exception):
    """Axtarış zamanı istifadəçinin ərzaq siyahısı dəyişib."""


class SearchFailed(Exception):
    """AI axtarışı alınmadı. `error` ilkin istisnadır, `state` saxlanmalı state-dir (varsa)."""

    def __init__(self, error, state=None):
        super().__init__(str(error))
        self.error = error
        self.state = state


class MeatChoiceRequired(Exception):
    """Reseptdə «Ət» var, növü (mal/qoyun) seçilməlidir."""


class RecipeRejected(Exception):
    """Tam resept yoxlamadan keçmədi.

    reason: "invalid" | "missing_changed" | "cached_missing_changed" | "time_mismatch"
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


MEAT_SPECIES = {"beef": "Mal əti", "lamb": "Qoyun əti"}
SERVINGS = (1, 2, 4)
TIME_VALUES = (0, 30, 45, 60, 90)
DEFAULT_PREFERENCES = {"time_limit": 0, "servings": 2}


def basket_names(rows):
    return [row[1] for row in rows]


def current_view(state):
    return state["views"][state["mode"]]


def _ensure_basket(state, rows_now):
    if tuple(rows_now()) != state["basket"]:
        raise BasketChanged()


async def start(basket_rows, rows_now):
    """Yeni axtarış: həmişə «Bütün təkliflər», Hamısı və 2 nəfər ilə başlayır."""
    preferences = dict(DEFAULT_PREFERENCES)
    try:
        recipes, pool, history, signatures = await core.fill(
            basket_names(basket_rows), [], set(), set(), 0, core.MODE_ALL,
            core.time_range(preferences["time_limit"]), preferences["servings"],
        )
    except Exception as error:
        view = core.new_view(core.MODE_ALL, [], [], set(), set())
        view.update(preferences)
        raise SearchFailed(error, {"basket": basket_rows, "mode": core.MODE_ALL,
                                   "views": {core.MODE_ALL: view}}) from error

    if tuple(rows_now()) != basket_rows:
        raise BasketChanged()

    view = core.new_view(core.MODE_ALL, recipes, pool, history, signatures)
    view.update(preferences)
    return {"basket": basket_rows, "mode": core.MODE_ALL, "views": {core.MODE_ALL: view}}


def set_preference(state, setting, raw_value):
    """Vaxt və ya nəfər seçimi bütün rejimlərə tətbiq olunur. Etibarsız dəyərdə None qaytarır."""
    allowed = TIME_VALUES if setting == "time_limit" else SERVINGS if setting == "servings" else ()
    if raw_value not in allowed:
        return None
    value = core.time_range(raw_value) if setting == "time_limit" else raw_value
    for view in state["views"].values():
        view[setting] = value
        view["exhausted"] = False
        view["empty_runs"] = 0
        view.pop("search_note", None)
    state.pop("pending_meat", None)
    return value


def preferences(state):
    view = current_view(state)
    return {"time_limit": view.get("time_limit", 0), "servings": view.get("servings", 2)}


async def switch_mode(state, mode, rows_now, prefs=None):
    """Rejimi dəyişir. Keşdə yoxdursa yeni axtarış edir. Dəyişiklik olmadısa False qaytarır."""
    if mode not in core.MODE_NAMES or mode == state["mode"]:
        return False
    if mode in state["views"]:
        state["mode"] = mode
        return True

    view = current_view(state)
    try:
        recipes, pool, history, signatures = await core.fill(
            basket_names(state["basket"]), [], set(), set(), 0, mode,
            core.time_range(view.get("time_limit", 0)), view.get("servings", 2),
        )
    except Exception as error:
        raise SearchFailed(error) from error
    _ensure_basket(state, rows_now)

    chosen = core.new_view(mode, recipes, pool, history, signatures)
    chosen.update(prefs if prefs is not None else preferences(state))
    state["views"][mode] = chosen
    state["mode"] = mode
    return True


def turn_page(state, step):
    view = current_view(state)
    page = view["page"] + step
    if 0 <= page < len(view["pages"]):
        view["page"] = page
        return True
    return False


def can_search(view, action):
    """«more», «findtime» və «complete» düymələrinin icazəsi (Telegram klaviaturası ilə eyni qaydalar)."""
    if action not in ("more", "findtime", "complete"):
        return False
    if action == "more" and (view["exhausted"] or view["page"] != len(view["pages"]) - 1):
        return False
    if action == "findtime" and (core.visible_recipes(view) or not core.time_range(view.get("time_limit", 0))):
        return False
    if action != "complete" and len(view["pages"]) >= core.MAX_PAGES:
        return False
    if action == "complete" and not any(core.deficits(view["pages"][view["page"]], view["mode"]).values()):
        return False
    return True


async def search_more(state, action, rows_now):
    """Başqa təkliflər / vaxta uyğun axtarış / 5-liyi tamamlama.

    Qaytarır: None (icazə yoxdur), "empty" (yeni tam 5-lik tapılmadı) və ya "ok".
    """
    view = current_view(state)
    if not can_search(view, action):
        return None
    slots = core.deficits(view["pages"][view["page"]], view["mode"]) if action == "complete" else None

    try:
        recipes, pool, history, signatures = await core.fill(
            basket_names(state["basket"]), view["pool"], view["history"], view["signatures"],
            len(view["pages"]), view["mode"], core.time_range(view.get("time_limit", 0)),
            view.get("servings", 2), **({"slots": slots} if slots is not None else {}),
        )
    except Exception as error:
        raise SearchFailed(error) from error
    _ensure_basket(state, rows_now)

    view["pool"] = pool
    view["history"] = history
    view["signatures"] = signatures
    if not recipes:
        view["empty_runs"] += 1
        view["exhausted"] = False
        return "empty"

    view["empty_runs"] = 0
    if action == "complete":
        # Append only: existing detail-cache and callback indices stay valid.
        view["pages"][view["page"]].extend(recipes)
    else:
        view["pages"].append(recipes)
        view["page"] = len(view["pages"]) - 1
    return "ok"


def detail_cache_key(view, index):
    servings = view.get("servings", 2)
    return (view["page"], index) if servings == 2 else (view["page"], index, servings)


def cached_detail(state, index):
    view = current_view(state)
    return view["details"].get(detail_cache_key(view, index))


async def open_recipe(state, index, rows_now, species=None):
    """Cari səhifədəki resepti tam açır (lazım olsa AI ilə) və (full, cache_key) qaytarır.

    Etibarsız indeksdə None qaytarır.
    """
    view = current_view(state)
    recipes = view["pages"][view["page"]]
    if not 0 <= index < len(recipes):
        return None
    servings = view.get("servings", 2)
    cache_key = detail_cache_key(view, index)
    full = view["details"].get(cache_key)

    if full is None:
        if core.meat_choice(recipes[index]) and not species:
            state["pending_meat"] = (state["mode"], view["page"], index)
            raise MeatChoiceRequired()
        try:
            full = await core.make_full(recipes[index], basket_names(state["basket"]), species, servings)
        except ValueError as error:
            raise RecipeRejected("invalid") from error
        except Exception as error:
            raise SearchFailed(error) from error
        _ensure_basket(state, rows_now)

        if {core.key(x) for x in full["missing"]} != {core.key(x) for x in recipes[index]["missing"]}:
            raise RecipeRejected("missing_changed")

        # Tam reseptdə dəqiqləşən ərzaqları və vaxtı qısa siyahıya da yaz.
        recipes[index]["ingredients"] = [item["name"] for item in full["ingredients"]]
        recipes[index]["missing"] = full["missing"]
        recipes[index]["minutes"] = full["total"]
        # Eyni resept təkrar açılanda Gemini-yə yeni sorğu göndərilmir.
        view["details"][cache_key] = full

    if {core.key(x) for x in full["missing"]} != {core.key(x) for x in recipes[index]["missing"]}:
        view["details"].pop(cache_key, None)
        raise RecipeRejected("cached_missing_changed")
    recipes[index]["minutes"] = full["total"]
    if not core.matches_time(full["total"], view.get("time_limit", 0)):
        raise RecipeRejected("time_mismatch")
    return full, cache_key

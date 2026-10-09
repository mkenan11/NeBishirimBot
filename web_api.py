"""Web interfeysi üçün API (/web/v1). Telegram handler-ləri ilə eyni core modulları işlədir.

Bu API brauzerə açıq deyil: yalnız website-in serveri INTERNAL_WEB_API_SECRET ilə çağırır.
İstifadəçi X-Web-Session token-i ilə müəyyən olunur (anonim web istifadəçisi, mənfi user_id).
"""

import asyncio
import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import pantry_store
import recipe_session
import recipes
import web_store
from account_data import delete_account
from ai_features import MAX_PHOTO_BYTES, api_error_message, recognize_photo
from favorites_store import (
    count_favorites,
    delete_favorite,
    get_favorite,
    is_favorite,
    list_favorites,
    save_favorite,
)
from ingredient_names import ingredient_key, normalize_name
from ingredient_parser import parse_ingredients
from quick_add import POPULAR, QUICK_GROUPS, STAPLES
from session_store import load_session, save_session

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/web/v1")

# Web-dən gələn AI sorğuları üçün saatlıq limitlər (Gemini xərcini qorumaq üçün).
LIMITS = {
    "session": ("ip", 20),
    "search": ("user", 40),
    "search_ip": ("ip", 120),
    "photo": ("user", 10),
    "photo_ip": ("ip", 30),
}
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_NAMES = 30


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def api_error_handler(_request, error: ApiError):
    return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)


# ============================================================
# AUTENTİFİKASİYA
# ============================================================

def require_internal(request: Request):
    secret = os.getenv("INTERNAL_WEB_API_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Web API is not configured")
    supplied = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return request.headers.get("X-Client-IP", "")


async def current_user(request: Request, client_ip: str = Depends(require_internal)):
    user_id = await asyncio.to_thread(web_store.resolve_session, request.headers.get("X-Web-Session", ""))
    if user_id is None:
        raise ApiError(401, "session_invalid", "Sessiya tapılmadı. Səhifəni yenilə.")
    return {"id": user_id, "ip": client_ip}


async def limit(kind, user):
    scope, maximum = LIMITS[kind]
    subject = f"user:{user['id']}" if scope == "user" else f"ip:{web_store.hash_ip(user['ip'])}"
    if not await asyncio.to_thread(web_store.hit, f"{subject}:{kind}", maximum):
        raise ApiError(429, "rate_limited", "Çox sorğu göndərildi. Bir az sonra yenidən cəhd et.")


async def limit_search(user):
    await limit("search", user)
    await limit("search_ip", user)


# ============================================================
# SESSİYA
# ============================================================

@router.post("/session")
async def create_session(client_ip: str = Depends(require_internal)):
    await limit("session", {"ip": client_ip})
    token, _ = await asyncio.to_thread(web_store.create_web_user)
    return {"token": token}


@router.delete("/account")
async def delete_my_account(user=Depends(current_user)):
    await asyncio.to_thread(delete_account, user["id"])
    await asyncio.to_thread(web_store.delete_web_sessions, user["id"])
    return {"ok": True}


# ============================================================
# ƏRZAQLAR
# ============================================================

def pantry_payload(user_id):
    rows = pantry_store.list_rows(user_id)
    owned = {ingredient_key(row[1]) for row in rows}
    return {
        "items": [{"id": row[0], "name": row[1]} for row in rows],
        # Telegram-dakı «Tez əlavə et» siyahısı: istifadəçidə hələ olmayan ərzaqlar.
        "suggestions": [name for name in STAPLES if ingredient_key(name) not in owned],
        "popular": [name for name in POPULAR if ingredient_key(name) not in owned],
        "suggestion_groups": [
            {"name": group, "items": missing}
            for group, items in QUICK_GROUPS
            if (missing := [name for name in items if ingredient_key(name) not in owned])
        ],
    }


class TextIn(BaseModel):
    text: str = Field(max_length=2000)


class NamesIn(BaseModel):
    names: list[str] = Field(max_length=MAX_NAMES)


class RenameIn(BaseModel):
    name: str = Field(max_length=100)


@router.get("/pantry")
async def get_pantry(user=Depends(current_user)):
    return await asyncio.to_thread(pantry_payload, user["id"])


@router.post("/pantry/text")
async def add_from_text(body: TextIn, user=Depends(current_user)):
    """Mətndən əlavə: tanınan adlar dərhal əlavə olunur, tanınmayanlar təsdiq üçün qaytarılır."""
    parsed = parse_ingredients(body.text.strip())
    if parsed is None:
        raise ApiError(422, "too_long", "Bir dəfəyə ən çox 30 ərzaq və 1500 simvol göndər.")
    known, unknown, skipped, invalid, corrected = parsed
    added, existing = await asyncio.to_thread(pantry_store.add_ingredients, user["id"], known) if known else ([], [])
    payload = await asyncio.to_thread(pantry_payload, user["id"])
    return {**payload, "added": added, "existing": existing, "unknown": unknown,
            "skipped": skipped, "invalid": invalid, "corrected": corrected}


@router.post("/pantry")
async def add_names(body: NamesIn, user=Depends(current_user)):
    """Təsdiqlənmiş adları əlavə edir (tanınmayan adlar və şəkildən tanınanlar)."""
    names = [normalize_name(" ".join(name.split())) for name in body.names]
    if any(name is None for name in names):
        raise ApiError(422, "invalid_name", "Ərzaq adlarından biri düzgün deyil.")
    added, existing = await asyncio.to_thread(pantry_store.add_ingredients, user["id"], names)
    payload = await asyncio.to_thread(pantry_payload, user["id"])
    return {**payload, "added": added, "existing": existing}


@router.patch("/pantry/{item_id}")
async def rename_item(item_id: int, body: RenameIn, user=Depends(current_user)):
    name, problem = pantry_store.parse_single_name(body.name)
    if problem:
        raise ApiError(422, "invalid_name", "Yalnız bir ərzağın adını yaz. Məsələn: Qırmızı soğan")
    try:
        await asyncio.to_thread(pantry_store.rename_ingredient, user["id"], item_id, name)
    except pantry_store.PantryChanged:
        raise ApiError(404, "not_found", "Bu ərzaq artıq siyahında yoxdur.")
    except pantry_store.DuplicateName:
        raise ApiError(409, "duplicate", f"«{name}» artıq siyahındadır.")
    return await asyncio.to_thread(pantry_payload, user["id"])


def _remember_undo(user_id, removed):
    session = load_session(user_id)
    session["undo"] = {"removed": [tuple(row) for row in removed],
                       "after": pantry_store.snapshot(pantry_store.list_rows(user_id))}
    save_session(user_id, session)


@router.delete("/pantry/{item_id}")
async def delete_item(item_id: int, user=Depends(current_user)):
    removed = await asyncio.to_thread(pantry_store.delete_ingredients, user["id"], [item_id])
    if removed:
        await asyncio.to_thread(_remember_undo, user["id"], removed)
    return {**await asyncio.to_thread(pantry_payload, user["id"]), "removed": len(removed)}


@router.delete("/pantry")
async def clear_pantry(user=Depends(current_user)):
    try:
        removed = await asyncio.to_thread(pantry_store.clear_ingredients, user["id"])
    except pantry_store.PantryChanged:
        removed = []
    if removed:
        await asyncio.to_thread(_remember_undo, user["id"], removed)
    return {**await asyncio.to_thread(pantry_payload, user["id"]), "removed": len(removed)}


def _undo(user_id):
    session = load_session(user_id)
    undo = session.pop("undo", None)
    restored = bool(undo) and pantry_store.restore_ingredients(user_id, undo["removed"], undo["after"])
    save_session(user_id, session)
    return restored


@router.post("/pantry/undo")
async def undo_delete(user=Depends(current_user)):
    if not await asyncio.to_thread(_undo, user["id"]):
        raise ApiError(409, "undo_expired", "Siyahı dəyişib. Bu silməni artıq geri qaytarmaq olmur.")
    return await asyncio.to_thread(pantry_payload, user["id"])


# ============================================================
# ŞƏKİLDƏN TANIMA
# ============================================================

@router.post("/photo")
async def recognize(request: Request, user=Depends(current_user)):
    mime = request.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if mime not in PHOTO_TYPES:
        raise ApiError(415, "bad_type", "JPG, PNG və ya WebP şəkil göndər.")
    data = await request.body()
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ApiError(413, "too_large", "Şəkil çox böyükdür. Daha kiçik foto göndər.")
    await limit("photo", user)
    await limit("photo_ip", user)
    try:
        status, names = await recognize_photo(data, mime)
    except Exception as error:
        LOG.warning("Web photo recognition failed: %s", type(error).__name__)
        raise ApiError(502, "ai_failed", "Şəkli analiz etmək mümkün olmadı. " + api_error_message(error))
    return {"status": status if names or status != "ok" else "empty", "names": names}


# ============================================================
# RESEPTLƏR
# ============================================================

def recipes_payload(state, notice=None):
    if not state:
        return {"state": None, "notice": notice}
    view = recipe_session.current_view(state)
    return {
        "notice": notice,
        "state": {
            "mode": state["mode"],
            "time_limit": recipes.time_range(view.get("time_limit", 0)),
            "servings": view.get("servings", 2),
            "page": view["page"],
            "pages": len(view["pages"]),
            "complete": bool(recipes.visible_recipes(view)),
            "can_more": recipe_session.can_search(view, "more"),
            "can_complete": recipe_session.can_search(view, "complete"),
            "can_findtime": recipe_session.can_search(view, "findtime"),
            "recipes": [
                {
                    "index": index,
                    "name": recipe["name"],
                    "minutes": recipe["minutes"],
                    "method": recipes.method_label(recipe["method"]),
                    "missing": recipe["missing"],
                }
                for index, recipe in recipes.display_recipes(view)
            ],
        },
    }


def _load_state(user_id):
    """Saxlanmış axtarışı qaytarır; ərzaq siyahısı o vaxtdan dəyişibsə onu atır."""
    session = load_session(user_id)
    state = session.get("recipe_state")
    if state and tuple(pantry_store.list_rows(user_id)) != state["basket"]:
        session.pop("recipe_state", None)
        save_session(user_id, session)
        return session, None, True
    return session, state, False


def _save_state(user_id, session, state):
    if state is None:
        session.pop("recipe_state", None)
    else:
        session["recipe_state"] = state
    save_session(user_id, session)


def _rows_now(user_id):
    return lambda: pantry_store.list_rows(user_id)


def _ai_failed(error):
    return ApiError(502, "ai_failed", "Axtarış alınmadı. " + api_error_message(error))


BASKET_CHANGED = ("basket_changed", "Ərzaq siyahın dəyişib. Reseptləri yenidən axtar.")


async def _require_state(user_id):
    session, state, stale = await asyncio.to_thread(_load_state, user_id)
    if state is None:
        raise ApiError(409, BASKET_CHANGED[0] if stale else "no_search",
                       BASKET_CHANGED[1] if stale else "Resept axtarışını yenidən başlat.")
    return session, state


@router.get("/recipes")
async def get_recipes(user=Depends(current_user)):
    _, state, stale = await asyncio.to_thread(_load_state, user["id"])
    return recipes_payload(state, BASKET_CHANGED[1] if stale else None)


@router.post("/recipes/search")
async def start_search(user=Depends(current_user)):
    rows = tuple(await asyncio.to_thread(pantry_store.list_rows, user["id"]))
    if not rows:
        raise ApiError(400, "empty_pantry", "Əvvəl ərzaqlarını əlavə et.")
    await limit_search(user)
    session = await asyncio.to_thread(load_session, user["id"])
    try:
        state = await recipe_session.start(rows, _rows_now(user["id"]))
    except recipe_session.SearchFailed as failure:
        await asyncio.to_thread(_save_state, user["id"], session, failure.state)
        raise _ai_failed(failure.error)
    except recipe_session.BasketChanged:
        raise ApiError(409, *BASKET_CHANGED)
    await asyncio.to_thread(_save_state, user["id"], session, state)
    return recipes_payload(state)


class ModeIn(BaseModel):
    mode: str


class PreferenceIn(BaseModel):
    setting: str
    value: int


class PageIn(BaseModel):
    step: int


class MoreIn(BaseModel):
    action: str


@router.post("/recipes/mode")
async def change_mode(body: ModeIn, user=Depends(current_user)):
    session, state = await _require_state(user["id"])
    if body.mode not in recipes.MODE_NAMES:
        raise ApiError(422, "bad_mode", "Belə seçim yoxdur.")
    if body.mode not in state["views"]:
        await limit_search(user)
    try:
        await recipe_session.switch_mode(state, body.mode, _rows_now(user["id"]))
    except recipe_session.SearchFailed as failure:
        raise _ai_failed(failure.error)
    except recipe_session.BasketChanged:
        await asyncio.to_thread(_save_state, user["id"], session, None)
        raise ApiError(409, *BASKET_CHANGED)
    await asyncio.to_thread(_save_state, user["id"], session, state)
    return recipes_payload(state)


@router.post("/recipes/preference")
async def change_preference(body: PreferenceIn, user=Depends(current_user)):
    session, state = await _require_state(user["id"])
    setting = {"time": "time_limit", "time_limit": "time_limit", "servings": "servings"}.get(body.setting)
    if setting is None or recipe_session.set_preference(state, setting, body.value) is None:
        raise ApiError(422, "bad_preference", "Belə seçim yoxdur.")
    await asyncio.to_thread(_save_state, user["id"], session, state)
    return recipes_payload(state)


@router.post("/recipes/page")
async def change_page(body: PageIn, user=Depends(current_user)):
    session, state = await _require_state(user["id"])
    recipe_session.turn_page(state, 1 if body.step > 0 else -1)
    await asyncio.to_thread(_save_state, user["id"], session, state)
    return recipes_payload(state)


@router.post("/recipes/more")
async def more_recipes(body: MoreIn, user=Depends(current_user)):
    session, state = await _require_state(user["id"])
    if not recipe_session.can_search(recipe_session.current_view(state), body.action):
        return recipes_payload(state)
    await limit_search(user)
    try:
        result = await recipe_session.search_more(state, body.action, _rows_now(user["id"]))
    except recipe_session.SearchFailed as failure:
        raise _ai_failed(failure.error)
    except recipe_session.BasketChanged:
        await asyncio.to_thread(_save_state, user["id"], session, None)
        raise ApiError(409, *BASKET_CHANGED)
    await asyncio.to_thread(_save_state, user["id"], session, state)
    notice = ("Bu cəhddə şərtlərə uyğun 5 yeni resept tamamlanmadı. Əvvəlki siyahı saxlanılıb; "
              "yenidən cəhd edə bilərsən.") if result == "empty" else None
    return recipes_payload(state, notice)


class OpenIn(BaseModel):
    mode: str
    page: int
    index: int
    species: str | None = None


REJECTED = {
    "invalid": "Bu AI resepti yoxlamadan keçmədi. Başqa yemək seçə bilərsən.",
    "missing_changed": "Tam reseptdə çatışmayan ərzaqlar ilkin seçimə uyğun gəlmədi. Başqa təklif seç və ya yenidən cəhd et.",
    "cached_missing_changed": "Saxlanmış reseptin ərzaq bölgüsü dəyişib. Resepti yenidən aç.",
    "time_mismatch": "Bu porsiya üçün dəqiqləşən vaxt seçilmiş aralığa uyğun deyil.",
}


def _select(state, body):
    """URL-dəki rejim və səhifəni state-ə tətbiq edir. Uyğun gəlmirsə ApiError."""
    view = state["views"].get(body.mode)
    if view is None or not 0 <= body.page < len(view["pages"]) or not 0 <= body.index < len(view["pages"][body.page]):
        raise ApiError(409, "stale_recipe", "Bu resept artıq siyahıda yoxdur. Siyahıya qayıt.")
    state["mode"] = body.mode
    view["page"] = body.page
    return view


def detail_payload(full, saved):
    return {
        "recipe": {
            "name": full["name"],
            "method": recipes.method_label(full["method"]),
            "servings": full.get("servings", 2),
            "prep": full["prep"],
            "cook": full["cook"],
            "finish": full["finish"],
            "total": full["total"],
            "ingredients": full["ingredients"],
            "missing": full["missing"],
            "steps": full["steps"],
            "note": full["note"],
            "video_url": recipes.video_url(full["name"]),
        },
        "saved": saved,
    }


@router.post("/recipes/open")
async def open_recipe(body: OpenIn, user=Depends(current_user)):
    session, state = await _require_state(user["id"])
    _select(state, body)
    species = recipe_session.MEAT_SPECIES.get(body.species or "")
    if recipe_session.cached_detail(state, body.index) is None:
        await limit_search(user)
    try:
        full, _ = await recipe_session.open_recipe(state, body.index, _rows_now(user["id"]), species)
    except recipe_session.MeatChoiceRequired:
        state.pop("pending_meat", None)
        raise ApiError(409, "meat_choice", "Siyahındakı «Ət» hansı növdür?")
    except recipe_session.RecipeRejected as rejected:
        await asyncio.to_thread(_save_state, user["id"], session, state)
        raise ApiError(422, "recipe_rejected", REJECTED[rejected.reason])
    except recipe_session.SearchFailed as failure:
        raise ApiError(502, "ai_failed", "Resept açıla bilmədi. " + api_error_message(failure.error))
    except recipe_session.BasketChanged:
        await asyncio.to_thread(_save_state, user["id"], session, None)
        raise ApiError(409, *BASKET_CHANGED)
    await asyncio.to_thread(_save_state, user["id"], session, state)
    try:
        saved = await asyncio.to_thread(is_favorite, user["id"], full)
    except Exception:
        LOG.exception("Web: reseptin secilmis veziyyeti yoxlanmadi.")
        saved = False
    return detail_payload(full, saved)


class SaveIn(BaseModel):
    mode: str
    page: int
    index: int


@router.post("/recipes/save")
async def save_recipe(body: SaveIn, user=Depends(current_user)):
    _, state = await _require_state(user["id"])
    _select(state, body)
    full = recipe_session.cached_detail(state, body.index)
    if full is None:
        raise ApiError(409, "not_opened", "Resepti yenidən açıb yadda saxla.")
    try:
        await asyncio.to_thread(save_favorite, user["id"], full)
    except Exception:
        LOG.exception("Web: resept secilmislere elave olunmadi.")
        raise ApiError(500, "save_failed", "Resept yadda saxlanmadı. Bir az sonra yenidən cəhd et.")
    return {"saved": True}


# ============================================================
# SEÇİLMİŞ RESEPTLƏR
# ============================================================

@router.get("/favorites")
async def get_favorites(offset: int = 0, user=Depends(current_user)):
    items = await asyncio.to_thread(list_favorites, user["id"], 20, max(0, offset))
    total = await asyncio.to_thread(count_favorites, user["id"])
    return {
        "total": total,
        "items": [
            {"id": item["id"], "name": item["name"], "total": item["total"],
             "servings": item["servings"], "method": recipes.method_label(item["method"] or "")}
            for item in items
        ],
    }


@router.get("/favorites/{recipe_id}")
async def get_favorite_detail(recipe_id: int, user=Depends(current_user)):
    full = await asyncio.to_thread(get_favorite, user["id"], recipe_id)
    if full is None:
        raise ApiError(404, "not_found", "Bu resept seçilmişlərində yoxdur.")
    return detail_payload(full, True)


@router.delete("/favorites/{recipe_id}")
async def remove_favorite(recipe_id: int, user=Depends(current_user)):
    if not await asyncio.to_thread(delete_favorite, user["id"], recipe_id):
        raise ApiError(404, "not_found", "Bu resept seçilmişlərində yoxdur.")
    return {"ok": True}

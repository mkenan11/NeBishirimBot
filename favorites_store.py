"""NeBishirim - secilmis reseptlerin Neon-da saxlanmasi."""

import hashlib
import json
import os

import psycopg


MAX_RECIPE_BYTES = 32 * 1024


def _database_url():
    """Movcud Neon baglantisini istifade edir."""

    url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError("DATABASE_URL tapilmadi.")

    return url


def _normalize(value):
    """Reseptlerin tekrarlanmasini yoxlamaq ucun metni normallasdirir."""

    if not isinstance(value, str):
        raise ValueError("Metn duzgun deyil.")

    return " ".join(
        value.replace("İ", "i").casefold().split()
    )


def recipe_fingerprint(recipe):
    """
    Eyni reseptin tekrar saxlanmasinin qarsisini alir.

    Ad, hazirlanma usulu ve erzaqlarin adlari esas goturulur.
    """

    if not isinstance(recipe, dict):
        raise ValueError("Resept dict olmalidir.")

    name = _normalize(recipe["name"])
    method = _normalize(recipe["method"])

    ingredients = recipe["ingredients"]

    if not isinstance(ingredients, list) or not ingredients:
        raise ValueError("Reseptin erzaq siyahisi bosdur.")

    ingredient_names = sorted({
        _normalize(item["name"])
        for item in ingredients
    })

    if not name or not method or not all(ingredient_names):
        raise ValueError("Resept melumatlari natamamdir.")

    identity = {
        "name": name,
        "method": method,
        "ingredients": ingredient_names,
    }

    content = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def _recipe_payload(recipe):
    """Tam resepti JSON formatina cevirir."""

    if not isinstance(recipe, dict):
        raise ValueError("Resept dict olmalidir.")

    required = {
        "name",
        "method",
        "prep",
        "cook",
        "finish",
        "total",
        "ingredients",
        "missing",
        "steps",
        "note",
        "poultry",
        "fish",
        "species",
    }

    if not required.issubset(recipe):
        raise ValueError("Tam resept melumatlari natamamdir.")

    payload = json.dumps(
        recipe,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )

    if len(payload.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise ValueError("Resept olcu limitini kecdi.")

    return payload


def save_favorite(user_id, recipe):
    """
    Tam resepti Neon-da saxlayir.

    Qaytarir:
        (recipe_id, created)

    created True olduqda yeni resept elave olunub.
    False olduqda resept evvelden saxlanilib.
    """

    fingerprint = recipe_fingerprint(recipe)
    payload = _recipe_payload(recipe)

    with psycopg.connect(
        _database_url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            INSERT INTO favorite_recipes
                (user_id, fingerprint, recipe)
            VALUES
                (%s, %s, %s::jsonb)
            ON CONFLICT (user_id, fingerprint)
            DO NOTHING
            RETURNING id
            """,
            (
                int(user_id),
                fingerprint,
                payload,
            ),
        ).fetchone()

        if row is not None:
            return int(row[0]), True

        existing = db.execute(
            """
            SELECT id
            FROM favorite_recipes
            WHERE user_id = %s
              AND fingerprint = %s
            """,
            (
                int(user_id),
                fingerprint,
            ),
        ).fetchone()

        if existing is None:
            raise RuntimeError(
                "Reseptin saxlanma neticesi tesdiqlenmedi."
            )

        return int(existing[0]), False


def is_favorite(user_id, recipe):
    """Reseptin cari istifadəçinin seçilmişlərində olub-olmadığını yoxlayır."""
    fingerprint = recipe_fingerprint(recipe)
    with psycopg.connect(
        _database_url(), connect_timeout=10, prepare_threshold=None,
    ) as db:
        row = db.execute(
            "SELECT 1 FROM favorite_recipes WHERE user_id = %s AND fingerprint = %s",
            (int(user_id), fingerprint),
        ).fetchone()
    return row is not None


def list_favorites(user_id, limit=50, offset=0):
    """
    Istifadecinin secilmis reseptlerini getirir.

    Burada yalniz siyahi ucun lazim olan melumatlar qaytarilir.
    """

    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))

    with psycopg.connect(
        _database_url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        rows = db.execute(
            """
            SELECT
                id,
                recipe->>'name' AS name,
                recipe->>'method' AS method,
                (recipe->>'total')::integer AS total,
                created_at
            FROM favorite_recipes
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (
                int(user_id),
                limit,
                offset,
            ),
        ).fetchall()

    return [
        {
            "id": int(row[0]),
            "name": row[1],
            "method": row[2],
            "total": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


def count_favorites(user_id):
    """Istifadecinin secilmis resept sayini qaytarir."""

    with psycopg.connect(
        _database_url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            SELECT COUNT(*)
            FROM favorite_recipes
            WHERE user_id = %s
            """,
            (int(user_id),),
        ).fetchone()

    return int(row[0])


def get_favorite(user_id, recipe_id):
    """
    Saxlanmis tam resepti getirir.

    Resept yalniz oz sahibine qaytarilir.
    """

    with psycopg.connect(
        _database_url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            SELECT recipe
            FROM favorite_recipes
            WHERE id = %s
              AND user_id = %s
            """,
            (
                int(recipe_id),
                int(user_id),
            ),
        ).fetchone()

    if row is None:
        return None

    return row[0]


def delete_favorite(user_id, recipe_id):
    """
    Istifadecinin secilmis reseptini silir.

    Basqa istifadecinin reseptine toxunmur.
    """

    with psycopg.connect(
        _database_url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            DELETE FROM favorite_recipes
            WHERE id = %s
              AND user_id = %s
            RETURNING id
            """,
            (
                int(recipe_id),
                int(user_id),
            ),
        ).fetchone()

    return row is not None

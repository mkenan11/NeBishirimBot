"""Persistent shopping list. Purchased flags never change the pantry."""

import os
import psycopg
from ingredient_names import normalize_name, ingredient_key

MAX_ITEMS = 100


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, prepare_threshold=None)


def add_items(user_id, names):
    names = list(dict.fromkeys(normalize_name(name) for name in names))
    if any(name is None for name in names):
        raise ValueError("Ərzaq adı düzgün deyil.")
    added = 0
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("shopping:" + str(user_id),))
        current = {row[0] for row in db.execute(
            "SELECT normalized_name FROM shopping_items WHERE user_id = %s", (user_id,),
        ).fetchall()}
        if len(current | {ingredient_key(name) for name in names}) > MAX_ITEMS:
            raise ValueError("Alış-veriş siyahısında ən çox 100 ərzaq ola bilər. Alınanları təmizlə.")
        for name in names:
            # Existing purchased entries stay purchased on repeated callbacks.
            result = db.execute("""INSERT INTO shopping_items (user_id, name, normalized_name)
                VALUES (%s, %s, %s) ON CONFLICT (user_id, normalized_name) DO NOTHING""",
                (user_id, name, ingredient_key(name)))
            added += result.rowcount
    return added


def list_items(user_id):
    with connect() as db:
        return db.execute("SELECT id, name, bought FROM shopping_items WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()


def set_bought(user_id, item_id, bought):
    with connect() as db:
        return db.execute("UPDATE shopping_items SET bought = %s WHERE id = %s AND user_id = %s",
                          (bool(bought), item_id, user_id)).rowcount == 1


def remove_bought(user_id):
    with connect() as db:
        return db.execute("DELETE FROM shopping_items WHERE user_id = %s AND bought = TRUE", (user_id,)).rowcount

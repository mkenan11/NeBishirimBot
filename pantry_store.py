"""Ərzaq siyahısı üzərində yazma əməliyyatları: Telegram və web eyni funksiyaları işlədir."""

import re

import database
from ingredient_names import ingredient_key, normalize_name

SINGLE_NAME = re.compile(r"[^\W\d_]+(?:[ -][^\W\d_]+)*", re.UNICODE)
NOT_A_NAME = re.compile(r"\b(evdə|evde|var|yoxdur|yoxdu|bitib|qalmayıb)\b", re.I)


class PantryChanged(Exception):
    """Siyahı istifadəçinin gördüyü vəziyyətdən fərqlidir."""


class DuplicateName(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


def list_rows(user_id):
    with database.connect() as db:
        return db.execute(
            """SELECT id, name, normalized_name FROM ingredients
               WHERE user_id = ? ORDER BY id""", (user_id,)
        ).fetchall()


def snapshot(rows):
    return tuple(tuple(row) for row in rows)


def add_ingredients(user_id, names):
    """One add path for text, photos and quick selection, including legacy spellings."""
    added, existing = [], []
    with database.connect() as db:
        db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        owned = {ingredient_key(row[0]) for row in db.execute(
            "SELECT name FROM ingredients WHERE user_id = ?", (user_id,),
        ).fetchall()}
        for value in names:
            name = normalize_name(value)
            if name is None:
                raise ValueError("Ərzaq adı düzgün deyil.")
            key = ingredient_key(name)
            if key in owned:
                existing.append(name)
                continue
            row = db.execute(
                "INSERT OR IGNORE INTO ingredients (user_id, name, normalized_name) VALUES (?, ?, ?)",
                (user_id, name, name.casefold()),
            )
            (added if row.rowcount else existing).append(name)
            owned.add(key)
    return added, existing


def parse_single_name(text):
    """Bir ərzaq adını yoxlayır. Qaytarır: (ad, None) və ya (None, "format" | "name")."""
    name = " ".join(text.split())
    if (
        not name or len(name) > 50
        or "," in name or ";" in name or "\n" in text
        or re.search(r"\d", name)
        or NOT_A_NAME.search(name)
        or not SINGLE_NAME.fullmatch(name)
    ):
        return None, "format"
    name = normalize_name(name)
    if name is None:
        return None, "name"
    return name, None


def rename_ingredient(user_id, item_id, new_name, expected=None):
    """Adı dəyişir və adın həqiqətən dəyişib-dəyişmədiyini qaytarır.

    expected: (köhnə ad, normalized) verilsə və sətir ondan fərqlidirsə PantryChanged.
    """
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            """SELECT name, normalized_name FROM ingredients
               WHERE id = ? AND user_id = ?""", (item_id, user_id)
        ).fetchone()
        if current is None or (expected is not None and tuple(current) != tuple(expected)):
            db.rollback()
            raise PantryChanged()
        duplicate = any(
            other_id != item_id and ingredient_key(name) == ingredient_key(new_name)
            for other_id, name in db.execute(
                "SELECT id, name FROM ingredients WHERE user_id = ?", (user_id,),
            ).fetchall()
        )
        if duplicate:
            db.rollback()
            raise DuplicateName(new_name)
        db.execute(
            """UPDATE ingredients SET name = ?, normalized_name = ?
               WHERE id = ? AND user_id = ?""",
            (new_name, new_name.casefold(), item_id, user_id),
        )
    return new_name != current[0]


def delete_ingredients(user_id, item_ids, expected_snapshot=None):
    """Seçilmiş ərzaqları silir və silinən sətirləri qaytarır (geri qaytarmaq üçün)."""
    item_ids = set(item_ids)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            """SELECT id, name, normalized_name FROM ingredients
               WHERE user_id = ? ORDER BY id""", (user_id,)
        ).fetchall()
        if expected_snapshot is not None and snapshot(current) != expected_snapshot:
            db.rollback()
            raise PantryChanged()
        removed = [row for row in current if row[0] in item_ids]
        for row in removed:
            db.execute(
                "DELETE FROM ingredients WHERE user_id = ? AND id = ?",
                (user_id, row[0]),
            )
    return removed


def clear_ingredients(user_id, expected_snapshot=None):
    """Bütün siyahını silir. Siyahı boşdursa və ya dəyişibsə PantryChanged."""
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            """SELECT id, name, normalized_name FROM ingredients
               WHERE user_id = ? ORDER BY id""", (user_id,)
        ).fetchall()
        if not current or (expected_snapshot is not None and snapshot(current) != expected_snapshot):
            db.rollback()
            raise PantryChanged()
        db.execute("DELETE FROM ingredients WHERE user_id = ?", (user_id,))
    return current


def restore_ingredients(user_id, removed, expected_after):
    """Silinmiş sətirləri eyni ID-lərlə qaytarır. Siyahı o vaxtdan dəyişibsə False."""
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            """SELECT id, name, normalized_name FROM ingredients
               WHERE user_id = ? ORDER BY id""", (user_id,)
        ).fetchall()
        if snapshot(current) != expected_after:
            db.rollback()
            return False
        for item_id, name, normalized in removed:
            db.execute(
                """INSERT INTO ingredients (id, user_id, name, normalized_name)
                   VALUES (?, ?, ?, ?)""", (item_id, user_id, name, normalized)
            )
    return True

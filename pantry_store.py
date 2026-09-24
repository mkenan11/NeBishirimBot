"""One add path for text, photos and quick selection, including legacy spellings."""

import database
from ingredient_names import ingredient_key, normalize_name


def add_ingredients(user_id, names):
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

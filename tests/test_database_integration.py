"""Opt-in PostgreSQL checks; all writes stay in a fresh, rolled-back schema."""

import json
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
import account_data
import delivery_store
import favorites_store
import pantry_store
import session_store
import shopping_store
from migrate import migrate


class RollbackOnly(Exception):
    pass


class BorrowedConnection:
    """Give each store call a savepoint instead of a new external connection."""
    def __init__(self, db):
        self.db = db
        self.transaction = db.transaction()
        self.transaction.__enter__()

    def __enter__(self):
        return self

    def __exit__(self, *error):
        if self.transaction:
            transaction, self.transaction = self.transaction, None
            transaction.__exit__(*error)
        return False

    def execute(self, *args, **kwargs):
        return self.db.execute(*args, **kwargs)

    def commit(self):
        self.__exit__(None, None, None)

    def rollback(self):
        self.__exit__(RollbackOnly, RollbackOnly(), None)

    def close(self):
        self.rollback()


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = psycopg.connect(os.environ["TEST_DATABASE_URL"], connect_timeout=10, prepare_threshold=None)
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)
        schema = "neb_test_" + uuid.uuid4().hex
        self.db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        migrate(self.db)
        self.connect_patch = patch.object(psycopg, "connect", side_effect=lambda *a, **k: BorrowedConnection(self.db))
        self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)
        self.env_patch = patch.dict(os.environ, {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.recipe = {
            "name": "Test recipe", "method": "Test method", "prep": 5, "cook": 15,
            "finish": 0, "total": 20, "ingredients": [{"name": "Ət", "quantity": "200 q"}],
            "missing": [], "steps": ["Test step"], "note": "", "poultry": False,
            "fish": False, "species": "Mal əti", "servings": 2,
        }

    def test_migrations_are_repeatable(self):
        migrate(self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 2)

    def test_pantry_recognizes_legacy_spelling_without_deleting_it(self):
        self.db.execute("INSERT INTO users VALUES (1)")
        self.db.execute("INSERT INTO ingredients (user_id, name, normalized_name) VALUES (1, 'Sogan', 'sogan')")
        added, existing = pantry_store.add_ingredients(1, ["Soğan", "Kartof"])
        self.assertEqual(added, ["Kartof"])
        self.assertEqual(existing, ["Soğan"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0], 2)

    def test_shopping_is_idempotent_and_separate_from_pantry(self):
        self.assertEqual(shopping_store.add_items(1, ["sogan", "Soğan", "Duz"]), 2)
        item_id = shopping_store.list_items(1)[0][0]
        self.assertFalse(shopping_store.set_bought(2, item_id, True))
        self.assertTrue(shopping_store.set_bought(1, item_id, True))
        self.assertEqual(shopping_store.add_items(1, ["Soğan"]), 0)
        self.assertTrue(shopping_store.list_items(1)[0][2])
        self.assertEqual(shopping_store.remove_bought(1), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0], 0)

    def test_legacy_favorites_and_new_variants(self):
        legacy = favorites_store.recipe_fingerprint(self.recipe, legacy=True)
        self.db.execute("INSERT INTO favorite_recipes (user_id, fingerprint, recipe) VALUES (1, %s, %s::jsonb)",
                        (legacy, json.dumps(self.recipe)))
        self.assertTrue(favorites_store.is_favorite(1, self.recipe))
        _, created = favorites_store.save_favorite(1, self.recipe)
        self.assertFalse(created)
        _, created = favorites_store.save_favorite(1, dict(self.recipe, species="Qoyun əti"))
        self.assertTrue(created)
        _, created = favorites_store.save_favorite(1, dict(self.recipe, servings=4))
        self.assertTrue(created)
        self.assertEqual(favorites_store.count_favorites(1), 3)
        self.assertEqual({x["servings"] for x in favorites_store.list_favorites(1)}, {2, 4})

    def test_delivery_claim_and_atomic_session_receipt(self):
        self.assertEqual(delivery_store.claim_update(101, 1), "claimed")
        self.assertEqual(delivery_store.claim_update(101, 1), "processing")
        session_store.save_session(1, {"selected": {1, 2}}, 101)
        self.assertEqual(delivery_store.claim_update(101, 1), "completed")
        delivery_store.finish_update(101, status="uncertain")
        self.assertEqual(delivery_store.claim_update(101, 1), "completed")
        self.assertEqual(session_store.load_session(1), {"selected": {1, 2}})

    def test_expired_delivery_is_not_replayed(self):
        delivery_store.claim_update(102, 1)
        self.db.execute("UPDATE processed_updates SET processed_at = NOW() - INTERVAL '6 minutes'")
        self.assertEqual(delivery_store.claim_update(102, 1), "uncertain")

    def test_safe_release_allows_retry_before_handler_started(self):
        delivery_store.claim_update(103, 1)
        delivery_store.release_update(103)
        self.assertEqual(delivery_store.claim_update(103, 1), "claimed")

    def test_account_deletion_is_scoped_and_keeps_anonymous_receipt(self):
        for user_id in (1, 2):
            pantry_store.add_ingredients(user_id, ["Kartof"])
            favorites_store.save_favorite(user_id, self.recipe)
            shopping_store.add_items(user_id, ["Duz"])
            session_store.save_session(user_id, {"preferences": True})
            delivery_store.claim_update(200 + user_id, user_id)
        account_data.delete_account(1, 201)
        for table in ("users", "ingredients", "favorite_recipes", "shopping_items", "bot_sessions"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = 1").fetchone()[0], 0)
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = 2").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT user_id, delivery_status FROM processed_updates WHERE update_id = 201").fetchone(), (None, "completed"))

    def test_session_prunes_old_recipe_cache_before_limit(self):
        state = {"recipe_state": {"views": {"all": {"details": {(0, 0): "x" * 600000}}}},
                 "recipe_preferences": {"servings": 4}}
        persisted = session_store.save_session(1, state)
        self.assertEqual(persisted["recipe_state"]["views"]["all"]["details"], {})
        self.assertEqual(persisted["recipe_preferences"], {"servings": 4})
        self.assertEqual(len(state["recipe_state"]["views"]["all"]["details"][(0, 0)]), 600000)

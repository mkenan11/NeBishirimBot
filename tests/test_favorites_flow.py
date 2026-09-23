"""Offline regression checks: no Telegram, Gemini or database requests."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import command_controls
import favorites_ui
import recipes
from session_store import _pack, _unpack


class FavoritesFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.full = {
            "name": "Kartof yeməyi", "method": "Qaynatma",
            "prep": 5, "cook": 20, "finish": 0, "total": 25,
            "ingredients": [{"name": "Kartof", "quantity": "2 ədəd"}],
            "missing": [], "steps": ["Kartofu hazırla."], "note": "",
            "poultry": False, "fish": False, "species": None,
        }
        self.rows = [(1, "Kartof", "kartof")]
        short = {"name": self.full["name"], "method": self.full["method"],
                 "ingredients": ["Kartof"], "missing": [], "minutes": 25}
        self.view = recipes.new_view("all", [short], [], set(), set())
        self.view["details"][(0, 0)] = copy.deepcopy(self.full)
        self.state = {"basket": tuple(self.rows), "mode": "all",
                      "views": {"all": self.view}}
        self.context = SimpleNamespace(user_data={
            "recipe_message_id": 42, "recipe_state": self.state,
        })
        self.query = SimpleNamespace(
            data="recipe:open:0", from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(message_id=42, reply_text=AsyncMock()),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        self.update = SimpleNamespace(callback_query=self.query)
        self.rows_patch = patch.object(recipes, "get_rows", return_value=self.rows)
        self.rows_patch.start()
        self.addCleanup(self.rows_patch.stop)

    async def open_recipe(self):
        with patch.object(recipes, "make_full", new_callable=AsyncMock) as ai:
            await recipes.recipe_click(self.update, self.context)
            ai.assert_not_awaited()
        markup = self.query.edit_message_text.call_args.kwargs["reply_markup"]
        callback = markup.inline_keyboard[0][0].callback_data
        self.assertLessEqual(len(callback.encode()), 64)
        self.query.data = callback
        return callback

    async def test_save_exact_cached_recipe_and_repeat(self):
        await self.open_recipe()
        with patch.object(recipes, "save_favorite", side_effect=[(7, True), (7, False)]) as save:
            await recipes.recipe_click(self.update, self.context)
            self.assertIn("əlavə edildi", self.query.message.reply_text.call_args.args[0])
            await recipes.recipe_click(self.update, self.context)
            self.assertIn("artıq", self.query.message.reply_text.call_args.args[0])
            self.assertEqual(save.call_count, 2)
            save.assert_called_with(123, self.full)

    async def test_old_token_cannot_save_reopened_recipe(self):
        old_callback = await self.open_recipe()
        self.query.data = "recipe:open:0"
        await self.open_recipe()
        self.query.data = old_callback
        with patch.object(recipes, "save_favorite") as save:
            await recipes.recipe_click(self.update, self.context)
            save.assert_not_called()

    async def test_back_invalidates_save(self):
        callback = await self.open_recipe()
        self.query.data = "recipe:back"
        await recipes.recipe_click(self.update, self.context)
        self.query.data = callback
        with patch.object(recipes, "save_favorite") as save:
            await recipes.recipe_click(self.update, self.context)
            save.assert_not_called()

    async def test_changed_basket_blocks_save(self):
        await self.open_recipe()
        with patch.object(recipes, "get_rows", return_value=[]), patch.object(recipes, "save_favorite") as save:
            await recipes.recipe_click(self.update, self.context)
            save.assert_not_called()
        self.assertNotIn("recipe_state", self.context.user_data)

    async def test_save_failure_preserves_recipe_for_retry(self):
        await self.open_recipe()
        with patch.object(recipes, "save_favorite", side_effect=RuntimeError("offline")), self.assertLogs(recipes.LOG, level="ERROR"):
            await recipes.recipe_click(self.update, self.context)
        self.assertIn("active_detail", self.state)
        self.assertIn("saxlanmadı", self.query.message.reply_text.call_args.args[0])

    async def test_save_after_session_roundtrip(self):
        await self.open_recipe()
        self.context.user_data = _unpack(_pack(self.context.user_data))
        with patch.object(recipes, "save_favorite", return_value=(7, True)) as save:
            await recipes.recipe_click(self.update, self.context)
            save.assert_called_once_with(123, self.full)

    async def test_enter_favorites_clears_pending_operations(self):
        self.context.user_data.update(rename_target={"id": 1}, photo_state={"mode": "rename"})
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=55))))
        with patch.object(favorites_ui, "favorites_view", new_callable=AsyncMock, return_value=("Favorites", None)):
            await favorites_ui.show_favorites(update, self.context)
        self.assertEqual(self.context.user_data, {"favorites_message_id": 55})
        command_controls.clear_pending_operations(self.context)
        self.assertNotIn("favorites_message_id", self.context.user_data)

    async def test_favorites_open_and_delete_require_owner(self):
        self.context.user_data["favorites_message_id"] = 42
        self.query.data = "favorite:open:7:0"
        with patch.object(favorites_ui, "get_favorite", return_value=self.full) as get:
            await favorites_ui.favorite_click(self.update, self.context)
            get.assert_called_once_with(123, 7)
        self.query.data = "favorite:deleteask:7:0"
        with patch.object(favorites_ui, "get_favorite", return_value=self.full), patch.object(favorites_ui, "delete_favorite") as delete:
            await favorites_ui.favorite_click(self.update, self.context)
            delete.assert_not_called()
        self.query.data = "favorite:deleteconfirm:7:0"
        with patch.object(favorites_ui, "delete_favorite", return_value=True) as delete, patch.object(favorites_ui, "favorites_view", new_callable=AsyncMock, return_value=("Empty", None)):
            await favorites_ui.favorite_click(self.update, self.context)
            delete.assert_called_once_with(123, 7)


if __name__ == "__main__":
    unittest.main()

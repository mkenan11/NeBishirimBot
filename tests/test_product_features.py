import copy
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import account_data
import ai_features
import bot
import command_controls
import favorites_store
import recipes
import session_bridge
import shopping_ui
import test_favorites_flow as fixtures
from ingredient_names import normalize_name, ingredient_key


def cold_recipe(servings=2):
    return recipes.FullRecipe(
        name="Xiyar salatı", method="Qarışdırma", servings=servings,
        prep_minutes=10, cook_minutes=0, finish_minutes=0,
        ingredients=[recipes.Ingredient(name="Xiyar", quantity="2 ədəd"),
                     recipes.Ingredient(name="Qatıq", quantity="100 q")],
        steps=[recipes.Step(instruction="Xiyarı təmizləyin və nazik dilimlərə doğrayın.", uses=["Xiyar"]),
               recipes.Step(instruction="Qatığı ayrıca qabda bir qaşıqla qarışdırın.", uses=["Qatıq"]),
               recipes.Step(instruction="Xiyarı qatıqla qarışdırın və süfrəyə verin.", uses=["Xiyar", "Qatıq"])],
        important_note="",
    )


class RecipeValidationTests(unittest.TestCase):
    def setUp(self):
        self.short = {"name": "Xiyar salatı", "method": "Qarışdırma",
                      "ingredients": ["Xiyar", "Qatıq"], "missing": [], "minutes": 10}

    def test_decimal_comma_and_typo(self):
        known, unknown, skipped, invalid, corrected = bot.parse_ingredients("1,5 kq kartof, sogan və yumurta")
        self.assertEqual(known, ["Kartof", "Soğan", "Yumurta"])
        self.assertEqual(unknown + skipped + invalid, [])

    def test_shared_spelling_and_distinct_varieties(self):
        self.assertEqual(normalize_name("SOGAN"), "Soğan")
        self.assertEqual(ai_features.unique_names(["sogan", "Soğan"]), ["Soğan"])
        self.assertEqual(ingredient_key("İSPANAQ"), ingredient_key("ispanaq"))
        self.assertNotEqual(ingredient_key("Kərə yağı"), ingredient_key("Bitki yağı"))
        self.assertEqual(bot.parse_ingredients("ispanaq")[0], ["İspanaq"])

    def test_cold_recipe_and_servings(self):
        full = recipes.validate_full(cold_recipe(4), self.short, ["Xiyar", "Qatıq"], None, 4)
        self.assertEqual(full["cook"], 0)
        self.assertIn("4 nəfərlik", recipes.detail_text(full))

    def test_unused_ingredients_are_rejected(self):
        raw = cold_recipe()
        for step in raw.steps:
            step.uses = ["Xiyar"]
        with self.assertRaisesRegex(ValueError, "istifadə olunmur"):
            recipes.validate_full(raw, self.short, ["Xiyar", "Qatıq"], None)

    def test_wrong_servings_rejected(self):
        with self.assertRaises(ValueError):
            recipes.validate_full(cold_recipe(2), self.short, ["Xiyar", "Qatıq"], None, 4)

    def test_species_and_servings_have_distinct_fingerprints(self):
        full = recipes.validate_full(cold_recipe(), self.short, ["Xiyar", "Qatıq"], None)
        self.assertEqual(favorites_store.recipe_fingerprint(full), favorites_store.recipe_fingerprint(full, legacy=True))
        self.assertNotEqual(favorites_store.recipe_fingerprint(dict(full, species="Mal əti")),
                            favorites_store.recipe_fingerprint(dict(full, species="Qoyun əti")))
        self.assertNotEqual(favorites_store.recipe_fingerprint(full),
                            favorites_store.recipe_fingerprint(dict(full, servings=4)))

    def test_time_filter_preserves_original_callback_indices(self):
        items = [dict(self.short, minutes=value) for value in (60, 20, 40)]
        view = recipes.new_view("all", items, [], set(), set())
        view["time_limit"] = 30
        callbacks = [b.callback_data for row in recipes.summary_keyboard(view).inline_keyboard for b in row]
        self.assertIn("recipe:open:1", callbacks)
        self.assertNotIn("recipe:open:0", callbacks)
        self.assertIn("1 təklif", recipes.summary_text(view))
        self.assertIn("recipe:servings:4", callbacks)

    def test_empty_filter_explains_next_action(self):
        view = recipes.new_view("all", [dict(self.short, minutes=90)], [], set(), set())
        view["time_limit"] = 30
        self.assertIn("vaxt limitinə uyğun təklif yoxdur", recipes.summary_text(view))

    def test_help_and_menu_expose_new_features(self):
        labels = [button.text for row in bot.MENU.keyboard for button in row]
        self.assertIn("🛒 Alış-veriş siyahısı", labels)
        text, markup = bot.help_view("privacy")
        self.assertIn("/delete_my_data", text)
        self.assertIn("help:delete", [b.callback_data for row in markup.inline_keyboard for b in row])


class RecipeInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.FavoritesFlowTests.setUp(self)

    async def test_settings_change_without_ai(self):
        self.query.data = "recipe:servings:4"
        with patch.object(recipes, "make_full", new_callable=AsyncMock) as ai:
            await recipes.recipe_click(self.update, self.context)
            ai.assert_not_awaited()
        self.assertEqual(self.view["servings"], 4)
        self.assertEqual(self.context.user_data["recipe_preferences"]["servings"], 4)

    async def test_serving_variant_uses_separate_cache_and_save(self):
        self.view["servings"] = 4
        full = dict(self.full, servings=4)
        with patch.object(recipes, "make_full", new_callable=AsyncMock, return_value=full) as ai:
            await recipes.recipe_click(self.update, self.context)
            self.assertEqual(ai.call_args.args[-1], 4)
        self.assertEqual(self.state["active_detail"]["cache_key"], (0, 0, 4))
        self.query.data = "recipe:save:" + self.state["active_detail"]["token"]
        with patch.object(recipes, "save_favorite", return_value=(7, True)) as save:
            await recipes.recipe_click(self.update, self.context)
            save.assert_called_once_with(123, full)
        self.assertNotIn("servings", self.view["details"][(0, 0)])

    async def test_shopping_add_uses_open_recipe_without_pantry_mutation(self):
        self.view["details"][(0, 0)]["missing"] = ["Duz"]
        await fixtures.FavoritesFlowTests.open_recipe(self)
        self.query.data = "recipe:shop:" + self.state["active_detail"]["token"]
        with patch.object(recipes, "add_items", return_value=1) as add:
            await recipes.recipe_click(self.update, self.context)
            add.assert_called_once_with(123, ["Duz"])
        self.assertTrue(self.state["active_detail"]["shopping_added"])
        self.query.message.reply_text.assert_not_awaited()

    async def test_generate_prompt_matches_requested_servings(self):
        raw = cold_recipe(4)
        short = {"name": raw.name, "method": raw.method,
                 "ingredients": ["Xiyar", "Qatıq"], "missing": [], "minutes": 10}
        with patch.object(recipes, "ask_gemini", new_callable=AsyncMock, return_value=raw) as ai:
            full = await recipes.make_full(short, ["Xiyar", "Qatıq"], servings=4)
            self.assertIn("4 nəfərlik", ai.call_args.args[0])
        self.assertEqual(full["servings"], 4)


class AccountAndNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = NS(message_id=5, reply_text=AsyncMock(return_value=NS(message_id=8)))
        self.query = NS(message=self.message, from_user=NS(id=123), data="account:delete:token",
                        answer=AsyncMock(), edit_message_text=AsyncMock())
        self.update = NS(callback_query=self.query, effective_user=NS(id=123), update_id=1001,
                         effective_message=self.message, effective_chat=NS(type="private"))
        self.context = NS(user_data={"delete_account_state": {
            "token": "token", "message_id": 5, "user_id": 123, "expires": time.time()+300,
        }}, application=NS(bot_data={}))

    async def test_expired_delete_does_not_touch_database(self):
        self.context.user_data["delete_account_state"]["expires"] = 0
        with patch.object(account_data, "delete_account") as delete:
            await account_data.account_click(self.update, self.context)
            delete.assert_not_called()

    async def test_foreign_user_cannot_confirm_deletion(self):
        self.query.from_user.id = 456
        with patch.object(account_data, "delete_account") as delete:
            await account_data.account_click(self.update, self.context)
            delete.assert_not_called()

    async def test_cancel_deletion_does_not_touch_database(self):
        self.query.data = "account:cancel:token"
        with patch.object(account_data, "delete_account") as delete:
            await account_data.account_click(self.update, self.context)
            delete.assert_not_called()
        self.assertNotIn("delete_account_state", self.context.user_data)

    async def test_confirm_deletion_does_not_recreate_session(self):
        with patch.object(account_data, "delete_account") as delete:
            await account_data.account_click(self.update, self.context)
            delete.assert_called_once_with(123, 1001)
        self.assertEqual(self.context.user_data, {})
        with patch.object(session_bridge, "save_session") as save:
            await session_bridge.save_state(self.update, self.context)
            save.assert_not_called()

    async def test_group_deletion_is_not_offered(self):
        self.update.effective_chat.type = "group"
        self.context.user_data.clear()
        await account_data.request_deletion(self.update, self.context)
        self.assertNotIn("delete_account_state", self.context.user_data)

    async def test_photo_entry_clears_other_screen_even_when_photo_too_large(self):
        self.context.user_data.update(rename_target={"id": 1}, favorites_message_id=3)
        self.update.message = NS(photo=[NS(file_size=ai_features.MAX_PHOTO_BYTES+1)], reply_text=AsyncMock())
        await ai_features.handle_photo(self.update, self.context)
        self.assertEqual(self.context.user_data, {})

    async def test_shopping_purchase_callback_sets_instead_of_toggles(self):
        self.context.user_data = {"shopping_message_id": 5}
        self.query.data = "shop:set:11:1:0"
        with patch.object(shopping_ui, "set_bought", return_value=True) as set_bought, patch.object(shopping_ui, "shopping_view", new_callable=AsyncMock, return_value=("List", None)):
            await shopping_ui.shopping_click(self.update, self.context)
            await shopping_ui.shopping_click(self.update, self.context)
        self.assertEqual(set_bought.call_count, 2)
        self.assertTrue(all(call.args == (123, 11, True) for call in set_bought.call_args_list))

    async def test_clearing_pending_preserves_preferences(self):
        self.context.user_data.update(recipe_preferences={"servings": 4}, photo_state={})
        command_controls.clear_pending_operations(self.context)
        self.assertEqual(self.context.user_data, {"recipe_preferences": {"servings": 4}})

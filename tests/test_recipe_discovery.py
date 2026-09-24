"""Recipe discovery regressions; no network or database calls."""

import unittest
from unittest.mock import AsyncMock, patch

import recipes
import favorites_ui
import test_favorites_flow as fixtures


def item(minutes=45, name="Sobada tərəvəz", method="Sobada bişirmə", ingredients=None):
    return dict(name=name, minutes=minutes, method=method,
                ingredients=ingredients or ["Kartof", "Soğan"], missing=[])


class DiscoveryTests(unittest.TestCase):
    def test_non_overlapping_boundaries_and_unlimited(self):
        values = (5, 30, 31, 60, 90, 91, 180)
        self.assertEqual([n for n in values if recipes.matches_time(n, 30)], [5, 30])
        self.assertEqual([n for n in values if recipes.matches_time(n, 90)], [31, 60, 90])
        self.assertTrue(all(recipes.matches_time(n, 0) for n in values))
        self.assertEqual(recipes.time_range(60), 90)

    def test_missing_salt_and_oil_are_counted(self):
        batch = recipes.ShortBatch(recipes=[recipes.ShortRecipe(
            name="Tavada kartof", method="Qızartma", minutes=25,
            ingredients=["Kartof", "Duz", "Bitki yağı"])])
        result = recipes.clean_short(batch, ["Kartof"], set(), set())
        self.assertEqual(result[0]["missing"], ["Duz", "Bitki yağı"])
        self.assertEqual(recipes.pick(result, recipes.MODE_HOME)[0], [])

    def test_renamed_method_and_salt_do_not_create_new_recipe(self):
        previous = recipes.signature(item(method="Sobada bişirmə"))
        batch = recipes.ShortBatch(recipes=[recipes.ShortRecipe(
            name="Tərəvəz ziyafəti", method="Sobada hazırlama", minutes=50,
            ingredients=["Kartof", "Soğan", "Duz"])])
        self.assertEqual(recipes.clean_short(batch, ["Kartof", "Soğan", "Duz"], set(), {previous}), [])
        batch.recipes[0].method = "Qaynatma"
        self.assertEqual(len(recipes.clean_short(batch, ["Kartof", "Soğan", "Duz"], set(), {previous})), 1)

    def test_hidden_shopping_buttons_but_missing_text_remains(self):
        full = {"name": "Yemək", "missing": ["Duz"]}
        keyboards = [recipes.detail_keyboard(full, "token"),
                     favorites_ui.favorite_detail_keyboard(1, 0, full)]
        for keyboard in keyboards:
            self.assertFalse(any(":shop:" in (b.callback_data or "")
                                 for row in keyboard.inline_keyboard for b in row))
        view = recipes.new_view("extra", [dict(item(), missing=["Duz"])], [], set(), set())
        self.assertIn("Çatışmayan: Duz", recipes.summary_text(view))


class DiscoveryInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.FavoritesFlowTests.setUp(self)

    async def test_time_filter_never_generates_and_legacy_60_maps_to_range(self):
        self.query.data = "recipe:time:60"
        with patch.object(recipes, "fill", new_callable=AsyncMock) as fill:
            await recipes.recipe_click(self.update, self.context)
            fill.assert_not_awaited()
        self.assertEqual(self.view["time_limit"], 90)
        callbacks = [b.callback_data for row in self.query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn("recipe:findtime", callbacks)
        self.assertNotIn("recipe:time:60", callbacks)

    async def test_targeted_search_from_earlier_page_appends_and_opens_new_page(self):
        self.view.update(time_limit=90, servings=4)
        self.view["pages"].append([item(20)])
        self.query.data = "recipe:findtime"
        with patch.object(recipes, "fill", new_callable=AsyncMock,
                          return_value=([item()], [], {"new"}, set())) as fill:
            await recipes.recipe_click(self.update, self.context)
            self.assertEqual(fill.call_args.args[-2:], (90, 4))
        self.assertEqual(self.view["page"], 2)
        self.assertEqual(self.view["pages"][2], [item()])
        self.assertEqual(self.view["details"][(0, 0)], self.full)

    async def test_search_failure_preserves_screen_and_history(self):
        self.view["time_limit"] = 90
        self.query.data = "recipe:findtime"
        with patch.object(recipes, "fill", new_callable=AsyncMock, side_effect=RuntimeError("offline")):
            await recipes.recipe_click(self.update, self.context)
        self.assertEqual(len(self.view["pages"]), 1)
        self.assertIn("reply_markup", self.query.edit_message_text.call_args.kwargs)

    async def test_empty_search_retains_out_of_range_pool(self):
        self.view["time_limit"] = 90
        self.query.data = "recipe:findtime"
        with patch.object(recipes, "fill", new_callable=AsyncMock,
                          return_value=([], [item(20)], {"found"}, set())):
            await recipes.recipe_click(self.update, self.context)
        self.assertEqual(self.view["pool"], [item(20)])
        self.assertEqual(self.view["history"], {"found"})

    async def test_full_recipe_outside_lower_bound_is_not_shown_as_matching(self):
        self.view["time_limit"] = 90
        await recipes.recipe_click(self.update, self.context)
        self.assertNotIn("active_detail", self.state)
        self.assertIn("aralığa uyğun deyil", self.query.edit_message_text.call_args.args[0])

    async def test_fill_bounds_calls_and_retains_other_time_ranges(self):
        short, long = item(20), item(120, name="Uzun yemək")
        with patch.object(recipes, "candidates", new_callable=AsyncMock, return_value=[]) as ai:
            selected, pool, _, _ = await recipes.fill(["Kartof"], [short, long], set(), set(), 0, "all", 90, 4)
        self.assertEqual(selected, [])
        self.assertEqual(pool, [short, long])
        self.assertEqual(ai.await_count, recipes.MAX_REFILL)
        self.assertEqual(ai.call_args.args[-2:], (90, 4))

    async def test_candidates_prompt_uses_range_servings_and_previous_combinations(self):
        with patch.object(recipes, "ask_gemini", new_callable=AsyncMock,
                          return_value=recipes.ShortBatch(recipes=[])) as ai:
            await recipes.candidates(["Kartof", "Soğan"], {"old"},
                                     {recipes.signature(item())}, 1, 0, "all", 90, 4)
        prompt = ai.call_args.args[0]
        for expected in ("31–90", "4 nəfər", "ƏVVƏLKİ ÜSUL", "daha az namizəd", "duzunu"):
            self.assertIn(expected, prompt)

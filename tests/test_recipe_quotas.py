"""Selection quotas, bounded refill, and stable presentation identities."""
import unittest
from unittest.mock import AsyncMock, patch
import recipes
import test_favorites_flow as fixtures
from test_product_features import cold_recipe


def recipe(index, missing=0, method="Qaynatma"):
    return dict(name=f"Yemək {index}", ingredients=["Kartof", "Soğan"],
                missing=["Duz", "Qatıq"][:missing], method=method, minutes=60)


class QuotaTests(unittest.TestCase):
    def test_exact_distribution_and_no_substitution(self):
        pool = [recipe(n, m) for m in (0, 1, 2) for n in range(m*10, m*10+7)]
        for mode, expected in (("all", [0,0,0,1,2]), ("owned", [0]*5), ("extra", [1,1,1,2,2])):
            chosen, rest = recipes.pick(pool, mode)
            self.assertEqual([len(r["missing"]) for r in chosen], expected)
            self.assertEqual(len(chosen)+len(rest), len(pool))
        chosen, _ = recipes.pick([r for r in pool if len(r["missing"]) != 2], "all")
        self.assertEqual([len(r["missing"]) for r in chosen], [0,0,0,1])

    def test_diversity_within_quota(self):
        pool = [recipe(0), recipe(1), recipe(2), recipe(3, method="Sobada bişirmə"),
                recipe(4, method="Qızartma"), recipe(5,1), recipe(6,2)]
        chosen, _ = recipes.pick(pool, "all")
        self.assertEqual(len({r["method"] for r in chosen[:3]}), 3)
        self.assertEqual([len(r["missing"]) for r in chosen], [0,0,0,1,2])

    def test_shared_display_order_with_filter_and_legacy_unsorted_page(self):
        page = [recipe(0,2), recipe(1), recipe(2,1), recipe(3)]
        page[1]["minutes"] = 20
        view = recipes.new_view("all", page, [], set(), set())
        view["time_limit"] = 90
        buttons = [b for row in recipes.summary_keyboard(view).inline_keyboard for b in row
                   if (b.callback_data or "").startswith("recipe:open:")]
        self.assertEqual([b.callback_data for b in buttons], ["recipe:open:3", "recipe:open:2", "recipe:open:0"])
        text = recipes.summary_text(view)
        for number, button in enumerate(buttons,1):
            self.assertTrue(button.text.startswith(f"{number}. "))
            self.assertIn(button.text, text)

    def test_method_labels_are_localized_without_losing_identity(self):
        for code in recipes.METHOD_LABELS:
            self.assertEqual(recipes.method_key(recipes.method_label(code)), code)
        view = recipes.new_view("all", [recipe(0, method="fry+boil")], [], set(), set())
        text = recipes.summary_text(view)
        self.assertNotIn("fry", text)
        self.assertIn("Qovurma və Qaynatma", text)

    def test_full_recipe_cannot_change_missing_group(self):
        raw = cold_recipe()
        short = dict(name=raw.name, method=raw.method, ingredients=["Xiyar", "Qatıq"], missing=[], minutes=10)
        with self.assertRaisesRegex(ValueError, "ilkin siyahı"):
            recipes.validate_full(raw, short, ["Xiyar"], None)


class RefillTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_batch_then_each_missing_group_is_requested(self):
        async def generate(*args, **kwargs):
            target = kwargs.get("missing_target")
            if target == 1:
                return [recipe(5,1)]
            if target == 2:
                return [recipe(6,2)]
            return [recipe(i) for i in range(3)]
        with patch.object(recipes, "candidates", side_effect=generate) as ai:
            selected, *_ = await recipes.fill([], [], set(), set(), 0, "all", 90, 2)
        self.assertEqual([len(r["missing"]) for r in selected], [0,0,0,1,2])
        self.assertEqual([c.kwargs["missing_target"] for c in ai.call_args_list], [None,1,2])

    async def test_impossible_one_group_does_not_starve_other_groups(self):
        async def generate(*args, **kwargs):
            return [recipe(6,2)] if kwargs.get("missing_target") == 2 else []
        with patch.object(recipes, "candidates", side_effect=generate) as ai:
            selected, *_ = await recipes.fill([], [], set(), set(), 0, "extra", 90, 2)
        self.assertTrue(any(c.kwargs["missing_target"] == 2 for c in ai.call_args_list))
        self.assertLessEqual(ai.call_count, recipes.MAX_REFILL)
        self.assertTrue(selected)

    async def test_timeout_preserves_existing_partial_results(self):
        with patch.object(recipes, "candidates", new_callable=AsyncMock, side_effect=TimeoutError):
            selected, *_ = await recipes.fill([], [recipe(0)], set(), set(), 0, "all", 90, 2)
        self.assertEqual(selected, [recipe(0)])


class DetailGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.FavoritesFlowTests.setUp(self)

    async def test_generated_detail_cannot_mutate_page_group(self):
        self.view["details"].clear()
        with patch.object(recipes,"make_full",new_callable=AsyncMock, return_value=dict(self.full, missing=["Duz"])):
            await recipes.recipe_click(self.update,self.context)
        self.assertEqual(self.view["pages"][0][0]["missing"], [])
        self.assertNotIn("active_detail",self.state)
        self.assertEqual(self.view["details"], {})

    async def test_incompatible_legacy_cache_is_discarded(self):
        self.view["details"][(0,0)]["missing"] = ["Duz"]
        await recipes.recipe_click(self.update,self.context)
        self.assertNotIn((0,0),self.view["details"])
        self.assertNotIn("active_detail",self.state)

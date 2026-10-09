"""Web API (/web/v1) regressions; no network or database calls."""

import copy
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import recipes
import web_api

SECRET = "x" * 40
HEADERS = {"Authorization": f"Bearer {SECRET}", "X-Web-Session": "token", "X-Client-IP": "1.2.3.4"}


def short(name, missing=()):
    return {"name": name, "minutes": 30, "method": "Qaynatma",
            "ingredients": ["Kartof"], "missing": list(missing)}


FULL = {
    "name": "Kartof şorbası", "method": "Qaynatma", "servings": 2,
    "prep": 5, "cook": 20, "finish": 0, "total": 25,
    "ingredients": [{"name": "Kartof", "quantity": "2 ədəd"}],
    "missing": [], "steps": ["Kartofu doğra."], "note": "",
    "poultry": False, "fish": False, "species": None,
}


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"INTERNAL_WEB_API_SECRET": SECRET})
        self.env.start()
        self.addCleanup(self.env.stop)

        self.rows = [(1, "Kartof", "kartof"), (2, "Soğan", "soğan")]
        self.session = {}

        def load(_user_id):
            return copy.deepcopy(self.session)

        def save(_user_id, state):
            self.session = copy.deepcopy(state)
            return state

        patches = [
            patch.object(web_api.web_store, "resolve_session", return_value=-7),
            patch.object(web_api.web_store, "hit", return_value=True),
            patch.object(web_api.pantry_store, "list_rows", side_effect=lambda _u: list(self.rows)),
            patch.object(web_api, "load_session", side_effect=load),
            patch.object(web_api, "save_session", side_effect=save),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        app = FastAPI()
        app.include_router(web_api.router)
        app.add_exception_handler(web_api.ApiError, web_api.api_error_handler)
        self.client = TestClient(app)

    def test_requires_internal_secret(self):
        self.assertEqual(self.client.get("/web/v1/pantry").status_code, 401)
        wrong = dict(HEADERS, Authorization="Bearer wrong")
        self.assertEqual(self.client.get("/web/v1/pantry", headers=wrong).status_code, 401)
        with patch.dict(os.environ, {"INTERNAL_WEB_API_SECRET": ""}):
            self.assertEqual(self.client.get("/web/v1/pantry", headers=HEADERS).status_code, 503)

    def test_unknown_session_is_rejected(self):
        with patch.object(web_api.web_store, "resolve_session", return_value=None):
            response = self.client.get("/web/v1/pantry", headers=HEADERS)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "session_invalid")

    def test_create_session_is_rate_limited_by_ip(self):
        with patch.object(web_api.web_store, "create_web_user", return_value=("abc", -1)) as create:
            response = self.client.post("/web/v1/session", headers={"Authorization": HEADERS["Authorization"]})
            self.assertEqual(response.json(), {"token": "abc"})
            with patch.object(web_api.web_store, "hit", return_value=False):
                limited = self.client.post("/web/v1/session", headers={"Authorization": HEADERS["Authorization"]})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(create.call_count, 1)

    def test_text_adds_known_and_returns_unknown_for_confirmation(self):
        with patch.object(web_api.pantry_store, "add_ingredients", return_value=(["Kartof"], [])) as add:
            response = self.client.post("/web/v1/pantry/text", headers=HEADERS,
                                        json={"text": "kartof, zəfəran tozu"})
        body = response.json()
        add.assert_called_once_with(-7, ["Kartof"])
        self.assertEqual(body["added"], ["Kartof"])
        self.assertEqual(body["unknown"], ["Zəfəran tozu"])
        self.assertEqual([item["name"] for item in body["items"]], ["Kartof", "Soğan"])

    def test_search_open_and_save_use_shared_core(self):
        page = [short("A"), short("B"), short("C"), short("D", ["Duz"]), short("E", ["Duz", "Qatıq"])]
        fill = AsyncMock(return_value=(page, [], {"a"}, set()))
        with patch.object(recipes, "fill", fill), \
             patch.object(recipes, "make_full", AsyncMock(return_value=copy.deepcopy(FULL))) as make_full, \
             patch.object(web_api, "is_favorite", return_value=False), \
             patch.object(web_api, "save_favorite") as save:
            listed = self.client.post("/web/v1/recipes/search", headers=HEADERS).json()["state"]
            self.assertTrue(listed["complete"])
            self.assertEqual([r["name"] for r in listed["recipes"]], ["A", "B", "C", "D", "E"])
            self.assertEqual(listed["recipes"][4]["missing"], ["Duz", "Qatıq"])

            target = {"mode": "all", "page": 0, "index": 0}
            detail = self.client.post("/web/v1/recipes/open", headers=HEADERS, json=target).json()
            self.assertEqual(detail["recipe"]["name"], "Kartof şorbası")
            self.assertIn("youtube.com/results", detail["recipe"]["video_url"])

            # İkinci açılış keşdən gəlir: Gemini yenidən çağırılmır.
            self.client.post("/web/v1/recipes/open", headers=HEADERS, json=target)
            self.assertEqual(make_full.await_count, 1)

            self.assertEqual(self.client.post("/web/v1/recipes/save", headers=HEADERS, json=target).json(),
                             {"saved": True})
            save.assert_called_once()

    def test_preferences_apply_without_ai_call(self):
        page = [short("A"), short("B"), short("C"), short("D", ["Duz"]), short("E", ["Duz", "Qatıq"])]
        fill = AsyncMock(return_value=(page, [], set(), set()))
        with patch.object(recipes, "fill", fill):
            self.client.post("/web/v1/recipes/search", headers=HEADERS)
            state = self.client.post("/web/v1/recipes/preference", headers=HEADERS,
                                     json={"setting": "servings", "value": 4}).json()["state"]
            bad = self.client.post("/web/v1/recipes/preference", headers=HEADERS,
                                   json={"setting": "servings", "value": 3})
        self.assertEqual(state["servings"], 4)
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(fill.await_count, 1)

    def test_changed_pantry_invalidates_saved_search(self):
        page = [short("A"), short("B"), short("C"), short("D", ["Duz"]), short("E", ["Duz", "Qatıq"])]
        with patch.object(recipes, "fill", AsyncMock(return_value=(page, [], set(), set()))):
            self.client.post("/web/v1/recipes/search", headers=HEADERS)
        self.rows.append((3, "Yumurta", "yumurta"))
        body = self.client.get("/web/v1/recipes", headers=HEADERS).json()
        self.assertIsNone(body["state"])
        self.assertIn("dəyişib", body["notice"])
        opened = self.client.post("/web/v1/recipes/open", headers=HEADERS,
                                  json={"mode": "all", "page": 0, "index": 0})
        self.assertEqual(opened.status_code, 409)

    def test_meat_recipe_asks_for_species(self):
        page = [short("Ət qovurması"), short("B"), short("C"), short("D", ["Duz"]), short("E", ["Duz", "Qatıq"])]
        page[0]["ingredients"] = ["Ət", "Soğan"]
        with patch.object(recipes, "fill", AsyncMock(return_value=(page, [], set(), set()))), \
             patch.object(recipes, "make_full", AsyncMock(return_value=copy.deepcopy(FULL))), \
             patch.object(web_api, "is_favorite", return_value=False):
            self.client.post("/web/v1/recipes/search", headers=HEADERS)
            index = next(r["index"] for r in self.client.get("/web/v1/recipes", headers=HEADERS)
                         .json()["state"]["recipes"] if r["name"] == "Ət qovurması")
            target = {"mode": "all", "page": 0, "index": index}
            asked = self.client.post("/web/v1/recipes/open", headers=HEADERS, json=target)
            chosen = self.client.post("/web/v1/recipes/open", headers=HEADERS, json=dict(target, species="beef"))
        self.assertEqual(asked.json()["code"], "meat_choice")
        self.assertEqual(chosen.status_code, 200)

    def test_ai_failure_returns_readable_message(self):
        with patch.object(recipes, "fill", AsyncMock(side_effect=TimeoutError())):
            response = self.client.post("/web/v1/recipes/search", headers=HEADERS)
        self.assertEqual(response.status_code, 502)
        self.assertIn("AI vaxtında cavab vermədi", response.json()["message"])

    def test_photo_validation_and_recognition(self):
        bad = self.client.post("/web/v1/photo", headers=dict(HEADERS, **{"Content-Type": "text/plain"}),
                               content=b"x")
        self.assertEqual(bad.status_code, 415)
        with patch.object(web_api, "recognize_photo", AsyncMock(return_value=("ok", ["Pomidor"]))) as recognize:
            ok = self.client.post("/web/v1/photo", headers=dict(HEADERS, **{"Content-Type": "image/jpeg"}),
                                  content=b"\xff\xd8data")
        self.assertEqual(ok.json(), {"status": "ok", "names": ["Pomidor"]})
        recognize.assert_awaited_once_with(b"\xff\xd8data", "image/jpeg")

    def test_delete_and_undo_are_kept_server_side(self):
        removed = [(2, "Soğan", "soğan")]
        with patch.object(web_api.pantry_store, "delete_ingredients", return_value=removed), \
             patch.object(web_api.pantry_store, "restore_ingredients", return_value=True) as restore:
            self.rows = [self.rows[0]]
            self.client.delete("/web/v1/pantry/2", headers=HEADERS)
            self.assertEqual(self.client.post("/web/v1/pantry/undo", headers=HEADERS).status_code, 200)
            restore.assert_called_once_with(-7, [(2, "Soğan", "soğan")], ((1, "Kartof", "kartof"),))
            self.assertEqual(self.client.post("/web/v1/pantry/undo", headers=HEADERS).status_code, 409)


if __name__ == "__main__":
    unittest.main()

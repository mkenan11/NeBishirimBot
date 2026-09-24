import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import app
import bot


class WorkerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.application = MagicMock()
        self.application.bot_data = {}
        self.application.__aenter__ = AsyncMock(return_value=self.application)
        self.application.__aexit__ = AsyncMock(return_value=False)
        self.application.start = AsyncMock()
        self.application.stop = AsyncMock()
        self.application.process_update = AsyncMock()
        self.request = NS(headers={"Upstash-Signature": "test"}, body=AsyncMock(return_value=json.dumps({"update_id": 99}).encode()))
        patches = [patch.object(app, "verify_qstash"), patch.object(app, "worker_url", return_value="https://test.invalid/internal/qstash"),
                   patch.object(bot, "create_application", return_value=self.application)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_duplicate_never_runs_handlers(self):
        with patch.object(app, "claim_update", return_value="completed"):
            result = await app.qstash_worker(self.request)
        self.assertTrue(result["duplicate"])
        self.application.process_update.assert_not_awaited()

    async def test_busy_delivery_returns_retryable_status(self):
        with patch.object(app, "claim_update", return_value="processing"):
            with self.assertRaises(HTTPException) as error:
                await app.qstash_worker(self.request)
        self.assertEqual(error.exception.status_code, 503)

    async def test_pre_handler_failure_releases_claim(self):
        self.application.start.side_effect = RuntimeError("offline")
        with patch.object(app, "claim_update", return_value="claimed"), patch.object(app, "release_update") as release, self.assertLogs(app.LOG, level="ERROR"):
            with self.assertRaises(HTTPException):
                await app.qstash_worker(self.request)
        release.assert_called_once_with(99)

    async def test_handler_failure_is_not_automatically_replayed(self):
        self.application.process_update.side_effect = RuntimeError("uncertain side effect")
        with patch.object(app, "claim_update", return_value="claimed"), patch.object(app, "finish_update") as finish, self.assertLogs(app.LOG, level="ERROR"):
            result = await app.qstash_worker(self.request)
        self.assertEqual(result["status"], "uncertain")
        finish.assert_called_once_with(99, None, "uncertain")

    async def test_success_finishes_claim(self):
        with patch.object(app, "claim_update", return_value="claimed"), patch.object(app, "finish_update") as finish:
            result = await app.qstash_worker(self.request)
        self.assertTrue(result["processed"])
        finish.assert_called_once_with(99, None)

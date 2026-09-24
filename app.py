"""NeBishirim — Telegram webhook ve QStash worker."""

import asyncio
import hmac
import json
import logging
import os

import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from qstash import Receiver
from telegram import Update
from delivery_store import claim_update, finish_update, release_update


load_dotenv()

LOG = logging.getLogger(__name__)

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

QSTASH_API = "https://qstash-eu-central-1.upstash.io"
QUEUE_NAME = "ne-bishirim"
MAX_UPDATE_BYTES = 1024 * 1024


# ============================================================
# KONFIQURASIYA
# ============================================================

def required_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing environment variable: {name}"
        )

    return value


def public_url():
    base_url = required_env(
        "PUBLIC_BASE_URL"
    ).rstrip("/")

    if not base_url.startswith("https://"):
        raise RuntimeError(
            "PUBLIC_BASE_URL must use HTTPS"
        )

    return base_url


def worker_url():
    return public_url() + "/internal/qstash"


def test_url():
    return public_url() + "/internal/qstash-test"


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "NeBishirim",
    }


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):

    try:
        secret = required_env(
            "TELEGRAM_WEBHOOK_SECRET"
        )

        token = required_env(
            "QSTASH_TOKEN"
        )

        destination = worker_url()

    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Server configuration incomplete",
        )

    supplied = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    )

    if not hmac.compare_digest(
        supplied,
        secret,
    ):
        raise HTTPException(
            status_code=403,
            detail="Forbidden",
        )

    body = await request.body()

    if not body or len(body) > MAX_UPDATE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Invalid request size",
        )

    try:
        payload = json.loads(body)

    except (ValueError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid update",
        )

    update_id = payload.get("update_id")

    if type(update_id) is not int:
        raise HTTPException(
            status_code=400,
            detail="Missing update ID",
        )

    enqueue_url = (
        f"{QSTASH_API}/v2/enqueue/"
        f"{QUEUE_NAME}/{destination}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Upstash-Method": "POST",
        "Upstash-Deduplication-Id": (
            f"telegram-{update_id}"
        ),
        "Upstash-Retries": "2",
        "Upstash-Timeout": "240s",
        "Upstash-Redact-Fields": "body",
    }

    try:
        async with httpx.AsyncClient(
            timeout=8.0,
        ) as client:

            response = await client.post(
                enqueue_url,
                content=body,
                headers=headers,
            )

            response.raise_for_status()

    except httpx.HTTPError:
        LOG.exception(
            "QStash enqueue failed"
        )

        raise HTTPException(
            status_code=503,
            detail="Queue temporarily unavailable",
        )

    return {
        "ok": True,
        "queued": True,
    }


# ============================================================
# QSTASH IMZASININ YOXLANMASI
# ============================================================

def verify_qstash(body, signature, destination_url):

    receiver = Receiver(
        current_signing_key=required_env(
            "QSTASH_CURRENT_SIGNING_KEY"
        ),
        next_signing_key=required_env(
            "QSTASH_NEXT_SIGNING_KEY"
        ),
    )

    receiver.verify(
        body=body,
        signature=signature,
        url=destination_url,
    )


# ============================================================
# QSTASH TEST ENDPOINT
# ============================================================

@app.post("/internal/qstash-test")
async def qstash_test(request: Request):

    signature = request.headers.get(
        "Upstash-Signature",
        "",
    )

    if not signature:
        raise HTTPException(
            status_code=403,
            detail="Missing signature",
        )

    body_bytes = await request.body()

    if (
        not body_bytes
        or len(body_bytes) > MAX_UPDATE_BYTES
    ):
        raise HTTPException(
            status_code=413,
            detail="Invalid request size",
        )

    try:
        body = body_bytes.decode("utf-8")

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid encoding",
        )

    try:
        destination = test_url()

        verify_qstash(
            body,
            signature,
            destination,
        )

    except RuntimeError:
        LOG.exception(
            "QStash test configuration incomplete"
        )

        raise HTTPException(
            status_code=503,
            detail="Server configuration incomplete",
        )

    except Exception:
        raise HTTPException(
            status_code=403,
            detail="Invalid QStash signature",
        )

    try:
        payload = json.loads(body)

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    if (
        not isinstance(payload, dict)
        or payload.get("test") != "qstash_connection"
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid test payload",
        )

    # Telegram botu ve Neon burada isledilmir.
    return {
        "ok": True,
        "signature_valid": True,
    }


# ============================================================
# REAL QSTASH WORKER
# ============================================================

@app.post("/internal/qstash")
async def qstash_worker(request: Request):

    signature = request.headers.get(
        "Upstash-Signature",
        "",
    )

    if not signature:
        raise HTTPException(
            status_code=403,
            detail="Missing signature",
        )

    body_bytes = await request.body()

    if (
        not body_bytes
        or len(body_bytes) > MAX_UPDATE_BYTES
    ):
        raise HTTPException(
            status_code=413,
            detail="Invalid request size",
        )

    try:
        body = body_bytes.decode("utf-8")

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid encoding",
        )

    try:
        verify_qstash(
            body,
            signature,
            worker_url(),
        )

    except RuntimeError:
        LOG.exception(
            "QStash configuration incomplete"
        )

        raise HTTPException(
            status_code=503,
            detail="Server configuration incomplete",
        )

    except Exception:
        raise HTTPException(
            status_code=403,
            detail="Invalid QStash signature",
        )

    try:
        payload = json.loads(body)

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid update",
        )

    update_id = payload.get("update_id")

    if type(update_id) is not int:
        raise HTTPException(
            status_code=400,
            detail="Missing update ID",
        )

    from bot import create_application

    telegram_app = create_application()

    telegram_app.bot_data["_worker_error"] = False
    telegram_app.bot_data["_session_failed"] = False

    update = Update.de_json(
        payload,
        telegram_app.bot,
    )

    if update is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update",
        )

    user_id = update.effective_user.id if update.effective_user else None
    try:
        claimed = await asyncio.to_thread(claim_update, update_id, user_id)
    except Exception:
        LOG.exception("Update claim failed")
        raise HTTPException(status_code=503, detail="Database unavailable")
    if claimed == "processing":
        raise HTTPException(status_code=503, detail="Update already in progress")
    if claimed != "claimed":
        return {"ok": True, "duplicate": True, "status": claimed}
    telegram_app.bot_data["_delivery_update_id"] = update_id
    started = False

    try:
        async with telegram_app:

            await telegram_app.start()

            try:
                started = True
                await telegram_app.process_update(
                    update
                )

            finally:
                await telegram_app.stop()

        if telegram_app.bot_data.get("_worker_error"):
            raise RuntimeError(
                "Telegram handler failed"
            )

        if telegram_app.bot_data.get("_session_failed"):
            raise RuntimeError(
                "Session processing failed"
            )

        user = update.effective_user

        user_id = (
            user.id
            if user is not None
            else None
        )

        await asyncio.to_thread(
            finish_update,
            update_id,
            user_id,
        )

    except Exception:
        LOG.exception("Worker failed for update %s", update_id)
        if not started:
            await asyncio.to_thread(release_update, update_id)
            raise HTTPException(status_code=503, detail="Worker unavailable before processing")
        # Telegram side effects cannot be rolled back with PostgreSQL. Do not
        # rerun the handler after an ambiguous failure and create duplicate messages.
        await asyncio.to_thread(finish_update, update_id, user_id, "uncertain")
        return {"ok": True, "status": "uncertain"}

    return {
        "ok": True,
        "processed": True,
    }
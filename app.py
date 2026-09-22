"""NeBishirim — Telegram webhook və QStash worker."""

import asyncio
import hmac
import json
import logging
import os

import httpx
import psycopg

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from qstash import Receiver
from telegram import Update


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


def worker_url():
    base_url = required_env(
        "PUBLIC_BASE_URL"
    ).rstrip("/")

    if not base_url.startswith("https://"):
        raise RuntimeError(
            "PUBLIC_BASE_URL must use HTTPS"
        )

    return base_url + "/internal/qstash"


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

    update_id = payload.get(
        "update_id"
    )

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
# TEKRAR MESAJLARIN YOXLAMASI
# ============================================================

def already_processed(update_id):

    with psycopg.connect(
        required_env("DATABASE_URL"),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            SELECT 1
            FROM processed_updates
            WHERE update_id = %s
            """,
            (update_id,),
        ).fetchone()

    return row is not None


def mark_processed(update_id, user_id):

    with psycopg.connect(
        required_env("DATABASE_URL"),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        db.execute(
            """
            INSERT INTO processed_updates
                (update_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (update_id)
            DO NOTHING
            """,
            (update_id, user_id),
        )


# ============================================================
# TELEGRAM HANDLER XETALARI
# ============================================================

async def capture_bot_error(update, context):

    context.application.bot_data[
        "_worker_error"
    ] = True

    LOG.error(
        "Telegram handler failed: %s",
        type(context.error).__name__,
    )


# ============================================================
# QSTASH IMZASININ YOXLAMASI
# ============================================================

def verify_qstash(body, signature):

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
        url=worker_url(),
    )


# ============================================================
# QSTASH WORKER
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

    # Ilk once QStash imzasini yoxla.
    try:
        verify_qstash(
            body,
            signature,
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

    update_id = payload.get(
        "update_id"
    )

    if type(update_id) is not int:
        raise HTTPException(
            status_code=400,
            detail="Missing update ID",
        )

    # Evvel islenmis Telegram mesajini tekrarlama.
    try:
        done = await asyncio.to_thread(
            already_processed,
            update_id,
        )

    except Exception:
        LOG.exception(
            "Processed update lookup failed"
        )

        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    if done:
        return {
            "ok": True,
            "duplicate": True,
        }

    # Movcud Telegram handlerlerini islet.
    from bot import create_application

    telegram_app = create_application()

    telegram_app.bot_data[
        "_worker_error"
    ] = False

    telegram_app.bot_data[
        "_session_failed"
    ] = False

    telegram_app.add_error_handler(
        capture_bot_error
    )

    update = Update.de_json(
        payload,
        telegram_app.bot,
    )

    if update is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update",
        )

    try:

        async with telegram_app:

            await telegram_app.start()

            try:
                await telegram_app.process_update(
                    update
                )

            finally:
                await telegram_app.stop()

        if telegram_app.bot_data.get(
            "_worker_error"
        ):
            raise RuntimeError(
                "Telegram handler failed"
            )

        if telegram_app.bot_data.get(
            "_session_failed"
        ):
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
            mark_processed,
            update_id,
            user_id,
        )

    except Exception:
        LOG.exception(
            "Worker failed for update %s",
            update_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Update processing failed",
        )

    return {
        "ok": True,
        "processed": True,
    }
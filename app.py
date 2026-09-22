"""NeBishirim — Vercel veb tətbiqi."""

from fastapi import FastAPI

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "NeBishirim",
    }
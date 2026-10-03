import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.sessions import build_session_store
from app.rag.store import HandbookStore
from app.routers.qa import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.REDIS_ENABLED and int(os.getenv("WEB_CONCURRENCY", "1")) != 1:
        raise RuntimeError("In-memory sessions require WEB_CONCURRENCY=1")
    logging.basicConfig(level=logging.INFO)
    app.state.handbook = await asyncio.to_thread(
        HandbookStore, settings.HANDBOOK_PATH, settings.RAG_CHUNK_CHARS
    )
    app.state.sessions = build_session_store()
    app.state.openai = AsyncOpenAI(
        api_key=settings.OPENAI_API_TOKEN, timeout=settings.OPENAI_TIMEOUT_SECONDS,
    ) if settings.OPENAI_API_TOKEN else None
    try:
        await app.state.sessions.start()
        yield
    finally:
        try:
            await app.state.sessions.close()
        finally:
            if app.state.openai:
                await app.state.openai.close()


app = FastAPI(
    title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)
app.include_router(router)


@app.get("/health-check")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.VERSION}


@app.exception_handler(RedisError)
async def redis_error_handler(request, exc):
    logging.getLogger(__name__).error("Redis session storage is unavailable")
    return JSONResponse(status_code=503, content={"detail": "Session storage is unavailable. Please retry."})

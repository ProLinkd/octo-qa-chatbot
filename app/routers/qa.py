import json
import logging

import anyio

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.agents.qa_assistant.main import stream_reply
from app.core.authentication import verify_static_token
from app.core.config import settings
from app.core.sessions import SessionBusyError

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_static_token)])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/qa-sessions", status_code=201)
async def create_session(request: Request):
    try:
        return (await request.app.state.sessions.create()).detail()
    except OverflowError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/qa-sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    session = await request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found or expired")
    return session.detail()


def index_detail(index):
    return {"file": index.file, "chapters": index.chapter_count,
            "sections": index.section_count, "chunks": len(index.chunks)}


@router.get("/knowledge-base")
async def knowledge_base(request: Request):
    index = await anyio.to_thread.run_sync(request.app.state.handbook.get_index)
    return index_detail(index)


@router.post("/knowledge-base")
async def update_knowledge_base(request: Request, file: UploadFile = File(...)):
    try:
        if not file.filename or not file.filename.lower().endswith(".ts"):
            raise HTTPException(422, "Upload a .ts file using the 'file' field")
        content = await file.read(settings.HANDBOOK_MAX_UPLOAD_BYTES + 1)
        if len(content) > settings.HANDBOOK_MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Handbook file exceeds the upload size limit")
        if not content:
            raise HTTPException(422, "Handbook file must not be empty")
        try:
            index = await anyio.to_thread.run_sync(request.app.state.handbook.update, content)
        except ValueError as exc:
            raise HTTPException(422, "Invalid UTF-8 handbook: expected the howIvyWorksHandbook array and chapter/section schema") from exc
        except OSError as exc:
            logger.exception("Failed to persist handbook update")
            raise HTTPException(503, "Could not save the handbook update. Please retry.") from exc
        return index_detail(index)
    finally:
        await file.close()


@router.get("/knowledge-base/search")
async def search(request: Request, query: str = Query(min_length=1, max_length=8000)):
    index = await anyio.to_thread.run_sync(request.app.state.handbook.get_index)
    return {"results": [
        {**chunk.source(), "text": chunk.text}
        for chunk in index.search(query, settings.RAG_TOP_K)
    ]}


@router.post("/qa-sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, request: Request):
    store = request.app.state.sessions
    try:
        session = await store.acquire(session_id)
    except SessionBusyError as exc:
        raise HTTPException(409, "A reply is already in progress for this session") from exc
    if session is None:
        raise HTTPException(404, "Session not found or expired")
    try:
        index = await anyio.to_thread.run_sync(request.app.state.handbook.get_index)
        chunks = index.retrieve(body.message, session.messages, settings.RAG_TOP_K)
        if chunks and request.app.state.openai is None:
            raise HTTPException(503, "OpenAI API key is not configured")
    except BaseException:
        with anyio.CancelScope(shield=True):
            await store.release(session)
        raise
    sources = [chunk.source() for chunk in chunks]

    async def events():
        try:
            yield sse({"type": "sources", "sources": sources})
            parts = []
            async for delta in stream_reply(request.app.state.openai, body.message, session.messages, chunks):
                parts.append(delta)
                yield sse({"type": "delta", "content": delta})
            reply = "".join(parts)
            if not reply.strip():
                raise RuntimeError("Empty model response")
            await store.append_turn(session, body.message, reply, sources)
            yield sse({"type": "done", "content": reply, "sources": sources})
        except Exception:
            logger.exception("Q&A response failed")
            yield sse({"type": "error", "detail": "The Q&A assistant could not complete the response. Please retry."})
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await store.release(session)
                except Exception:
                    logger.exception("Failed to release Q&A session lock")

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })

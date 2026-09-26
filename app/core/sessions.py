import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

from redis.asyncio import Redis

from app.core.config import settings


class SessionBusyError(Exception):
    pass


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0
    messages: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    lease_token: str = ""

    def detail(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "messages": self.messages,
        }

    def completed_turn(self, question: str, answer: str, sources: list[dict], ttl: int) -> dict:
        return {
            **self.detail(),
            "messages": self.messages + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer, "sources": sources},
            ],
            "expires_at": time.time() + ttl,
        }


class SessionStore:
    def __init__(self, ttl: int, max_sessions: int):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self.sessions: dict[str, Session] = {}

    async def start(self):
        pass

    async def close(self):
        pass

    async def create(self) -> Session:
        now = time.time()
        for key, session in list(self.sessions.items()):
            if session.expires_at <= now and not session.lock.locked():
                del self.sessions[key]
        if len(self.sessions) >= self.max_sessions:
            raise OverflowError("Session capacity reached")
        session = Session(expires_at=now + self.ttl)
        self.sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        session = self.sessions.get(session_id)
        if session and session.expires_at <= time.time() and not session.lock.locked():
            del self.sessions[session_id]
            return None
        return session

    async def acquire(self, session_id: str) -> Session | None:
        session = await self.get(session_id)
        if session is None:
            return None
        if session.lock.locked():
            raise SessionBusyError
        await session.lock.acquire()
        return session

    async def release(self, session: Session):
        session.lock.release()

    async def append_turn(self, session: Session, question: str, answer: str, sources: list[dict]):
        result = session.completed_turn(question, answer, sources, self.ttl)
        session.messages = result["messages"]
        session.expires_at = result["expires_at"]


class RedisSessionStore:
    ACQUIRE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0} end
if not redis.call('SET', KEYS[2], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return {1}
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
return {2, raw}
"""
    SAVE = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
    RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, client: Redis, ttl: int, lease_seconds: int):
        self.client = client
        self.ttl = ttl
        self.lease_seconds = lease_seconds

    @staticmethod
    def key(session_id: str) -> str:
        return f"qa-session:{{{session_id}}}"

    async def start(self):
        await self.client.ping()

    async def close(self):
        await self.client.aclose()

    async def create(self) -> Session:
        session = Session(expires_at=time.time() + self.ttl)
        await self.client.set(self.key(session.session_id), json.dumps(session.detail()), ex=self.ttl)
        return session

    async def get(self, session_id: str) -> Session | None:
        raw = await self.client.get(self.key(session_id))
        return Session(**json.loads(raw)) if raw else None

    async def acquire(self, session_id: str) -> Session | None:
        key = self.key(session_id)
        token = uuid.uuid4().hex
        result = await self.client.eval(
            self.ACQUIRE, 2, key, key + ":lock", token,
            self.lease_seconds, max(self.ttl, self.lease_seconds),
        )
        if result[0] == 0:
            return None
        if result[0] == 1:
            raise SessionBusyError
        return Session(**json.loads(result[1]), lease_token=token)

    async def release(self, session: Session):
        await self.client.eval(self.RELEASE, 1, self.key(session.session_id) + ":lock", session.lease_token)

    async def append_turn(self, session: Session, question: str, answer: str, sources: list[dict]):
        result = session.completed_turn(question, answer, sources, self.ttl)
        key = self.key(session.session_id)
        saved = await self.client.eval(
            self.SAVE, 2, key, key + ":lock", session.lease_token,
            json.dumps(result), self.ttl,
        )
        if not saved:
            raise RuntimeError("Session lock expired before the answer could be saved")
        session.messages = result["messages"]
        session.expires_at = result["expires_at"]


def build_session_store() -> SessionStore | RedisSessionStore:
    if settings.REDIS_ENABLED:
        client = Redis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None, ssl=settings.REDIS_SSL,
            decode_responses=True, socket_connect_timeout=5, socket_timeout=5,
        )
        return RedisSessionStore(client, settings.SESSION_TTL_SECONDS, settings.OPENAI_TIMEOUT_SECONDS * 4 + 60)
    return SessionStore(settings.SESSION_TTL_SECONDS, settings.MAX_SESSIONS)

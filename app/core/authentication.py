import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_static_token(authorization: str | None = Security(api_key_header)):
    if not settings.STATIC_API_TOKEN:
        raise HTTPException(503, "Authentication is not configured")
    parts = (authorization or "").split()
    if (
        len(parts) != 2
        or parts[0] != "Token"
        or not secrets.compare_digest(parts[1].encode(), settings.STATIC_API_TOKEN.encode())
    ):
        raise HTTPException(401, "Invalid token")

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", extra="ignore", populate_by_name=True
    )

    APP_NAME: str = "octo_qa_chatbot"
    VERSION: str = "1.1.0"
    DEBUG: bool = True
    STATIC_API_TOKEN: str = Field(default_factory=lambda: os.getenv("auth-token", ""))
    OPENAI_API_TOKEN: str = Field(default_factory=lambda: os.getenv("openai-api-token", ""))
    QA_OPENAI_MODEL: str = Field(
        default_factory=lambda: os.getenv("interview-assistant-openai-model", "gpt-6-luna")
    )
    OPENAI_TIMEOUT_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("openai-timeout-seconds", "300")), gt=0
    )
    HANDBOOK_PATH: Path = PROJECT_ROOT / "howIvyWorksHandbook.ts"
    TRAINING_VIDEO_PATH: Path = PROJECT_ROOT / "trainingvideo.txt"
    HANDBOOK_MAX_UPLOAD_BYTES: int = Field(10 * 1024 * 1024, gt=0)
    RAG_TOP_K: int = Field(6, ge=1, le=20)
    RAG_CHUNK_CHARS: int = Field(2400, ge=500, le=10000)
    SESSION_TTL_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("session-ttl-seconds", str(10 * 24 * 60 * 60))), gt=0
    )
    REDIS_HOST: str = Field(default_factory=lambda: os.getenv("redis-host", ""))
    REDIS_PORT: int = Field(
        default_factory=lambda: int(os.getenv("redis-port", "6379")), ge=1, le=65535
    )
    REDIS_DB: int = Field(default_factory=lambda: int(os.getenv("redis-db", "0")), ge=0)
    REDIS_PASSWORD: str = Field(default_factory=lambda: os.getenv("redis-pass", ""))
    REDIS_SSL: bool = Field(
        default_factory=lambda: os.getenv("redis-ssl", "false").lower() == "true"
    )
    MAX_SESSIONS: int = Field(1000, gt=0)
    HISTORY_MAX_MESSAGES: int = Field(20, ge=2, le=100)
    MAX_OUTPUT_TOKENS: int = Field(4096, ge=256)

    @property
    def REDIS_ENABLED(self) -> bool:
        return bool(self.REDIS_HOST)


settings = Settings()

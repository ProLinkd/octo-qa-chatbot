from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", extra="ignore", populate_by_name=True
    )

    APP_NAME: str = "octo_qa_chatbot"
    VERSION: str = "1.1.0"
    DEBUG: bool = True
    
    STATIC_API_TOKEN: str = Field(
        "", validation_alias=AliasChoices("auth-token", "AUTH_TOKEN", "STATIC_API_TOKEN")
    )
    OPENAI_API_TOKEN: str = Field(
        "", validation_alias=AliasChoices("openai-api-token", "OPENAI_API_KEY", "OPENAI_API_TOKEN")
    )
    QA_OPENAI_MODEL: str = Field(
        "gpt-6-luna", validation_alias=AliasChoices("qa-openai-model", "QA_OPENAI_MODEL")
    )
    OPENAI_TIMEOUT_SECONDS: int = Field(300, gt=0)
    HANDBOOK_PATH: Path = PROJECT_ROOT / "howIvyWorksHandbook.ts"
    RAG_TOP_K: int = Field(6, ge=1, le=20)
    RAG_CHUNK_CHARS: int = Field(2400, ge=500, le=10000)
    SESSION_TTL_SECONDS: int = Field(
        864000, gt=0, validation_alias=AliasChoices("session-ttl-seconds", "SESSION_TTL_SECONDS")
    )
    REDIS_HOST: str = Field("", validation_alias=AliasChoices("redis-host", "REDIS_HOST"))
    REDIS_PORT: int = Field(6379, ge=1, le=65535, validation_alias=AliasChoices("redis-port", "REDIS_PORT"))
    REDIS_DB: int = Field(0, ge=0, validation_alias=AliasChoices("redis-db", "REDIS_DB"))
    REDIS_PASSWORD: str = Field("", validation_alias=AliasChoices("redis-pass", "REDIS_PASSWORD"))
    REDIS_SSL: bool = Field(False, validation_alias=AliasChoices("redis-ssl", "REDIS_SSL"))
    MAX_SESSIONS: int = Field(1000, gt=0)
    HISTORY_MAX_MESSAGES: int = Field(20, ge=2, le=100)
    MAX_OUTPUT_TOKENS: int = Field(4096, ge=256)

    @property
    def REDIS_ENABLED(self) -> bool:
        return bool(self.REDIS_HOST)


settings = Settings()

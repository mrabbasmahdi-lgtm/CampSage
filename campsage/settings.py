"""Small environment-backed settings object for the local prototype."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    model_id: str
    fallback_model_ids: tuple[str, ...]
    database_path: Path
    index_path: Path
    session_ttl_seconds: int = 8 * 60 * 60
    session_idle_seconds: int = 60 * 60
    cookie_secure: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        model_id = os.getenv("MODEL_ID", "").strip()
        fallback = tuple(
            value.strip()
            for value in os.getenv("FALLBACK_MODEL_IDS", "").split(",")
            if value.strip()
        )
        return cls(
            model_id=model_id,
            fallback_model_ids=fallback,
            database_path=Path(os.getenv("CAMPSAGE_DB", str(ROOT / "data" / "campsage.sqlite3"))),
            index_path=Path(os.getenv("CAMPSAGE_INDEX", str(ROOT / "data" / "notice_index.json"))),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", str(8 * 60 * 60))),
            session_idle_seconds=int(os.getenv("SESSION_IDLE_SECONDS", str(60 * 60))),
            cookie_secure=os.getenv("COOKIE_SECURE", "0").lower() in {"1", "true", "yes"},
        )

    def require_model(self) -> str:
        if not self.model_id:
            raise RuntimeError("MODEL_ID is not configured; set the exact working Gemma model ID in .env")
        return self.model_id

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    base_url: str | None = None
    model_timeout: float = 90
    agent_timeout: float = 240
    resend_key: str = ""
    mail_from: str = ""
    proxy: str | None = None
    frontend_dir: Path | None = None

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None) -> "Settings":
        load_dotenv()
        return cls(
            data_dir=Path(data_dir or os.getenv("NEWS_BRIEF_DATA_DIR", "data")).expanduser().resolve(),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            model_timeout=float(os.getenv("NEWS_BRIEF_MODEL_TIMEOUT", "90")),
            agent_timeout=float(os.getenv("NEWS_BRIEF_AGENT_TIMEOUT", "240")),
            resend_key=os.getenv("RESEND_API_KEY", ""),
            mail_from=os.getenv("MAIL_FROM", ""),
            proxy=os.getenv("NEWS_BRIEF_HTTP_PROXY") or None,
            frontend_dir=Path(os.environ["NEWS_BRIEF_FRONTEND_DIR"]).resolve()
            if os.getenv("NEWS_BRIEF_FRONTEND_DIR")
            else None,
        )

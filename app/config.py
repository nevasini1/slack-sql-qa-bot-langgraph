from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_signing_secret: str
    slack_allowed_team_id: str | None
    openai_api_key: str
    openai_model: str
    sqlite_path: str
    checkpointer_path: str
    max_agent_steps: int
    duplicate_event_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            slack_bot_token=_required("SLACK_BOT_TOKEN"),
            slack_signing_secret=_required("SLACK_SIGNING_SECRET"),
            slack_allowed_team_id=os.getenv("SLACK_ALLOWED_TEAM_ID"),
            openai_api_key=_required("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            sqlite_path=os.getenv("APP_SQLITE_PATH", "./state/app.db"),
            checkpointer_path=os.getenv(
                "LANGGRAPH_CHECKPOINTER_PATH", "./state/langgraph_memory.db"
            ),
            max_agent_steps=int(os.getenv("APP_MAX_AGENT_STEPS", "24")),
            duplicate_event_ttl_seconds=int(os.getenv("APP_DUPLICATE_EVENT_TTL_SECONDS", "300")),
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .slack_bot import SlackQaBot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = Settings.from_env()
bot = SlackQaBot.build(settings)
app = FastAPI(title="Slack SQLite Q&A Bot")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events(req: Request) -> JSONResponse:
    return await bot.handler.handle(req)

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from time import monotonic

from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from .agent import QaAgent
from .config import Settings


MENTION_RE = re.compile(r"<@[^>]+>")
AGENT_TIMEOUT_SECONDS = 75
PROGRESS_UPDATE_SECONDS = 8
LOGGER = logging.getLogger(__name__)


@dataclass
class SlackQaBot:
    settings: Settings
    qa_agent: QaAgent
    app: AsyncApp
    handler: AsyncSlackRequestHandler
    _conversation_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _processed_events: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, settings: Settings) -> "SlackQaBot":
        # Explicitly disable proxy usage from shell environment variables.
        # Some local dev environments inject localhost proxies that break Slack API calls.
        slack_client = AsyncWebClient(
            token=settings.slack_bot_token,
            proxy=None,
            trust_env_in_session=False,
        )
        app = AsyncApp(
            client=slack_client,
            signing_secret=settings.slack_signing_secret,
        )
        qa_agent = QaAgent.build(settings)
        bot = cls(
            settings=settings,
            qa_agent=qa_agent,
            app=app,
            handler=AsyncSlackRequestHandler(app),
        )
        bot._register_handlers()
        return bot

    def _register_handlers(self) -> None:
        @self.app.event("app_mention")
        async def on_app_mention(event: dict, client: AsyncWebClient, logger) -> None:
            try:
                await self._respond(event=event, client=client, force_reply=True)
            except Exception:
                logger.exception("Failed handling app_mention")

        @self.app.event("message")
        async def on_message(event: dict, client: AsyncWebClient, logger) -> None:
            # Skip bot and system messages.
            if event.get("subtype") is not None or not event.get("user"):
                return
            # Only process direct messages here.
            # Channel mentions are handled by app_mention to avoid duplicate replies.
            is_dm = event.get("channel_type") == "im"
            if not is_dm:
                return
            try:
                await self._respond(event=event, client=client, force_reply=False)
            except Exception:
                logger.exception("Failed handling message event")

    async def _respond(self, event: dict, client: AsyncWebClient, force_reply: bool) -> None:
        if not self._is_allowed_team(event):
            return

        channel = event["channel"]
        user = event["user"]
        ts = event["ts"]
        thread_ts = event.get("thread_ts", ts)
        event_key = f"{channel}:{user}:{ts}"
        if self._is_duplicate_event(event_key):
            LOGGER.info("Skipping duplicate Slack event %s", event_key)
            return
        cleaned_text = MENTION_RE.sub("", event.get("text", "")).strip()
        if not cleaned_text and force_reply:
            cleaned_text = "Please answer the user question from our database."
        if not cleaned_text:
            return

        conversation_id = f"{channel}:{thread_ts}:{user}"
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            waiting = await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Working on it... searching the database now.",
            )
            waiting_ts = waiting["ts"]
            progress_task = asyncio.create_task(
                self._delayed_progress_update(client, channel, waiting_ts)
            )

            try:
                run = await asyncio.wait_for(
                    asyncio.to_thread(self.qa_agent.answer_with_metrics, cleaned_text, conversation_id),
                    timeout=AGENT_TIMEOUT_SECONDS,
                )
                answer = str(run.get("answer", "I could not generate an answer.")).strip()
                LOGGER.info(
                    "conversation_id=%s latency_ms=%s tool_calls=%s",
                    conversation_id,
                    run.get("latency_ms"),
                    run.get("tool_calls"),
                )
            except asyncio.TimeoutError:
                answer = (
                    "This is taking longer than expected. Please try again with a narrower question, "
                    "or ask me to focus on one customer or artifact."
                )
            except Exception:
                answer = "I hit an internal error while querying the database. Please try again."
            finally:
                progress_task.cancel()

            if not answer:
                answer = "I could not generate an answer."

            try:
                await client.chat_update(
                    channel=channel,
                    ts=waiting_ts,
                    text=answer,
                )
            except Exception:
                # Fallback path if update fails for any Slack API reason.
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=answer,
                )

    def _is_allowed_team(self, event: dict) -> bool:
        allowed_team = self.settings.slack_allowed_team_id
        if not allowed_team:
            return True
        return event.get("team") == allowed_team

    def _is_duplicate_event(self, event_key: str) -> bool:
        now = monotonic()
        ttl = max(30, self.settings.duplicate_event_ttl_seconds)
        stale_keys = [k for k, ts in self._processed_events.items() if now - ts > ttl]
        for key in stale_keys:
            self._processed_events.pop(key, None)
        if event_key in self._processed_events:
            return True
        self._processed_events[event_key] = now
        return False

    async def _delayed_progress_update(
        self, client: AsyncWebClient, channel: str, waiting_ts: str
    ) -> None:
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
        try:
            await client.chat_update(
                channel=channel,
                ts=waiting_ts,
                text=(
                    "Still working... validating evidence across multiple artifacts "
                    "and database tables."
                ),
            )
        except Exception:
            # Non-critical UX improvement; ignore update failures.
            return

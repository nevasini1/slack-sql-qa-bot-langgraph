# Slack SQLite Q&A Bot (LangGraph)

Slack chatbot that answers questions using a provided SQLite database, with:

- LangGraph agent orchestration
- Read-only SQL tool grounding
- Persistent multi-turn memory keyed by Slack thread/user
- Slack Events API integration via FastAPI + Slack Bolt
- Slack request signing validation
- Tool latency / usage metrics for evaluation

## 1) Prerequisites

- Python 3.11+
- A Slack workspace where you can install an app
- An OpenAI API key
- `ngrok` (or equivalent tunnel) for local Slack callbacks

## 2) Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## 3) Download the take-home SQLite DB

```bash
./scripts/download_db.sh
```

This copies the largest `.db/.sqlite/.sqlite3` file from  
`langchain-ai/applied-ai-take-home-database` into `./state/app.db`.

## 4) Configure environment

```bash
cp .env.example .env
```

Set values in `.env`:

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- Optional: `SLACK_ALLOWED_TEAM_ID` to only accept one workspace/team
- `OPENAI_API_KEY`
- Optional: `OPENAI_MODEL` (default `gpt-4.1-mini`; recommended `gpt-4.1`)
- Optional: `APP_SQLITE_PATH` (default `./state/app.db`)
- Optional: `LANGGRAPH_CHECKPOINTER_PATH` for persistent thread memory DB
- Optional: `APP_MAX_AGENT_STEPS` (default `24`) to cap action depth
- Optional: `APP_DUPLICATE_EVENT_TTL_SECONDS` (default `300`)

## 5) Create and configure the Slack app

1. Create app from scratch in Slack.
2. Enable **Event Subscriptions**.
3. Set Request URL to:
   - `https://<your-ngrok-domain>/slack/events`
4. Subscribe to bot events:
   - `app_mention`
   - `message.channels`
   - `message.groups` (optional)
   - `message.im`
5. Add OAuth scopes:
   - `app_mentions:read`
   - `channels:history`
   - `groups:history` (if needed)
   - `im:history`
   - `chat:write`
6. Install app and copy bot token + signing secret into `.env`.

## 6) Run locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload
```

In another terminal:

```bash
ngrok http 3000
```

Use the HTTPS ngrok URL in Slack Event Subscriptions.

## 7) Behavior notes

- In channels, bot responds when mentioned.
- In DMs, bot responds to all user messages.
- Bot posts a quick "working..." message, then a delayed progress update for long runs.
- Multi-turn context is preserved per `channel + thread + user` and persisted via SQLite checkpointer.
- Per-thread locking prevents overlapping responses from racing in long conversations.
- Duplicate-event suppression reduces Slack retry duplicates.
- SQL execution is read-only and SELECT-only with extra guardrails:
  - read-only SQLite connection mode
  - blocked write/admin keywords
  - max query length and no multi-statement SQL
  - capped output rows and VM-step safety limit
- DB tool calls emit log metrics (`latency_ms`, `rows`, query size).
- Agent logs include latency and tool-call counts per response.

## 8) Security and auth highlights

- Slack request signature verification is handled by Slack Bolt (`signing_secret`).
- Optional team allowlist (`SLACK_ALLOWED_TEAM_ID`) rejects cross-workspace events.
- Secrets are loaded from environment only (`.env` for local dev).
- DB tools run in read-only SQLite mode with query-only enforcement.
- OpenAI key is used server-side only (never sent to Slack/users).

## 9) Suggested evaluation

Run automated evals:

```bash
source .venv/bin/activate
python scripts/eval_queries.py --out state/eval_results.json
```

The script reports:

- pass rate
- average answer score (required-term coverage)
- average latency
- average tool calls

Then test in Slack with the exact example queries from the assignment in a single thread, and follow with clarifying questions like:

- "Can you quote the exact rollout date from the source records?"
- "What evidence supports that competitor risk conclusion?"

This checks both retrieval quality and conversation memory.

## 10) Human design writeup helper

Use `DESIGN_TEMPLATE.md` as a checklist while writing your own human-authored `DESIGN.md`.

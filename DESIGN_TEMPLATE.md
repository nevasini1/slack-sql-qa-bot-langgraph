# Design Writeup Template (Human-Authored)

Use this as a checklist while writing your own `DESIGN.md` in your words.

## 1) Problem framing

- What user problem does the Slack bot solve?
- What are the explicit constraints from the assignment?
- What success looks like (accuracy, latency, UX expectations)?

## 2) System architecture

- Why you chose LangGraph (or DeepAgents) for orchestration.
- Request path: Slack event -> webhook -> agent -> DB tools -> Slack reply.
- How multi-turn context is preserved (threading and memory boundaries).

## 3) Data access and grounding

- How the SQLite tool layer works.
- What guardrails exist to prevent unsafe SQL.
- Why the tool set is minimal (`list_tables`, `describe_table`, `run_sql`).

## 4) Security and authentication

- Slack signature verification strategy.
- Token/secret management approach locally and in deployment.
- How outbound auth is handled for OpenAI and Slack APIs.

## 5) Agent behavior and quality

- Prompting strategy and grounding policy.
- Typical reasoning/action pattern for a query.
- Common failure modes and how the system handles ambiguity.

## 6) Performance and observability

- Latency and tool-call metrics collected.
- Evaluation workflow (query set, scoring approach, pass criteria).
- Expected bottlenecks and optimization plan.

## 7) User experience details

- Non-streaming UX choices (`working...` placeholder and update flow).
- Thread handling and long conversation behavior.
- Response formatting decisions for readability in Slack.

## 8) Trade-offs and future improvements

- What you intentionally did not build yet.
- How you'd improve retrieval precision and reduce tool calls.
- Production hardening steps (timeouts, retries, monitoring, deployment).

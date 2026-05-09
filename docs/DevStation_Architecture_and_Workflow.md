# AI Dev Station — Architecture & Workflow Master Document

**Audience:** operator of the personal AI software development station.
**Nodes:** Mac Mini 2015 (orchestrator), Mac Studio M1 Max (worker), MacBook Air M1 (client), iPhone (remote).
**Pipeline:** Linear → n8n → Pipeline (CrewAI) → Ollama / DeepSeek / Claude → GitHub PR.
**Admin plane:** Tailscale Mesh + Telegram.

## Architecture → [`Architecture.md`](./Architecture.md)

- 3-node topology + trust zones
- LLM fallback chain (local → DeepSeek → Claude)
- Self-healing properties

## Workflow → [`Workflow.md`](./Workflow.md)

- Sequence diagram: Linear ticket → GitHub PR
- Pipeline stages (plan → code → commit → PR)
- Error handling + idempotency

## Setup → [`Tooling_Checklist.md`](./Tooling_Checklist.md)

- Per-machine prerequisite checklist
- One-command setup
- Environment variables reference

## Self-Healing → [`Self_Healing.md`](./Self_Healing.md)

- Docker restart policies
- Ollama service management
- Pipeline retry logic
- Power failure recovery

## Deployment → [`DEPLOYMENT_PLAN.md`](./DEPLOYMENT_PLAN.md)

- Hardware requirements
- Step-by-step setup order
- Network requirements

## Operations → [`EXECUTION_GUIDE.md`](./EXECUTION_GUIDE.md)

- Daily operations (status, logs, run pipeline)
- Remote control via Telegram
- Backup & restore
- Updating

## Telegram → [`TELEGRAM_SETUP.md`](./TELEGRAM_SETUP.md)

- Bot creation via @BotFather
- Chat ID retrieval
- Available commands

## Agents Rules & Skills

See [`AGENTS_RULES.md`](../AGENTS_RULES.md) for reusable agent definitions, prompt templates, and skill configurations.

## Research & Integration

- [`AI_Dev_Station_2026_Hybrid_Research.md`](./AI_Dev_Station_2026_Hybrid_Research.md) — original research document
- [`AI_INTEGRATION_GUIDE.md`](./AI_INTEGRATION_GUIDE.md) — extending with other AI tools
- [`AIOps_and_GitOps_Strategy.md`](./AIOps_and_GitOps_Strategy.md) — AIOps and GitOps deployment strategy
# AI Software Dev Station 2026 — Production Hybrid Stack

> **Mục tiêu:** trạm phát triển AI tự trị (autonomous) chạy 24/7 trên phần cứng Apple Silicon, biến ticket Linear thành Pull Request đã pass test + đã tự review, tối ưu chi phí token bằng cách ưu tiên model local.
>
> **Phạm vi tài liệu:** kiến trúc tham chiếu sẵn-sản-xuất gồm sơ đồ dữ liệu, Docker Compose stack đầy đủ, implementation Linear→CrewAI, smart model router, RAG memory, Git workflow có rollback, và AI Gate (LLM reviewer + autofix runner).

---

## 0. Bản đồ vai trò phần cứng

| Node | Vai trò | Service thường trú | Lý do |
|---|---|---|---|
| **MacBook Air M1** | Workstation người dùng | Cursor/VS Code, Claude Code CLI, Tailscale client, **AI Router proxy** :4000 | Di động, phù hợp UI/IDE; KHÔNG host model nặng. |
| **Mac Studio M1 Max 64GB** | AI compute node | Ollama (qwen2.5-coder:32b, llama3.1:70b, deepseek-coder-v2, nomic-embed-text), CrewAI service, OpenCode runner, **pgvector** | 64GB unified memory đủ cho model 30–70B q4, throughput cao nhờ Metal. |
| **Mac Mini 2015** | Headless ops node | Docker Desktop / OrbStack, n8n, Telegram bridge, Postgres, Redis, Traefik, Prometheus, Grafana, Loki | Online 24/7, không tốn pin/CPU Mac Studio cho I/O. |

**Mạng:** mọi liên kết nội bộ qua **Tailscale** (mesh, mTLS). Webhook public (Linear, GitHub, Telegram) đi qua **Cloudflare Tunnel** → Traefik trên Mac Mini. Không có port nào bind `0.0.0.0` trừ Traefik (và Traefik chỉ accept origin từ Cloudflare).

---

## 1. Sơ đồ luồng dữ liệu tổng thể

```mermaid
flowchart LR
    subgraph CLOUD["Cloud / SaaS"]
        LIN[Linear API + Webhooks]
        GH[GitHub API]
        ANT[Anthropic Claude<br/>Sonnet 4.5 / 4.6]
        TG[Telegram Bot API]
    end

    subgraph MINI["Mac Mini 2015 — ops node (Docker)"]
        TR[Traefik :443]
        N8N[n8n :5678]
        BRIDGE[Telegram Bridge]
        REDIS[(Redis<br/>queue + cache)]
        PG[(Postgres + pgvector<br/>memory + audit)]
        PROM[Prometheus / Grafana / Loki]
        CFD[cloudflared]
    end

    subgraph STUDIO["Mac Studio M1 Max — AI compute"]
        OLL[Ollama :11434]
        CREW[CrewAI service :8001]
        OCR[OpenCode runner :8002]
        RAG[RAG indexer]
        WS[(Local git workspaces)]
    end

    subgraph AIR["MacBook Air M1 — workstation"]
        CC[Claude Code CLI]
        IDE[Cursor / VS Code]
        ROUTER[AI Router :4000]
    end

    LIN -- webhook --> CFD --> TR --> N8N
    TG <-- webhook --> TR --> BRIDGE
    GH -- webhook --> CFD --> TR --> N8N

    N8N -- enqueue --> REDIS
    REDIS -- consume --> CREW
    CREW <--> OLL
    CREW <--> RAG
    RAG <--> PG
    CREW --> OCR
    OCR --> WS
    OCR -- push --> GH
    GH -- PR url --> N8N --> LIN
    N8N --> BRIDGE --> TG

    CC --> ROUTER
    IDE --> ROUTER
    ROUTER -- simple --> OLL
    ROUTER -- hard --> ANT
    ROUTER -- log --> PROM
```

**Ba luồng chính:**
1. **Coding loop:** Linear → n8n → Redis queue → CrewAI → OpenCode runner → GitHub PR → comment Linear.
2. **Memory loop:** RAG indexer ingest repo + Linear history + ADR vào pgvector → CrewAI retrieve khi planning/coding/reviewing.
3. **Token-saving loop:** Claude Code/Cursor → AI Router → ưu tiên Ollama local; fallback Claude Sonnet 4.5/4.6 khi cần reasoning sâu.

---

## 2. Docker Compose production stack — **per-node split**

Triết lý: **mỗi node chỉ chạy phần thuộc về nó.** Không dồn 1 file `docker-compose.yml` khổng lồ chạy hết mọi service vì:

- Mac Mini 2015 (16GB) sẽ chết khi cõng cả Ollama + Grafana stack + n8n workers.
- Mac Studio nên dành toàn bộ RAM/Metal cho Ollama; không host Postgres/Loki ở đó.
- MacBook Air đôi khi tắt/đem đi → AI Router bound vào Air không phù hợp 24/7. Air chạy 1 instance LOCAL ưu tiên dùng tại chỗ; instance "production" chạy trên Mini và là single-source-of-truth cho server-side flows.

### 2.1 Cấu trúc thư mục (1 repo `~/devstation/`, deploy lên cả 3 node qua rsync/Tailscale)

```
~/devstation/
├── bin/
│   └── devstation.sh                 # unified install/manage script (xem §15)
├── compose/
│   ├── common.yml                    # YAML anchors: networks, logging, healthcheck
│   ├── mini.yml                      # Mac Mini  — ops/orchestration node
│   ├── studio.yml                    # Mac Studio — AI compute node
│   └── air.yml                       # MacBook Air — local dev router (optional)
├── env/
│   ├── .env.shared                   # giá trị dùng chung (domain, TZ, Tailscale IPs)
│   ├── .env.mini
│   ├── .env.studio
│   └── .env.air
├── traefik/        { traefik.yml, dynamic.yml }
├── postgres/init/  { 01-pgvector.sql }
├── redis/          { redis.conf }
├── prometheus/     { prometheus.yml, rules/ }
├── grafana/        { provisioning/{datasources,dashboards}, dashboards/devstation.json }
├── loki/           { loki-config.yml }
├── promtail/       { promtail-config.yml }
├── ai-router/      { config.yaml, smart_router.py }
├── telegram-bridge/{ Dockerfile, bot.py, miniapp/ }
├── ai-gate/        { Dockerfile, runner.py, templates/ }
└── iphone-pwa/     { web/ (PWA static), Dockerfile }
```

### 2.2 `compose/common.yml` — anchors dùng chung

```yaml
# compose/common.yml
# Loaded first: provides reusable YAML anchors. Other files reference them.

x-logging: &default-logging
  driver: "loki"
  options:
    loki-url: "http://127.0.0.1:3100/loki/api/v1/push"
    loki-retries: "5"
    loki-batch-size: "400"
    loki-external-labels: "node=${NODE_NAME}"

x-restart: &default-restart
  restart: unless-stopped

x-healthcheck-tcp: &healthcheck-tcp
  interval: 15s
  timeout: 5s
  retries: 5
  start_period: 30s

networks:
  edge:        # public-facing via Traefik
  internal:    # private mesh (intra-node)
    internal: true
  mesh:        # shared via Tailscale (cross-node)
    driver: bridge
```

### 2.3 `compose/mini.yml` — Mac Mini (ops/orchestration)

Profiles: `core` (mặc định, bắt buộc), `monitoring` (thêm Prometheus/Grafana/Loki), `ingress` (Traefik+cloudflared), `ai-control` (AI Router + AI Gate + Telegram bridge + iPhone PWA).

```yaml
# ~/devstation/compose/mini.yml
name: devstation-mini

include:
  - common.yml

volumes:
  pg_data:
  redis_data:
  n8n_data:
  prom_data:
  grafana_data:
  loki_data:
  traefik_letsencrypt:

services:

  # ───────── ingress profile ─────────
  traefik:
    profiles: ["ingress", "core"]
    image: traefik:v3.1
    <<: *default-restart
    command:
      - --providers.file.filename=/etc/traefik/dynamic.yml
      - --providers.docker=true
      - --providers.docker.exposedByDefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.websecure.http.tls=true
      - --metrics.prometheus=true
      - --accesslog=true
    ports: ["80:80", "443:443"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ../traefik/traefik.yml:/etc/traefik/traefik.yml:ro
      - ../traefik/dynamic.yml:/etc/traefik/dynamic.yml:ro
      - traefik_letsencrypt:/letsencrypt
    networks: [edge, internal]
    logging: *default-logging

  cloudflared:
    profiles: ["ingress", "core"]
    image: cloudflare/cloudflared:latest
    <<: *default-restart
    command: ["tunnel", "--no-autoupdate", "run", "--token", "${CLOUDFLARED_TOKEN}"]
    networks: [edge]
    logging: *default-logging

  # ───────── core orchestration ─────────
  postgres:
    profiles: ["core"]
    image: pgvector/pgvector:pg16
    <<: *default-restart
    environment:
      POSTGRES_USER: devstation
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: devstation
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ../postgres/init:/docker-entrypoint-initdb.d:ro
    networks: [internal, mesh]
    healthcheck:
      <<: *healthcheck-tcp
      test: ["CMD-SHELL", "pg_isready -U devstation"]
    logging: *default-logging

  redis:
    profiles: ["core"]
    image: redis:7-alpine
    <<: *default-restart
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    volumes:
      - redis_data:/data
      - ../redis/redis.conf:/usr/local/etc/redis/redis.conf:ro
    networks: [internal, mesh]
    healthcheck:
      <<: *healthcheck-tcp
      test: ["CMD", "redis-cli", "ping"]
    logging: *default-logging

  n8n:
    profiles: ["core"]
    image: n8nio/n8n:latest
    <<: *default-restart
    environment:
      N8N_HOST: ${N8N_HOST}
      N8N_PORT: 5678
      N8N_PROTOCOL: https
      WEBHOOK_URL: https://${N8N_HOST}/
      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: postgres
      DB_POSTGRESDB_DATABASE: devstation
      DB_POSTGRESDB_USER: devstation
      DB_POSTGRESDB_PASSWORD: ${POSTGRES_PASSWORD}
      QUEUE_BULL_REDIS_HOST: redis
      EXECUTIONS_MODE: queue
      N8N_ENCRYPTION_KEY: ${N8N_ENCRYPTION_KEY}
      GENERIC_TIMEZONE: ${TZ}
    volumes:
      - n8n_data:/home/node/.n8n
    depends_on: [postgres, redis]
    networks: [edge, internal]
    labels:
      - traefik.enable=true
      - traefik.http.routers.n8n.rule=Host(`${N8N_HOST}`)
      - traefik.http.routers.n8n.entrypoints=websecure
      - traefik.http.services.n8n.loadbalancer.server.port=5678
    logging: *default-logging

  n8n-worker:
    profiles: ["core"]
    image: n8nio/n8n:latest
    <<: *default-restart
    command: ["worker"]
    environment:
      N8N_ENCRYPTION_KEY: ${N8N_ENCRYPTION_KEY}
      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: postgres
      DB_POSTGRESDB_DATABASE: devstation
      DB_POSTGRESDB_USER: devstation
      DB_POSTGRESDB_PASSWORD: ${POSTGRES_PASSWORD}
      QUEUE_BULL_REDIS_HOST: redis
    depends_on: [n8n]
    networks: [internal]
    deploy: { replicas: 2 }
    logging: *default-logging

  # ───────── ai-control profile ─────────
  ai-router:
    profiles: ["ai-control"]
    image: ghcr.io/berriai/litellm:main-stable
    <<: *default-restart
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DATABASE_URL: postgresql://devstation:${POSTGRES_PASSWORD}@postgres:5432/devstation
      REDIS_URL: redis://redis:6379/1
      OLLAMA_BASE_URL: http://${TS_STUDIO_IP}:11434
    volumes:
      - ../ai-router:/app:ro
    depends_on: [postgres, redis]
    networks: [edge, internal, mesh]
    labels:
      - traefik.enable=true
      - traefik.http.routers.router.rule=Host(`router.${PUBLIC_DOMAIN}`)
      - traefik.http.routers.router.entrypoints=websecure
      - traefik.http.services.router.loadbalancer.server.port=4000
    logging: *default-logging

  ai-gate:
    profiles: ["ai-control"]
    build: ../ai-gate
    <<: *default-restart
    environment:
      ROUTER_URL: http://ai-router:4000
      ROUTER_KEY: ${LITELLM_MASTER_KEY}
      DATABASE_URL: postgresql://devstation:${POSTGRES_PASSWORD}@postgres:5432/devstation
      REDIS_URL: redis://redis:6379/2
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      MAX_AUTOFIX_ITERATIONS: "3"
      RISK_HUMAN_THRESHOLD: "60"
    depends_on: [redis, ai-router]
    networks: [internal]
    logging: *default-logging

  telegram-bridge:
    profiles: ["ai-control"]
    build: ../telegram-bridge
    <<: *default-restart
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_ALLOWED_CHAT_IDS: ${TELEGRAM_ALLOWED_CHAT_IDS}
      REDIS_URL: redis://redis:6379/0
      N8N_BASE_URL: http://n8n:5678
      N8N_WEBHOOK_SECRET: ${N8N_WEBHOOK_SECRET}
      CREWAI_BASE_URL: http://${TS_STUDIO_IP}:8001
      MINIAPP_PUBLIC_URL: https://app.${PUBLIC_DOMAIN}
    depends_on: [redis, n8n]
    networks: [edge, internal, mesh]
    labels:
      - traefik.enable=true
      - traefik.http.routers.tg.rule=Host(`tg.${PUBLIC_DOMAIN}`)
      - traefik.http.routers.tg.entrypoints=websecure
      - traefik.http.services.tg.loadbalancer.server.port=7700
    logging: *default-logging

  iphone-pwa:
    profiles: ["ai-control"]
    build: ../iphone-pwa
    <<: *default-restart
    environment:
      PUBLIC_API_BASE: https://api.${PUBLIC_DOMAIN}
      LINEAR_OAUTH_CLIENT_ID: ${LINEAR_OAUTH_CLIENT_ID}
    networks: [edge, internal]
    labels:
      - traefik.enable=true
      - traefik.http.routers.iphone.rule=Host(`app.${PUBLIC_DOMAIN}`)
      - traefik.http.routers.iphone.entrypoints=websecure
      - traefik.http.services.iphone.loadbalancer.server.port=80
    logging: *default-logging

  # ───────── monitoring profile ─────────
  prometheus:
    profiles: ["monitoring"]
    image: prom/prometheus:latest
    <<: *default-restart
    volumes:
      - ../prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../prometheus/rules:/etc/prometheus/rules:ro
      - prom_data:/prometheus
    networks: [internal]
    logging: *default-logging

  grafana:
    profiles: ["monitoring"]
    image: grafana/grafana:latest
    <<: *default-restart
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_DEFAULT_THEME: light
      GF_AUTH_ANONYMOUS_ENABLED: "false"
    volumes:
      - grafana_data:/var/lib/grafana
      - ../grafana/provisioning:/etc/grafana/provisioning:ro
      - ../grafana/dashboards:/var/lib/grafana/dashboards:ro
    networks: [edge, internal]
    labels:
      - traefik.enable=true
      - traefik.http.routers.grafana.rule=Host(`grafana.${PUBLIC_DOMAIN}`)
      - traefik.http.routers.grafana.entrypoints=websecure
      - traefik.http.services.grafana.loadbalancer.server.port=3000
    logging: *default-logging

  loki:
    profiles: ["monitoring"]
    image: grafana/loki:latest
    <<: *default-restart
    command: ["-config.file=/etc/loki/loki-config.yml"]
    volumes:
      - ../loki/loki-config.yml:/etc/loki/loki-config.yml:ro
      - loki_data:/loki
    ports: ["127.0.0.1:3100:3100"]   # Docker daemon log driver targets this
    networks: [internal]

  promtail:
    profiles: ["monitoring"]
    image: grafana/promtail:latest
    <<: *default-restart
    command: ["-config.file=/etc/promtail/promtail-config.yml"]
    volumes:
      - ../promtail/promtail-config.yml:/etc/promtail/promtail-config.yml:ro
      - /var/log:/var/log:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [internal]
    depends_on: [loki]

  # ───────── exporters (monitoring) ─────────
  cadvisor:
    profiles: ["monitoring"]
    image: gcr.io/cadvisor/cadvisor:latest
    <<: *default-restart
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker:/var/lib/docker:ro
    networks: [internal]

  node-exporter:
    profiles: ["monitoring"]
    image: prom/node-exporter:latest
    <<: *default-restart
    networks: [internal]

  postgres-exporter:
    profiles: ["monitoring"]
    image: prometheuscommunity/postgres-exporter
    <<: *default-restart
    environment:
      DATA_SOURCE_NAME: "postgresql://devstation:${POSTGRES_PASSWORD}@postgres:5432/devstation?sslmode=disable"
    networks: [internal]
    depends_on: [postgres]

  redis-exporter:
    profiles: ["monitoring"]
    image: oliver006/redis_exporter
    <<: *default-restart
    environment:
      REDIS_ADDR: redis://redis:6379
    networks: [internal]
    depends_on: [redis]

  blackbox-exporter:
    profiles: ["monitoring"]
    image: prom/blackbox-exporter:latest
    <<: *default-restart
    networks: [internal]
```

### 2.4 `compose/studio.yml` — Mac Studio (AI compute)

> Note: Ollama tự cài bằng `brew install ollama` (chạy native, KHÔNG trong Docker — vì Docker Desktop không truy cập được Apple Metal hiệu quả). File compose dưới chỉ phục vụ các side-car nhẹ.

```yaml
# ~/devstation/compose/studio.yml
name: devstation-studio

include:
  - common.yml

volumes:
  workspaces:                 # /Volumes/work cloned repos

services:

  crewai:
    image: python:3.11-slim
    <<: *default-restart
    working_dir: /app
    command: ["bash", "-lc", "pip install -e . && uvicorn crew_service:app --host 0.0.0.0 --port 8001"]
    environment:
      ROUTER_URL: http://${TS_MINI_IP}:4000
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      DATABASE_URL: postgresql://devstation:${POSTGRES_PASSWORD}@${TS_MINI_IP}:5432/devstation
      REDIS_HOST: ${TS_MINI_IP}
      REDIS_URL: redis://${TS_MINI_IP}:6379/3
      CONSUMER_NAME: studio-1
    volumes:
      - ../crewai:/app:ro
      - workspaces:/Volumes/work
    ports: ["8001:8001"]      # Tailscale-bound at OS level
    networks: [mesh]
    logging: *default-logging

  opencode-runner:
    image: node:20-alpine
    <<: *default-restart
    working_dir: /app
    command: ["sh", "-lc", "npm i -g opencode@1.14.24 && node server.mjs"]
    environment:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      WORKSPACES_DIR: /Volumes/work
    volumes:
      - ../opencode-runner:/app:ro
      - workspaces:/Volumes/work
    ports: ["8002:8002"]
    networks: [mesh]
    logging: *default-logging

  rag-indexer:
    image: python:3.11-slim
    <<: *default-restart
    working_dir: /app
    command: ["bash", "-lc", "pip install -e . && python -m memory.worker"]
    environment:
      DATABASE_URL: postgresql://devstation:${POSTGRES_PASSWORD}@${TS_MINI_IP}:5432/devstation
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      REDIS_URL: redis://${TS_MINI_IP}:6379/4
    volumes:
      - ../memory:/app:ro
      - workspaces:/Volumes/work:ro
    networks: [mesh]
    logging: *default-logging

  promtail-studio:
    image: grafana/promtail:latest
    <<: *default-restart
    command: ["-config.file=/etc/promtail/promtail-config.yml"]
    environment:
      LOKI_URL: http://${TS_MINI_IP}:3100/loki/api/v1/push
    volumes:
      - ../promtail/promtail-config.yml:/etc/promtail/promtail-config.yml:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [mesh]

  node-exporter-studio:
    image: prom/node-exporter:latest
    <<: *default-restart
    ports: ["9100:9100"]
    networks: [mesh]
```

### 2.5 `compose/air.yml` — MacBook Air (LOCAL-only AI Router)

Chạy 1 instance LiteLLM **bound `127.0.0.1`** dành cho Claude Code/Cursor *khi đang ngồi máy này*. Không cần Postgres/Redis: dùng cache file. Khi máy tắt, IDE auto-fall-back sang `https://router.devstn.xyz` (instance trên Mini).

```yaml
# ~/devstation/compose/air.yml  (chỉ chạy local)
name: devstation-air

include:
  - common.yml

services:
  ai-router-local:
    image: ghcr.io/berriai/litellm:main-stable
    <<: *default-restart
    command: ["--config", "/app/config-local.yaml", "--port", "4000"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OLLAMA_BASE_URL: http://${TS_STUDIO_IP}:11434
    volumes:
      - ../ai-router:/app:ro
    ports: ["127.0.0.1:4000:4000"]
    logging: *default-logging
```

### 2.6 `.env` layout — split theo node, share giá trị chung

```bash
# env/.env.shared  (commit .example, không commit giá trị)
TZ=Asia/Ho_Chi_Minh
PUBLIC_DOMAIN=devstn.xyz
TS_MINI_IP=100.y.y.y
TS_STUDIO_IP=100.x.x.x
ANTHROPIC_API_KEY=sk-ant-xxx
LITELLM_MASTER_KEY=sk-litellm-master-xxx
LINEAR_API_KEY=lin_api_xxx
LINEAR_WEBHOOK_SECRET=lin_wh_xxx
LINEAR_OAUTH_CLIENT_ID=lin_oauth_xxx
GITHUB_TOKEN=ghp_xxx
GITHUB_WEBHOOK_SECRET=gh_wh_xxx
TELEGRAM_BOT_TOKEN=xxx:yyy
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

```bash
# env/.env.mini
NODE_NAME=mini
N8N_HOST=n8n.devstn.xyz
N8N_ENCRYPTION_KEY=...
N8N_WEBHOOK_SECRET=...
POSTGRES_PASSWORD=...
GRAFANA_ADMIN_PASSWORD=...
CLOUDFLARED_TOKEN=eyJh...
```

```bash
# env/.env.studio
NODE_NAME=studio
# (giá trị cross-node lấy từ .env.shared, gộp khi compose chạy)
```

```bash
# env/.env.air
NODE_NAME=air
```

`bin/devstation.sh` sẽ gộp `.env.shared` + `.env.<node>` thành 1 file `.env.merged` rồi truyền vào `docker compose --env-file`.

### 2.7 `postgres/init/01-pgvector.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Long-term codebase memory (chunks of code, ADRs, Linear comments)
CREATE TABLE IF NOT EXISTS memory_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type TEXT NOT NULL,        -- 'code' | 'linear' | 'adr' | 'pr_review' | 'episodic'
  source_ref  TEXT NOT NULL UNIQUE, -- "repo:path#sym" | "linear:NHA-12" | "pr:42" ...
  repo        TEXT,
  language    TEXT,
  content     TEXT NOT NULL,
  metadata    JSONB NOT NULL DEFAULT '{}',
  embedding   vector(768) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON memory_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memory_chunks (source_type, repo);
CREATE INDEX ON memory_chunks USING gin (metadata jsonb_path_ops);

CREATE TABLE IF NOT EXISTS webhook_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source      TEXT NOT NULL,
  delivery_id TEXT NOT NULL UNIQUE,
  payload     JSONB NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',
  attempts    INT  NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gate_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pr_number    INT NOT NULL,
  iteration    INT NOT NULL,
  reviewer     TEXT NOT NULL,
  model        TEXT NOT NULL,
  risk_score   INT,
  status       TEXT NOT NULL,
  issues_json  JSONB NOT NULL,
  fixed_count  INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lightweight task ledger for the iPhone PWA / Mini App
CREATE TABLE IF NOT EXISTS task_ledger (
  issue_id   TEXT PRIMARY KEY,         -- e.g. NHA-42
  pr_url     TEXT,
  state      TEXT NOT NULL,            -- queued|running|review|merged|reverted|failed
  risk_score INT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload    JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX ON task_ledger (state, updated_at DESC);
```

### 2.8 Phân bổ service theo node (cheat sheet)

| Service | mini | studio | air |
|---|:-:|:-:|:-:|
| Traefik / cloudflared | ✓ | | |
| Postgres + pgvector | ✓ | | |
| Redis | ✓ | | |
| n8n + workers | ✓ | | |
| AI Router (production) | ✓ | | |
| AI Gate | ✓ | | |
| Telegram bridge | ✓ | | |
| iPhone PWA | ✓ | | |
| Prometheus / Grafana / Loki / Promtail | ✓ | | |
| Exporters (cadvisor, node, pg, redis, blackbox) | ✓ | | |
| Ollama (native, không Docker) | | ✓ | |
| CrewAI service | | ✓ | |
| OpenCode runner | | ✓ | |
| RAG indexer | | ✓ | |
| AI Router (local-only) | | | ✓ |


---

## 3. Linear Webhook → CrewAI execution

### 3.1 End-to-end sequence

```mermaid
sequenceDiagram
    autonumber
    participant L as Linear
    participant CF as Cloudflare Tunnel
    participant TR as Traefik
    participant N as n8n (webhook)
    participant PG as Postgres (idempotency)
    participant Q as Redis Stream queue
    participant W as n8n worker
    participant C as CrewAI /run_ticket
    participant OC as OpenCode runner
    participant GH as GitHub
    participant G as AI Gate

    L->>CF: POST /webhook/linear (Linear-Signature)
    CF->>TR: forward
    TR->>N: route by Host
    N->>N: HMAC verify (LINEAR_WEBHOOK_SECRET)
    N->>PG: INSERT webhook_events ON CONFLICT(delivery_id) DO NOTHING
    alt new event
        N->>Q: XADD tickets * issue_id ... priority ...
        N-->>L: 200 OK (fast ack)
    else duplicate
        N-->>L: 200 OK (already processed)
    end

    Q-->>W: XREADGROUP
    W->>C: POST /run_ticket (with retry budget, deadline)
    C->>C: plan + retrieve memory
    C->>OC: execute task on workspace
    OC->>GH: push branch, open PR
    OC-->>C: pr_url
    C->>G: trigger gate run on PR
    G->>G: review + autofix loop
    G-->>C: final verdict
    C-->>W: result {pr_url, verdict, tokens, runtime}
    W->>N: mark webhook_events.done
    W->>L: comment Linear with PR url + verdict
```

### 3.2 Bảo mật webhook (HMAC + idempotency)

n8n node Code (chạy ngay sau Webhook node):

```js
// n8n Code node — verify Linear signature & dedupe
const crypto = require('crypto');
const raw = $binary.raw ? Buffer.from($binary.raw.data, 'base64') : Buffer.from(JSON.stringify($json), 'utf8');
const sig = $headers['linear-signature'];
const computed = crypto.createHmac('sha256', $env.LINEAR_WEBHOOK_SECRET).update(raw).digest('hex');
if (!sig || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(computed))) {
  throw new Error('invalid signature');
}
const deliveryId = $headers['linear-delivery'] || $json.id;
return [{
  json: {
    deliveryId,
    type:    $json.type,         // 'Issue' | 'Comment' | ...
    action:  $json.action,       // 'create' | 'update' | 'remove'
    data:    $json.data,
    receivedAt: new Date().toISOString(),
  },
  pairedItem: { item: 0 },
}];
```

Idempotency node (Postgres):

```sql
INSERT INTO webhook_events (source, delivery_id, payload)
VALUES ('linear', $1, $2)
ON CONFLICT (delivery_id) DO NOTHING
RETURNING id;
```

Nếu `RETURNING` rỗng → là duplicate, skip enqueue.

### 3.3 Filter tickets đáng chạy

```js
// n8n Code node — gate
const d = $json.data;
const labels = (d.labels || []).map(l => l.name);
const isCreate = $json.action === 'create' || ($json.action === 'update' && d.stateChanged?.toState === 'Todo');
const eligible = isCreate && labels.includes('ai-build');
return [{ json: { ...$json, eligible } }];
```

IF node `eligible == true` → enqueue; ngược lại → kết thúc.

### 3.4 Enqueue lên Redis Stream

n8n HTTP node (gọi qua Redis REST hoặc Redis node):

```
XADD tickets * \
     issue_id   {{$json.data.identifier}} \
     issue_db_id {{$json.data.id}} \
     title      {{$json.data.title}} \
     priority   {{$json.data.priority}} \
     repo       {{$json.data.project.repository || "thanhnhan2tn/mini-dev-station"}} \
     attempts   0 \
     deadline   {{Date.now() + 30*60*1000}}
```

### 3.5 CrewAI consumer — `crew_service.py`

```python
# Mac Studio (native): uvicorn crew_service:app --host 0.0.0.0 --port 8001
import os, json, time, hmac, hashlib, redis, asyncio, traceback, logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from crewai import Agent, Task, Crew, Process

log = logging.getLogger("crewai-svc")
app = FastAPI()
r = redis.Redis(host=os.environ["REDIS_HOST"], port=6379, decode_responses=True)
GROUP, CONSUMER = "crew-grp", os.environ.get("CONSUMER_NAME", "studio-1")

# Init consumer group once
try:
    r.xgroup_create("tickets", GROUP, id="$", mkstream=True)
except redis.ResponseError:
    pass

class TicketPayload(BaseModel):
    issue_id: str
    issue_db_id: str
    title: str
    body: str = ""
    repo: str
    priority: int = 3

@app.post("/run_ticket")
async def run_ticket(p: TicketPayload):
    return await _execute(p)

@app.on_event("startup")
async def consumer_loop():
    asyncio.create_task(_loop())

async def _loop():
    while True:
        try:
            msgs = r.xreadgroup(GROUP, CONSUMER, {"tickets": ">"}, count=1, block=10_000)
            for _stream, items in msgs or []:
                for msg_id, fields in items:
                    p = TicketPayload(
                        issue_id=fields["issue_id"],
                        issue_db_id=fields["issue_db_id"],
                        title=fields["title"],
                        body=fields.get("body", ""),
                        repo=fields["repo"],
                        priority=int(fields.get("priority", 3)),
                    )
                    try:
                        result = await _execute(p)
                        r.xack("tickets", GROUP, msg_id)
                        r.xadd("ticket-results", {"id": p.issue_id, "result": json.dumps(result)})
                    except Exception as e:
                        log.exception("ticket failed")
                        attempts = int(fields.get("attempts", "0")) + 1
                        if attempts >= 3:
                            r.xack("tickets", GROUP, msg_id)
                            r.xadd("ticket-deadletter", {**fields, "error": str(e)[:1000]})
                        else:
                            # requeue with backoff
                            await asyncio.sleep(2 ** attempts)
                            r.xadd("tickets", {**fields, "attempts": attempts})
                            r.xack("tickets", GROUP, msg_id)
        except Exception:
            log.exception("loop crash"); await asyncio.sleep(5)

async def _execute(p: TicketPayload) -> dict:
    from agents import build_crew
    crew = build_crew(p)
    out = crew.kickoff()
    return out
```

### 3.6 CrewAI agent topology (`agents.py`)

```python
# agents.py
from crewai import Agent, Task, Crew, Process
from crewai_tools import tool
import subprocess, os, json, httpx
from memory import retrieve as rag_retrieve
from gitops import open_pr_safely

ROUTER = os.environ["ROUTER_URL"]            # e.g. http://router.devstn.xyz
ROUTER_KEY = os.environ["LITELLM_MASTER_KEY"]

def llm(tag: str) -> str:
    """Route through AI Router so the smart-routing policy applies."""
    return f"openai/{tag}"   # LiteLLM is OpenAI-compatible

@tool("retrieve_memory")
def retrieve_memory(query: str, k: int = 8, repo: str | None = None) -> str:
    """Search the long-term RAG memory and return the top-k chunks."""
    return json.dumps(rag_retrieve(query, k=k, repo=repo))

@tool("opencode_apply")
def opencode_apply(repo: str, branch: str, instruction: str) -> str:
    """Run OpenCode runner sidecar on Mac Studio to apply changes & push branch."""
    resp = httpx.post("http://localhost:8002/apply",
                      json={"repo": repo, "branch": branch, "instruction": instruction},
                      timeout=60 * 25)
    resp.raise_for_status()
    return resp.text

@tool("open_pr")
def open_pr(repo: str, branch: str, title: str, body: str, issue_id: str) -> str:
    return open_pr_safely(repo=repo, branch=branch, title=title, body=body, issue_id=issue_id)

def build_crew(p):
    pm = Agent(
        role="Engineering PM", goal="Convert the ticket into a precise spec.",
        llm=llm("planner"), tools=[retrieve_memory], allow_delegation=False,
    )
    arch = Agent(
        role="Staff Engineer", goal="Plan files + tests; minimize risk.",
        llm=llm("planner"), tools=[retrieve_memory],
    )
    coder = Agent(
        role="Senior Software Engineer", goal="Implement via OpenCode.",
        llm=llm("coder"), tools=[opencode_apply, retrieve_memory],
    )
    pr_writer = Agent(
        role="Release Engineer", goal="Open a safe PR with full context.",
        llm=llm("planner"), tools=[open_pr],
    )

    t1 = Task(description=f"Spec ticket {p.issue_id}: {p.title}\n\n{p.body}",
              agent=pm, expected_output="JSON{spec, acceptance_criteria[], risk}")
    t2 = Task(description="Plan minimal diff; cite memory chunks.", agent=arch, context=[t1],
              expected_output="JSON{file_plan[], invariants[], test_plan[]}")
    t3 = Task(description=f"Implement on branch feat/{p.issue_id}. repo={p.repo}",
              agent=coder, context=[t2],
              expected_output="JSON{branch, files_changed[], tests_passed}")
    t4 = Task(description="Open PR linking the Linear issue.", agent=pr_writer, context=[t3],
              expected_output="JSON{pr_url}")
    return Crew(agents=[pm, arch, coder, pr_writer], tasks=[t1, t2, t3, t4],
                process=Process.sequential, verbose=False)
```

---

## 4. Smart model router — production-grade

### 4.1 Mục tiêu

- Route theo **độ phức tạp** thực tế của task (không chỉ keyword).
- 3 tầng: **local-coder** (Ollama qwen2.5-coder:32b) → **local-planner** (llama3.1:70b) → **claude-sonnet** (`claude-sonnet-4-5` / `claude-sonnet-4-6` khi GA).
- Có **budget cap**, **circuit breaker**, **PII redaction**, **prompt cache**, **observability**.

### 4.2 LiteLLM `ai-router/config.yaml`

```yaml
# ai-router/config.yaml
model_list:
  - model_name: local-coder
    litellm_params:
      model: ollama_chat/qwen2.5-coder:32b-instruct-q4_K_M
      api_base: ${OLLAMA_BASE_URL}
      timeout: 120
  - model_name: local-planner
    litellm_params:
      model: ollama_chat/llama3.1:70b-instruct-q4_K_M
      api_base: ${OLLAMA_BASE_URL}
      timeout: 240
  - model_name: claude-sonnet-4-5
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  routing_strategy: simple-shuffle
  fallbacks:
    - { local-coder:   ["local-planner", "claude-sonnet-4-5"] }
    - { local-planner: ["claude-sonnet-4-5"] }
    - { claude-sonnet-4-6: ["claude-sonnet-4-5"] }
  num_retries: 1
  timeout: 300
  cooldown_time: 60
  allowed_fails: 3              # circuit-breaker

litellm_settings:
  cache: true
  cache_params:
    type: redis
    host: redis
    port: 6379
    ttl: 86400
  success_callback: ["prometheus", "langfuse"]
  failure_callback: ["prometheus", "langfuse"]
  callbacks: ["smart_router.RouteByComplexity"]
  drop_params: true
  set_verbose: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  budget_duration: 30d
  max_budget: 100               # USD/tháng — cứng
  alert_to_webhook_url: http://n8n:5678/webhook/budget-alert
```

### 4.3 Pre-call hook — phân loại task

```python
# ai-router/smart_router.py
"""Production smart router: decides among local-coder / local-planner / claude-sonnet-4-5 / claude-sonnet-4-6."""
from __future__ import annotations
import os, re, json, time
from litellm.integrations.custom_logger import CustomLogger
import redis, tiktoken

R = redis.Redis.from_url(os.environ["REDIS_URL"])
ENC = tiktoken.get_encoding("cl100k_base")

HARD_HINTS = re.compile(
    r"\b(architect|design doc|race condition|deadlock|memory leak|"
    r"refactor across|migrate schema|threat model|security audit|"
    r"distributed|consensus|cap theorem)\b", re.I)
TRIVIAL_HINTS = re.compile(
    r"\b(rename|format|lint|fix typo|jsdoc|docstring|commit message|"
    r"add test for|simple bug|null check)\b", re.I)

# Budget knobs (env-overridable, hot-reloadable via Redis)
def _knob(k: str, default):
    v = R.get(f"router:knob:{k}")
    return v.decode() if v else default

def _count_tokens(messages) -> int:
    return sum(len(ENC.encode(m.get("content","") or "")) for m in messages)

def _classify(messages) -> str:
    text = "\n".join(m.get("content","") for m in messages if isinstance(m.get("content"), str))
    n_tokens = _count_tokens(messages)

    if HARD_HINTS.search(text) or n_tokens > 6000:
        return _knob("hard_target", "claude-sonnet-4-5")
    if TRIVIAL_HINTS.search(text) and n_tokens < 800:
        return "local-coder"
    if n_tokens < 1500:
        return "local-coder"
    if n_tokens < 4000:
        return "local-planner"
    return _knob("hard_target", "claude-sonnet-4-5")

class RouteByComplexity(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        force = R.get("router:force")
        if force:                                     # operator override / budget cap
            data["model"] = force.decode()
            return data
        # Caller can request a tag explicitly: model=auto-coder|auto-planner|auto
        requested = (data.get("model") or "").lower()
        if requested in ("auto", "auto-coder", "auto-planner"):
            data["model"] = _classify(data.get("messages", []))
        return data

    async def async_post_call_failure_hook(self, *args, **kwargs):
        R.incr("router:fails:1m"); R.expire("router:fails:1m", 60)

proxy_handler_instance = RouteByComplexity()
```

### 4.4 Budget cap & emergency switch

```mermaid
flowchart LR
    A[LiteLLM spend tracker] -->|>= 80%| B[Webhook /budget-alert]
    B --> N[n8n flow]
    N --> R{Action}
    R -->|warn| TG[Telegram notify]
    R -->|cap| RD[(Redis SET router:force=local-coder)]
    RD --> ROUTER[Router forces local from now]
```

n8n workflow `budget-alert` (rút gọn):

```json
{
  "name": "Budget alert",
  "nodes": [
    { "name": "Webhook", "type": "n8n-nodes-base.webhook",
      "parameters": { "path": "budget-alert", "httpMethod": "POST" } },
    { "name": "Decide", "type": "n8n-nodes-base.if",
      "parameters": { "conditions": { "number": [{
        "value1": "={{$json.percent_of_budget}}", "operation": "largerEqual", "value2": 95 }]}}},
    { "name": "Force local", "type": "n8n-nodes-base.redis",
      "parameters": { "operation": "set", "key": "router:force", "value": "local-coder" } },
    { "name": "Notify", "type": "n8n-nodes-base.httpRequest",
      "parameters": { "url": "http://telegram-bridge:7700/notify",
        "bodyParametersJson": "={\"text\":\"Budget {{$json.percent_of_budget}}% — switched to local-only\"}" } }
  ],
  "connections": {
    "Webhook":      { "main": [[{ "node": "Decide", "type": "main", "index": 0 }]] },
    "Decide":       { "main": [[{ "node": "Force local", "type": "main", "index": 0 }],
                                [{ "node": "Notify", "type": "main", "index": 0 }]] },
    "Force local":  { "main": [[{ "node": "Notify", "type": "main", "index": 0 }]] }
  }
}
```

### 4.5 Cấu hình Claude Code & Cursor

Claude Code (Anthropic-compatible) trỏ thẳng vào AI Router:

```bash
# ~/.zshrc on MacBook Air
export ANTHROPIC_BASE_URL="https://router.devstn.xyz"
export ANTHROPIC_API_KEY="$LITELLM_MASTER_KEY"
```

Cursor: Settings → Models → custom OpenAI base URL `https://router.devstn.xyz`, model `auto`.

Khi caller gửi `model=auto`, hook ở §4.3 sẽ rewrite sang model phù hợp.

---

## 5. Memory / RAG layer — long-term codebase memory

### 5.1 Mục tiêu

- Agent **nhớ** repo, ADR, comment Linear, review trước → giảm hallucination, tránh refactor lặp.
- Một store thống nhất (`memory_chunks`) để mọi agent / mọi node truy vấn.
- Embedding bằng model local (`nomic-embed-text` qua Ollama) → free, ổn định.

### 5.2 Sơ đồ

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        REPO[Git repos]
        LIN[Linear comments<br/>+ issues]
        ADR[ADRs / docs/*.md]
        REV[PR review history]
    end
    REPO --> CHUNK[chunker<br/>tree-sitter / by symbol]
    LIN --> CHUNK
    ADR --> CHUNK
    REV --> CHUNK
    CHUNK --> EMB[Ollama<br/>nomic-embed-text]
    EMB --> PG[(pgvector<br/>memory_chunks)]
    AGENT[CrewAI agents] --> Q[query+filter]
    Q --> PG
    PG --> RR[reranker<br/>cross-encoder local]
    RR --> AGENT
```

### 5.3 Chunking & ingestion

```python
# memory/ingest.py
import os, hashlib, json, psycopg, httpx, pathlib
from tree_sitter_languages import get_parser

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://100.x.x.x:11434")
PG = psycopg.connect(os.environ["DATABASE_URL"])

def embed(text: str) -> list[float]:
    r = httpx.post(f"{OLLAMA}/api/embeddings",
                   json={"model": "nomic-embed-text", "prompt": text}, timeout=60)
    return r.json()["embedding"]

def chunk_code(path: pathlib.Path):
    """Symbol-level chunks: function / class / md heading."""
    text = path.read_text(errors="ignore")
    suffix = path.suffix.lower()
    if suffix in (".md",):
        # split by headings
        cur, name = [], "preamble"
        for line in text.splitlines():
            if line.startswith("#"):
                if cur: yield (name, "\n".join(cur))
                name, cur = line.strip("# ").strip(), [line]
            else:
                cur.append(line)
        if cur: yield (name, "\n".join(cur))
        return
    lang = {".py":"python", ".ts":"typescript", ".tsx":"typescript", ".js":"javascript",
            ".go":"go", ".rs":"rust", ".sh":"bash"}.get(suffix)
    if not lang:
        # fall back to 80-line windows
        lines = text.splitlines()
        for i in range(0, len(lines), 80):
            yield (f"chunk_{i}", "\n".join(lines[i:i+80]))
        return
    parser = get_parser(lang)
    tree = parser.parse(text.encode())
    for node in tree.root_node.children:
        if node.type in ("function_definition","class_definition","method_definition","function_declaration"):
            sym = next((c.text.decode() for c in node.children if c.type in ("identifier","name")), "anon")
            yield (sym, text[node.start_byte:node.end_byte])

def upsert(repo: str, path: str, sym: str, content: str, source_type="code"):
    sid = hashlib.sha1(f"{repo}:{path}:{sym}".encode()).hexdigest()
    emb = embed(content[:4000])
    with PG.cursor() as c:
        c.execute("""
            INSERT INTO memory_chunks (source_type, source_ref, repo, language, content, metadata, embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (source_ref) DO UPDATE
            SET content=EXCLUDED.content, embedding=EXCLUDED.embedding, updated_at=now()
        """, (source_type, f"{repo}:{path}#{sym}", repo,
              pathlib.Path(path).suffix.lstrip("."), content,
              json.dumps({"sym": sym}), emb))
    PG.commit()
```

> Chạy ingest qua n8n cron `0 */6 * * *` hoặc trigger từ GitHub `push` webhook để diff-only update.

### 5.4 Retrieve API (`memory/retrieve.py`)

```python
import os, psycopg, httpx
PG = psycopg.connect(os.environ["DATABASE_URL"])
OLLAMA = os.environ["OLLAMA_BASE_URL"]

def retrieve(query: str, k: int = 8, repo: str | None = None,
             source_types=("code","adr","linear","pr_review")) -> list[dict]:
    emb = httpx.post(f"{OLLAMA}/api/embeddings",
                     json={"model":"nomic-embed-text","prompt":query}).json()["embedding"]
    with PG.cursor() as c:
        c.execute("""
          SELECT source_ref, source_type, content, metadata,
                 1 - (embedding <=> %s::vector) AS score
          FROM memory_chunks
          WHERE source_type = ANY(%s) AND (%s IS NULL OR repo = %s)
          ORDER BY embedding <=> %s::vector
          LIMIT %s
        """, (emb, list(source_types), repo, repo, emb, k * 4))
        rows = c.fetchall()
    # cheap MMR-style dedupe by source_ref prefix
    seen, out = set(), []
    for ref, st, content, md, score in rows:
        key = ref.split("#")[0]
        if key in seen: continue
        seen.add(key)
        out.append({"ref": ref, "type": st, "score": score, "snippet": content[:1200]})
        if len(out) >= k: break
    return out
```

### 5.5 Episodic memory (agent học từ chính kết quả)

Sau mỗi PR merged hoặc revert, ghi 1 chunk `source_type='episodic'`:

```python
def record_episode(issue_id: str, pr_url: str, outcome: str, lessons: str):
    upsert(repo="meta", path=f"episodes/{issue_id}.md",
           sym="lesson", source_type="episodic",
           content=f"# {issue_id} — {outcome}\nPR: {pr_url}\n\n{lessons}")
```

Khi agent bắt đầu ticket mới, `retrieve_memory` sẽ pull cả episodic gần nhất → tránh lặp lại lỗi.

---

## 6. Autonomous Git workflow + safety guardrails + auto-rollback

### 6.1 Nguyên tắc

- **Branch isolation:** mỗi ticket → branch `feat/<LINEAR_ID>-<slug>`. Cấm push lên `main`.
- **Path allowlist:** mỗi repo có file `.devstation/policy.yaml` quy định path cho phép sửa, lệnh test bắt buộc, owner mặc định, paths chống đụng (`PROTECTED_PATHS`).
- **Pre-push gates:** secret scan, lint, type-check, unit test; fail → không push.
- **AI Gate** (xem §7) chạy trên PR; nếu `risk_score > threshold` → đặt label `needs-human` và mention reviewer.
- **Auto-rollback:** sau merge, nếu CI prod fail trong N phút → tự `gh pr revert` và mở revert PR; agent học vào episodic memory.

### 6.2 Sơ đồ

```mermaid
flowchart TD
    A[CrewAI coder calls opencode_apply] --> B[Open workspace<br/>checkout branch]
    B --> C[Apply edits<br/>OpenCode]
    C --> D{Path allowlist?}
    D -- no --> X1[Reject: out-of-scope path]
    D -- yes --> E[Run lint/type/tests]
    E -- fail --> F{auto-fix loop<br/>≤ N times}
    F -- give up --> X2[Mark ticket failed<br/>comment Linear]
    F -- still fail --> E
    E -- pass --> G[Secret scan<br/>trufflehog]
    G -- leak --> X3[Abort + alert]
    G -- clean --> H[Commit + push]
    H --> I[open_pr_safely<br/>via GitHub API]
    I --> J[Trigger AI Gate]
    J --> K{verdict}
    K -- PASS --> L[Label: ready-review<br/>Tag CODEOWNERS]
    K -- FAIL critical --> M[Label: needs-human<br/>Comment with risk]
    L --> N[Merge by human or auto-merge if labeled auto-merge]
    N --> O[Watch prod CI<br/>15 min window]
    O -- fail --> P[Auto revert PR<br/>+ episodic memory]
    O -- ok --> Q[Done]
```

### 6.3 `.devstation/policy.yaml` (per repo)

```yaml
version: 1
default_branch: main
allowed_paths:
  - "src/**"
  - "tests/**"
  - "docs/**"
  - "package.json"
  - "pnpm-lock.yaml"
protected_paths:
  - ".github/workflows/**"
  - "infra/**"
  - "**/*.env*"
  - "**/secrets/**"
  - "Dockerfile*"
required_checks:
  - "pnpm typecheck"
  - "pnpm lint"
  - "pnpm test -- --run"
secret_scan: true
auto_merge_label: "auto-merge"
codeowners_must_review: true
rollback:
  enabled: true
  window_minutes: 15
  signals:
    - "prod-smoke"
    - "prod-e2e"
```

### 6.4 `gitops.py` — `open_pr_safely` & `auto_revert`

```python
# gitops.py
import os, subprocess, yaml, fnmatch, json, httpx
from pathlib import Path

GH = os.environ["GITHUB_TOKEN"]
GH_API = "https://api.github.com"

def _policy(repo_dir: str) -> dict:
    p = Path(repo_dir) / ".devstation/policy.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}

def _changed_files(repo_dir: str, base: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", repo_dir, "diff", "--name-only", f"{base}...HEAD"], text=True)
    return [x for x in out.splitlines() if x]

def _match_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)

def enforce_policy(repo_dir: str, base: str = "origin/main"):
    pol = _policy(repo_dir)
    if not pol: return
    files = _changed_files(repo_dir, base)
    bad = [f for f in files if _match_any(f, pol.get("protected_paths", []))]
    if bad:
        raise RuntimeError(f"protected paths touched: {bad}")
    allowed = pol.get("allowed_paths") or ["**"]
    out = [f for f in files if not _match_any(f, allowed)]
    if out:
        raise RuntimeError(f"out-of-scope paths: {out}")

def run_required_checks(repo_dir: str):
    for cmd in _policy(repo_dir).get("required_checks", []):
        subprocess.check_call(cmd, cwd=repo_dir, shell=True)

def secret_scan(repo_dir: str):
    if not _policy(repo_dir).get("secret_scan", True): return
    subprocess.check_call(["trufflehog", "git", "file://" + repo_dir,
                           "--since-commit", "origin/main", "--fail", "--no-update"], cwd=repo_dir)

def open_pr_safely(repo: str, branch: str, title: str, body: str, issue_id: str) -> str:
    repo_dir = f"/Volumes/work/{repo.split('/')[-1]}"
    enforce_policy(repo_dir)
    run_required_checks(repo_dir)
    secret_scan(repo_dir)
    subprocess.check_call(["git", "-C", repo_dir, "push", "-u", "origin", branch])
    payload = {"title": title, "head": branch, "base": "main",
               "body": body + f"\n\nRefs {issue_id}\nGenerated by AI Dev Station — see AI Gate report below."}
    h = {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github+json"}
    r = httpx.post(f"{GH_API}/repos/{repo}/pulls", headers=h, json=payload, timeout=60)
    r.raise_for_status()
    pr = r.json()
    return pr["html_url"]

def auto_revert_if_prod_fails(repo: str, pr_number: int):
    """Called by n8n when prod CI fails within rollback window."""
    h = {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github+json"}
    httpx.post(f"{GH_API}/repos/{repo}/pulls/{pr_number}/comments",
               headers=h, json={"body": "Prod signals failing — auto-reverting via revert PR."})
    # GitHub revert: open a PR with the inverse merge commit
    subprocess.check_call(["gh", "pr", "revert", str(pr_number), "--label", "auto-revert", "-R", repo])
```

### 6.5 n8n watcher cho rollback

```mermaid
sequenceDiagram
    participant GH as GitHub Actions
    participant N as n8n
    participant GO as gitops auto_revert
    GH->>N: workflow_run completed (prod-e2e, conclusion=failure)
    N->>N: lookup merged PR within 15m window
    alt PR is from feat/* and merged < 15m
        N->>GO: POST /revert {repo, pr_number}
        GO->>GH: gh pr revert + label auto-revert
        N->>N: write episodic memory chunk
        N->>TG: notify "Auto-reverted PR #X due to prod-e2e failure"
    else
        N->>N: noop
    end
```

---

## 7. AI Gate — LLM reviewer + autofix runner

### 7.1 Vai trò

- Chạy ngay sau khi CrewAI mở PR.
- Đọc **diff** + **file context có liên quan** (qua RAG) → xuất report JSON.
- Tự sửa các issue `severity ∈ {minor, major}` qua coder model (auto-fix loop, có circuit breaker).
- Chỉ tag human khi còn `critical` hoặc `risk_score >= RISK_HUMAN_THRESHOLD`.
- Mọi run được lưu vào `gate_runs` (audit) + posted dưới dạng PR comment.

### 7.2 Sơ đồ autofix loop

```mermaid
flowchart TD
    S[PR opened] --> R0[Fetch diff + context]
    R0 --> RV[Run reviewer prompts<br/>parallel templates]
    RV --> AGG[Aggregate JSON reports]
    AGG --> D{any issue?}
    D -- no --> POST[Post PASS comment<br/>label ready-review]
    D -- yes --> CRIT{has critical?<br/>or risk>=T?}
    CRIT -- yes --> H[Label needs-human<br/>tag CODEOWNERS<br/>stop]
    CRIT -- no --> FX[Autofix runner<br/>severity ≤ major]
    FX --> TST[Run required_checks]
    TST -- fail --> ITER{iter < MAX?}
    TST -- pass --> RV2[Re-run reviewer]
    RV2 --> D
    ITER -- yes --> FX
    ITER -- no --> H
```

### 7.3 Review prompt templates

Mỗi template chạy bằng model `local-planner` mặc định; fallback `claude-sonnet-4-5` khi `risk_score` của lần trước cao.

#### 7.3.1 `reviewer.staff.md` — Senior Staff Engineer (template chính)

```
You are a senior staff engineer reviewer.

Review ONLY the git diff.

Check for:

1. architectural violations
2. hidden side effects
3. race conditions
4. memory leaks
5. TypeScript anti-patterns
6. security issues
7. unnecessary complexity
8. duplicated logic
9. regression risk
10. API contract violations

Return JSON:

{
  "status": "PASS|FAIL",
  "risk_score": 0-100,
  "issues": [
    {
      "severity": "critical|major|minor",
      "file": "",
      "problem": "",
      "fix": ""
    }
  ],
  "summary": ""
}
```

#### 7.3.2 `reviewer.security.md`

```
You are a security-focused reviewer. Examine ONLY the git diff for:
- injection vectors (SQL, command, path traversal, template, LDAP, XPath)
- authn/authz bypass, IDOR, privilege escalation
- secret/credential leakage (incl. via logs, error messages, telemetry)
- unsafe deserialization, prototype pollution
- SSRF / open-redirect, weak CSRF defenses
- insecure crypto (algorithm, IV, key length, RNG)
- dependency CVEs introduced via lockfile changes
- supply-chain risks (post-install scripts, tarball URLs)

Return EXACTLY this JSON shape:

{
  "status": "PASS|FAIL",
  "risk_score": 0-100,
  "issues": [
    { "severity": "critical|major|minor", "file": "", "problem": "", "fix": "", "cwe": "" }
  ],
  "summary": ""
}

Mark `status=FAIL` if ANY critical issue exists. Be terse; do not echo the diff.
```

#### 7.3.3 `reviewer.perf_concurrency.md`

```
You are a performance & concurrency reviewer. Read ONLY the git diff. Detect:
- N+1 queries, unbounded loops, accidental O(n^2)
- blocking calls in async/event-loop contexts
- shared mutable state without synchronization
- race conditions, TOCTOU, lost updates
- memory leaks: closures retaining large scope, unsubscribed listeners, growing caches
- backpressure missing in streams

Return the standard JSON schema (status, risk_score, issues[], summary). Severity must reflect production impact, not stylistic preference.
```

#### 7.3.4 `reviewer.api_contract.md`

```
You are an API-contract reviewer. Diff the OpenAPI/GraphQL/TypeScript types implied by the change.
Flag any of:
- breaking change in request/response shape
- removed/renamed fields without deprecation
- HTTP status semantics changed
- error envelope drift
- pagination/filter semantics altered
- enum value removed

Return the standard JSON schema. Set risk_score >= 70 for any breaking change without `breaking-change` label on the PR.
```

#### 7.3.5 `reviewer.test_coverage.md`

```
You are a test-coverage gatekeeper. For the diff:
- list functions/branches that lack a corresponding test
- detect tests that only assert "no throw"
- detect snapshot abuse hiding logic regressions
Return the standard JSON schema. Severity:
- critical: business-critical path with zero tests
- major: complex new function with no unit test
- minor: missing edge-case
```

### 7.4 Autofix prompt template

```
You are a senior software engineer. You are given:
- the failing review issue (severity, file, problem, suggested fix)
- the current file content
- repo conventions (lint, formatter, framework)

Apply the MINIMUM diff required to resolve the issue.
- Do NOT touch unrelated code.
- Do NOT introduce new dependencies unless explicitly required by the fix.
- Preserve public API surface.
- Keep style consistent with the surrounding file.

Return ONLY a unified diff (git format), no prose, no fences.
```

### 7.5 Autofix runner — `ai-gate/runner.py`

```python
# ai-gate/runner.py — service consumed by n8n via webhook
import os, json, subprocess, tempfile, pathlib, httpx, redis, psycopg
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
ROUTER  = os.environ["ROUTER_URL"]
KEY     = os.environ["ROUTER_KEY"]
PG      = psycopg.connect(os.environ["DATABASE_URL"])
R       = redis.Redis.from_url(os.environ["REDIS_URL"])
MAX_IT  = int(os.environ.get("MAX_AUTOFIX_ITERATIONS", "3"))
T_HUMAN = int(os.environ.get("RISK_HUMAN_THRESHOLD", "60"))

REVIEWERS = ["staff", "security", "perf_concurrency", "api_contract", "test_coverage"]

def _llm(prompt: str, model: str = "auto") -> str:
    r = httpx.post(f"{ROUTER}/v1/chat/completions",
                   headers={"Authorization": f"Bearer {KEY}"},
                   json={"model": model, "messages":[{"role":"user","content":prompt}],
                         "response_format":{"type":"json_object"}}, timeout=240)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _load_template(name: str) -> str:
    return pathlib.Path(f"templates/reviewer.{name}.md").read_text()

def _diff(repo_dir: str, base: str = "origin/main") -> str:
    return subprocess.check_output(["git","-C",repo_dir,"diff",f"{base}...HEAD"], text=True)

def _review(repo_dir: str) -> list[dict]:
    diff = _diff(repo_dir)
    reports = []
    for name in REVIEWERS:
        prompt = _load_template(name) + "\n\n--- DIFF ---\n" + diff
        try:
            data = json.loads(_llm(prompt, model="local-planner"))
            data["_template"] = name
            reports.append(data)
        except Exception as e:
            reports.append({"_template": name, "status": "FAIL",
                            "risk_score": 0, "issues": [],
                            "summary": f"reviewer error: {e}"})
    return reports

def _aggregate(reports: list[dict]) -> dict:
    issues = []
    for rep in reports:
        for iss in rep.get("issues", []):
            iss["_template"] = rep["_template"]
            issues.append(iss)
    risk = max((r.get("risk_score", 0) for r in reports), default=0)
    has_critical = any(i.get("severity") == "critical" for i in issues)
    status = "FAIL" if has_critical or risk >= T_HUMAN else ("PASS" if not issues else "FIX")
    return {"status": status, "risk_score": risk, "issues": issues}

def _autofix(repo_dir: str, issue: dict) -> bool:
    """Apply a minimal diff for one issue. Return True if applied & tests pass."""
    if issue.get("severity") == "critical": return False
    file = issue["file"]
    if not file: return False
    content = pathlib.Path(repo_dir, file).read_text()
    prompt = _load_template("autofix")  # reviewer.autofix.md (template §7.4)
    prompt += f"\n\nIssue:\n{json.dumps(issue, indent=2)}\n\n--- FILE: {file} ---\n{content}"
    diff = _llm(prompt, model="local-coder")
    diff = diff.strip().strip("`")
    if not diff.startswith("diff --git"): return False
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(diff); patch_path = f.name
    rc = subprocess.call(["git","-C",repo_dir,"apply","--3way",patch_path])
    if rc != 0: return False
    # run required checks
    from gitops import run_required_checks
    try: run_required_checks(repo_dir)
    except subprocess.CalledProcessError:
        subprocess.call(["git","-C",repo_dir,"reset","--hard","HEAD"])
        return False
    subprocess.check_call(["git","-C",repo_dir,"add","-A"])
    subprocess.check_call(["git","-C",repo_dir,"commit","-m",
                           f"fixup: {issue['_template']} — {issue['problem'][:60]}"])
    return True

class GateReq(BaseModel):
    repo: str
    pr_number: int
    repo_dir: str

@app.post("/gate")
def gate(req: GateReq):
    for it in range(1, MAX_IT + 1):
        reports = _review(req.repo_dir)
        agg = _aggregate(reports)
        _audit(req.pr_number, it, reports, agg)
        if agg["status"] == "PASS":
            _comment_pr(req.repo, req.pr_number, _render_pass(agg))
            _label(req.repo, req.pr_number, "ready-review")
            return {"status":"PASS","iter":it}
        if agg["status"] == "FAIL":
            _comment_pr(req.repo, req.pr_number, _render_human(agg))
            _label(req.repo, req.pr_number, "needs-human")
            return {"status":"NEEDS_HUMAN","iter":it,"risk":agg["risk_score"]}
        # status == "FIX" → try autofix
        fixed = 0
        for iss in sorted(agg["issues"],
                          key=lambda i: {"major":0,"minor":1,"critical":2}[i["severity"]]):
            if iss["severity"] == "critical": continue
            if _autofix(req.repo_dir, iss): fixed += 1
        if fixed == 0:
            _comment_pr(req.repo, req.pr_number, _render_human(agg))
            _label(req.repo, req.pr_number, "needs-human")
            return {"status":"NEEDS_HUMAN","iter":it,"reason":"autofix exhausted"}
        # next iteration: re-review
    _comment_pr(req.repo, req.pr_number, "AI Gate hit max autofix iterations.")
    _label(req.repo, req.pr_number, "needs-human")
    return {"status":"NEEDS_HUMAN","iter":MAX_IT,"reason":"max iterations"}

def _audit(pr, it, reports, agg):
    with PG.cursor() as c:
        for rep in reports:
            c.execute("""INSERT INTO gate_runs(pr_number,iteration,reviewer,model,risk_score,status,issues_json,fixed_count)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (pr, it, rep["_template"], "local-planner",
                       rep.get("risk_score"), rep.get("status","FAIL"),
                       json.dumps(rep.get("issues",[])), 0))
    PG.commit()

def _comment_pr(repo, n, body):
    httpx.post(f"https://api.github.com/repos/{repo}/issues/{n}/comments",
               headers={"Authorization":f"Bearer {os.environ['GITHUB_TOKEN']}"},
               json={"body": body})

def _label(repo, n, label):
    httpx.post(f"https://api.github.com/repos/{repo}/issues/{n}/labels",
               headers={"Authorization":f"Bearer {os.environ['GITHUB_TOKEN']}"},
               json={"labels":[label]})

def _render_pass(agg):
    return f"### AI Gate: PASS\nRisk score: **{agg['risk_score']}**. No blocking issues."

def _render_human(agg):
    lines = [f"### AI Gate: needs human review (risk={agg['risk_score']})", ""]
    for i in agg["issues"]:
        lines.append(f"- **{i['severity'].upper()}** `{i.get('file','-')}` _{i.get('_template','')}_: {i['problem']}")
        if i.get("fix"): lines.append(f"  - Suggested fix: {i['fix']}")
    return "\n".join(lines)
```

### 7.6 Tích hợp với CrewAI / n8n

- CrewAI `pr_writer` agent kết thúc bằng cách POST `http://ai-gate:8003/gate` với `repo, pr_number, repo_dir`.
- n8n có flow `pr-opened` (GitHub webhook) là **fallback** — nếu PR mở không qua CrewAI (vd dev tay) vẫn được Gate review.
- Telegram bridge subscribe Redis `pubsub gate:events` → push notification kèm risk_score và link PR.

### 7.7 Kill-switch & circuit breaker

- `redis SET gate:disabled 1` → runner skip; dùng khi LLM provider lỗi diện rộng.
- Nếu cùng PR có `gate_runs.iteration > MAX_IT` × 2 trong 24h → label `gate-loop` và stop để tránh đốt token.

---

## 8. Telegram bridge (vai trò ops, không liên quan health)

OpenClaw/Telegram bridge giờ chỉ làm **cổng điều khiển**:

| Lệnh | Hành động |
|---|---|
| `/status` | Đọc Redis: queue length, last PR, last gate verdict |
| `/queue` | List 10 ticket trong stream `tickets` |
| `/approve <pr>` | Add label `auto-merge` qua GitHub API |
| `/pause` | `SET pipeline:paused 1` (CrewAI consumer skip) |
| `/resume` | `DEL pipeline:paused` |
| `/budget` | LiteLLM `/spend` snapshot |
| `/force_local` | `SET router:force local-coder` (tạm) |
| `/clear_force` | `DEL router:force` |

Notifications subscribe Redis pubsub: `pipeline:events`, `gate:events`, `budget:events`, `rollback:events`.

---

## 9. Hardening & vận hành

| Khía cạnh | Khuyến nghị |
|---|---|
| **Bảo mật** | Mọi service Docker bind vào network `internal`, chỉ Traefik expose `443`. mTLS Tailscale giữa Mini và Studio. Webhook verify HMAC + idempotency table. |
| **Secrets** | Một `.env` duy nhất (`chmod 600`), không commit. Cân nhắc dùng Doppler/1Password Connect khi scale. |
| **Quan sát** | Prometheus scrape Traefik, n8n, LiteLLM, ai-gate, postgres-exporter, redis-exporter, node-exporter. Loki gom toàn bộ stdout. Grafana dashboards: token spend, p95 latency, gate pass-rate, autofix iterations, rollback count. |
| **Tự phục hồi** | `restart: unless-stopped` + healthcheck. Watchdog n8n (cron 5') ping `/healthz` toàn stack, alert Telegram. |
| **Chi phí** | LiteLLM `max_budget=100` USD/30d hard cap. Khi > 95% → ép `router:force=local-coder`. |
| **Privacy** | Repo `private=true` & `policy:level<=1` không gửi nội dung sang Anthropic; route luôn về local + redaction layer. |
| **Backup** | Postgres dump nightly → S3-compatible (MinIO local). Workspaces là git → không cần backup riêng. |

---

## 10. Tóm tắt env vars

Tham khảo §2.6 cho layout `.env.shared` + `.env.<node>`. `bin/devstation.sh` sẽ tự gộp.

---

## 11. Observability dashboard — đơn giản, dễ tự sửa

### 11.1 Triết lý thiết kế

- **3 service, không hơn:** Prometheus (metrics) + Loki (logs) + Grafana (UI). Đã có sẵn trong `compose/mini.yml` profile `monitoring`.
- **1 dashboard chính** `DevStation Overview` provisioned tự động (file JSON commit trong repo), không cần click chuột tạo bằng tay.
- **8 panel cốt lõi**, mỗi panel kèm 1 dòng chú thích _"Cái này cho biết gì? Khi nào báo động?"_ ngay trên panel.
- **Exporter tối thiểu:** `cadvisor`, `node-exporter`, `postgres-exporter`, `redis-exporter`, `blackbox-exporter`. Không thêm Tempo/Mimir/Pyroscope.
- **Mở rộng dễ:** thêm panel = copy-paste 5 dòng JSON, không cần học PromQL nâng cao.

### 11.2 Sơ đồ

```mermaid
flowchart LR
    subgraph SRC[Sources]
        T[Traefik /metrics]
        N[n8n /metrics]
        R[ai-router /metrics]
        G[ai-gate /metrics]
        CA[cadvisor]
        NE[node-exporter]
        PE[postgres-exporter]
        RE[redis-exporter]
        BB[blackbox-exporter]
    end
    SRC --> P[Prometheus]
    DOCKER[Docker daemon] -- loki driver --> L[Loki]
    P --> GF[Grafana]
    L --> GF
    PG[(Postgres)] --> GF
    GF --> USER[Operator browser<br/>or iPhone PWA]
```

Grafana dùng 3 datasource: **Prometheus**, **Loki**, **Postgres** (để đọc trực tiếp `task_ledger` + `gate_runs` cho 2 panel cuối).

### 11.3 `prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 30s

scrape_configs:
  - job_name: traefik
    static_configs: [{ targets: [traefik:8080] }]
  - job_name: n8n
    metrics_path: /metrics
    static_configs: [{ targets: [n8n:5678] }]
  - job_name: ai-router
    static_configs: [{ targets: [ai-router:4000] }]
  - job_name: ai-gate
    static_configs: [{ targets: [ai-gate:8003] }]
  - job_name: cadvisor
    static_configs: [{ targets: [cadvisor:8080] }]
  - job_name: node-mini
    static_configs: [{ targets: [node-exporter:9100] }]
  - job_name: node-studio
    static_configs: [{ targets: ['${TS_STUDIO_IP}:9100'] }]
  - job_name: postgres
    static_configs: [{ targets: [postgres-exporter:9187] }]
  - job_name: redis
    static_configs: [{ targets: [redis-exporter:9121] }]
  - job_name: blackbox-uptime
    metrics_path: /probe
    params: { module: [http_2xx] }
    static_configs:
      - targets:
          - https://n8n.devstn.xyz/healthz
          - https://router.devstn.xyz/health
          - https://app.devstn.xyz/
          - http://${TS_STUDIO_IP}:8001/healthz
          - http://${TS_STUDIO_IP}:11434/api/tags
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115

rule_files:
  - rules/*.yml
```

### 11.4 Provisioning Grafana (auto-load datasource + dashboard)

```yaml
# grafana/provisioning/datasources/datasources.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
  - name: Loki
    type: loki
    url: http://loki:3100
  - name: DevStation DB
    type: postgres
    url: postgres:5432
    database: devstation
    user: devstation
    secureJsonData: { password: ${POSTGRES_PASSWORD} }
    jsonData: { sslmode: disable }
```

```yaml
# grafana/provisioning/dashboards/dashboards.yml
apiVersion: 1
providers:
  - name: devstation
    folder: DevStation
    type: file
    options: { path: /var/lib/grafana/dashboards }
```

### 11.5 Dashboard `DevStation Overview` — 8 panel cốt lõi

| # | Panel | Datasource | Query (rút gọn) | Đọc thế nào |
|---|---|---|---|---|
| 1 | **Containers up/down** | Prometheus | `sum by (name) (container_last_seen{name!=""})` | Đỏ = container chết. Báo động khi bất kỳ ô nào lỗi > 1'. |
| 2 | **Queue depth (tickets)** | Prometheus | `redis_stream_length{stream="tickets"}` | Tăng đột biến = consumer (CrewAI) đứng. |
| 3 | **Ticket throughput** | Postgres | `SELECT date_trunc('hour', updated_at), state, count(*) FROM task_ledger GROUP BY 1,2` | Cột merged cao = pipeline khoẻ. |
| 4 | **Token spend (USD/ngày theo model)** | Prometheus | `sum by (model) (increase(litellm_spend_usd[1d]))` | Cột claude-sonnet vượt ngân sách → báo động. |
| 5 | **AI Gate pass-rate** | Postgres | `SELECT date_trunc('day', created_at), status, count(*) FROM gate_runs GROUP BY 1,2` | PASS/FAIL ratio < 60% → review prompt cần chỉnh. |
| 6 | **Autofix iterations (avg)** | Postgres | `SELECT date_trunc('day', created_at), avg(iteration) FROM gate_runs GROUP BY 1` | Trung bình > 2 = autofix loop yếu. |
| 7 | **p95 latency CrewAI/Router** | Prometheus | `histogram_quantile(0.95, sum by (le, job) (rate(http_request_duration_seconds_bucket[5m])))` | > 60s sustained = LLM chậm. |
| 8 | **Error logs (live)** | Loki | `{node=~".+"} |~ "(?i)error|panic|fatal"` | Stream lỗi cuối cùng, click drill xuống container. |

File JSON dashboard (rút gọn — full JSON nằm trong `grafana/dashboards/devstation.json`):

```json
{
  "title": "DevStation Overview",
  "refresh": "30s",
  "timezone": "",
  "panels": [
    {
      "id": 1, "type": "stat", "title": "Containers up/down",
      "description": "Số container chạy theo node. Đỏ = có container chết — kiểm tra Loki panel #8.",
      "datasource": "Prometheus",
      "targets": [{ "expr": "count by (instance) (container_last_seen{name!=\"\"} > (time()-60))" }]
    },
    {
      "id": 2, "type": "timeseries", "title": "Queue depth (tickets)",
      "description": "Số ticket đang chờ. Tăng đột biến = CrewAI consumer ngưng. Bình thường: 0–3.",
      "datasource": "Prometheus",
      "targets": [{ "expr": "redis_stream_length{stream=\"tickets\"}" }]
    },
    {
      "id": 3, "type": "barchart", "title": "Ticket throughput by state",
      "description": "merged cao = pipeline khoẻ. failed/reverted tăng = cần xem AI Gate.",
      "datasource": "DevStation DB",
      "targets": [{ "format": "time_series",
        "rawSql": "SELECT date_trunc('hour', updated_at) AS time, state AS metric, count(*) AS value FROM task_ledger WHERE $__timeFilter(updated_at) GROUP BY 1,2 ORDER BY 1" }]
    },
    {
      "id": 4, "type": "barchart", "title": "Token spend USD/day by model",
      "description": "Cột claude-sonnet vượt 80% ngân sách → router tự fall-back local-coder.",
      "datasource": "Prometheus",
      "targets": [{ "expr": "sum by (model) (increase(litellm_spend_usd_total[1d]))" }]
    },
    {
      "id": 5, "type": "piechart", "title": "AI Gate pass-rate (7d)",
      "description": "PASS/FAIL ratio. < 60% PASS → prompt template nên được tinh chỉnh.",
      "datasource": "DevStation DB",
      "targets": [{ "format": "table",
        "rawSql": "SELECT status, count(*) AS value FROM gate_runs WHERE created_at > now() - interval '7 days' GROUP BY status" }]
    },
    {
      "id": 6, "type": "stat", "title": "Avg autofix iterations",
      "description": "Trung bình lần lặp autofix mỗi PR. > 2 = autofix yếu, cân nhắc dùng claude-sonnet cho coder.",
      "datasource": "DevStation DB",
      "targets": [{ "format": "table",
        "rawSql": "SELECT avg(iteration)::float AS value FROM gate_runs WHERE created_at > now() - interval '1 day'" }]
    },
    {
      "id": 7, "type": "timeseries", "title": "p95 latency (CrewAI / Router)",
      "description": "p95 thời gian trả lời. > 60s sustained = LLM chậm hoặc model đang swap.",
      "datasource": "Prometheus",
      "targets": [{ "expr": "histogram_quantile(0.95, sum by (le, job) (rate(http_request_duration_seconds_bucket{job=~\"crewai|ai-router\"}[5m])))" }]
    },
    {
      "id": 8, "type": "logs", "title": "Errors (live)",
      "description": "Stream lỗi gần nhất. Click container_name để drill xuống node.",
      "datasource": "Loki",
      "targets": [{ "expr": "{node=~\".+\"} |~ \"(?i)error|panic|fatal\"" }]
    }
  ]
}
```

### 11.6 Alert rules (nhỏ gọn, đủ dùng)

```yaml
# prometheus/rules/devstation.yml
groups:
- name: devstation-basic
  interval: 1m
  rules:
  - alert: ContainerDown
    expr: time() - container_last_seen{name!=""} > 90
    for: 2m
    labels: { severity: critical }
    annotations:
      summary: "Container {{ $labels.name }} down"
  - alert: TicketQueueBacklog
    expr: redis_stream_length{stream="tickets"} > 10
    for: 5m
    labels: { severity: warning }
  - alert: BudgetNearCap
    expr: litellm_budget_spend_usd / litellm_budget_max_usd > 0.8
    for: 1m
    labels: { severity: warning }
  - alert: GateFailRateHigh
    expr: |
      (sum(rate(gate_runs_status_total{status="FAIL"}[1h])))
        / clamp_min(sum(rate(gate_runs_status_total[1h])), 1) > 0.4
    for: 30m
    labels: { severity: warning }
  - alert: ProbeDown
    expr: probe_success == 0
    for: 3m
    labels: { severity: critical }
```

Alert đẩy về Telegram qua webhook n8n `alert-router` → `telegram-bridge.notify` (cùng pattern §4.4).

### 11.7 Cách thêm panel mới (cho người mới)

1. Mở dashboard, click **Add panel**.
2. Chọn datasource (Prometheus / Loki / DevStation DB).
3. Dán query (xem bảng §11.5 làm mẫu).
4. Đặt tên + 1 dòng `description` _"Cái này cho biết gì? Khi nào báo động?"_.
5. **Save dashboard** → click **JSON Model** → copy → ghi đè `grafana/dashboards/devstation.json` trong repo → commit.

Quy ước: panel nào chưa có mô tả thì coi như chưa hoàn thành (PR review reject).

---

## 12. iPhone 15 — review/control trên di động

### 12.1 3 mức truy cập

| Tier | Công cụ | Khi nào dùng | Setup |
|---|---|---|---|
| **A. Quick action** | Apple Shortcuts + Siri | Hỏi nhanh, ra lệnh giọng nói | 5 phút, không cần code |
| **B. Telegram Mini App** | bot DevStation + WebApp button | Daily driver: xem queue, approve, comment, đọc gate report | Đã có Telegram bot |
| **C. Standalone PWA** | `app.devstn.xyz` đăng nhập Linear OAuth | Review code dài, đọc diff đầy đủ | PWA tự host trong `iphone-pwa` container |

### 12.2 Sơ đồ

```mermaid
flowchart LR
    PHONE[iPhone 15] -->|Shortcuts → HTTP| API[api.devstn.xyz<br/>n8n webhook]
    PHONE -->|Telegram app| TG[Telegram cloud]
    TG --> BR[telegram-bridge :7700]
    BR -->|MiniApp WebView| MA[app.devstn.xyz/mini]
    PHONE -->|Safari PWA| PWA[app.devstn.xyz]
    MA --> API
    PWA --> API
    API --> DB[(Postgres task_ledger)]
    API --> GH[GitHub API]
    API --> LIN[Linear API]
```

### 12.3 Apple Shortcuts (Tier A)

Mỗi shortcut là 1 HTTP request đến `api.devstn.xyz` (n8n webhook + token cá nhân). Ví dụ:

```
Name: DevStation queue
When Run: Get Contents of URL https://api.devstn.xyz/webhook/queue
         Headers: Authorization = Bearer ${PHONE_TOKEN}
         Response: JSON
         Show {{queue_size}} tickets, {{running}} running, top: {{top.title}}
```

```
Name: Approve PR
When Run: Ask for input "PR number"
         Get URL https://api.devstn.xyz/webhook/approve?pr={{input}}
         Method: POST
         Show Result.message
```

```
Name: Pause pipeline
Get URL https://api.devstn.xyz/webhook/pause   (POST)
Speak "Pipeline paused."
```

```
Name: Resume pipeline
Get URL https://api.devstn.xyz/webhook/resume  (POST)
```

Thêm vào Siri: _"Hey Siri, DevStation queue."_

n8n endpoints này verify HMAC token + đọc `task_ledger` / set Redis key tương ứng.

### 12.4 Telegram Mini App (Tier B)

Telegram cho phép bot embed 1 WebApp (URL HTTPS) chạy trong client. Strict-mobile, không cần App Store.

```mermaid
sequenceDiagram
    participant U as iPhone Telegram
    participant B as @DevStationBot
    participant W as MiniApp WebView (app.devstn.xyz/mini)
    participant A as api.devstn.xyz (n8n)
    U->>B: /app
    B-->>U: "Open DevStation" inline button
    U->>W: launch WebApp
    W->>W: read Telegram.WebApp.initData (signed by bot token)
    W->>A: GET /tasks (with initData header)
    A->>A: verify HMAC of initData (Telegram doc)
    A-->>W: list tasks + last gate report
    U->>W: tap "Approve PR"
    W->>A: POST /approve {pr}
    A->>GH: add label auto-merge
    A-->>W: ok
    W->>B: WebApp.sendData('approved 42')
    B-->>U: ✓ Approved PR #42
```

Verify init-data trong n8n Function node:

```js
// n8n verify Telegram initData
const crypto = require('crypto');
const initData = $headers['x-telegram-init-data'];
const params = new URLSearchParams(initData);
const hash = params.get('hash'); params.delete('hash');
const dataCheck = [...params.entries()].sort().map(([k,v]) => `${k}=${v}`).join('\n');
const secret = crypto.createHmac('sha256', 'WebAppData').update($env.TELEGRAM_BOT_TOKEN).digest();
const hmac = crypto.createHmac('sha256', secret).update(dataCheck).digest('hex');
if (!crypto.timingSafeEqual(Buffer.from(hash,'hex'), Buffer.from(hmac,'hex'))) throw new Error('bad initData');
const user = JSON.parse(params.get('user'));
return [{ json: { user_id: user.id } }];
```

Python bot publish nút WebApp:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
async def cmd_app(update, ctx):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Open DevStation", web_app=WebAppInfo(url="https://app.devstn.xyz/mini"))
    ]])
    await update.message.reply_text("Open the DevStation control panel:", reply_markup=kb)
```

UI Mini App tối thiểu (1 file `index.html` + 1 file `app.js` trong `iphone-pwa/web/mini/`) — 5 màn hình:

- **Queue**: list 20 task gần nhất từ `task_ledger`, mỗi row hiện state badge (queued/running/review/merged/reverted/failed) + risk_score.
- **Detail**: chi tiết task, link PR, AI Gate report rendered từ JSON.
- **Approve / Reject**: nút lớn (44px theo HIG) → POST `/approve` hoặc `/reject` (xoá branch).
- **Comment**: textarea → POST `/comment` (n8n chuyển về Linear comment).
- **Status**: queue depth, budget %, last 3 alerts.

### 12.5 PWA standalone (Tier C)

Khi cần đọc diff dài hơn, dùng `https://app.devstn.xyz` (không bị giới hạn không gian Telegram WebView). Auth = Linear OAuth (whitelist user). Thêm to Home Screen → biểu tượng riêng, fullscreen.

Minimal `iphone-pwa/web/manifest.webmanifest`:

```json
{
  "name": "DevStation",
  "short_name": "DevStation",
  "start_url": "/",
  "display": "standalone",
  "theme_color": "#0e0e10",
  "background_color": "#0e0e10",
  "icons": [{ "src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }]
}
```

Service worker chỉ cache shell + GET `/api/tasks` 30s → mở app vẫn xem được khi mất mạng tạm thời.

iOS Push: dùng Telegram (Tier B) cho push thay vì Web Push — đỡ phải xin Apple Developer + setup APNs.

### 12.6 API gateway cho mobile (`/api/*` qua Traefik → n8n)

Tất cả endpoint mobile nằm trong 1 n8n workflow `mobile-api` với các route con: `/tasks`, `/tasks/:id`, `/approve`, `/reject`, `/comment`, `/pause`, `/resume`, `/status`, `/queue`. Mỗi route:

1. Verify token (Telegram initData hoặc Linear OAuth bearer).
2. Whitelist user_id.
3. Đọc/ghi Postgres + Redis + GitHub/Linear API.
4. Trả JSON nhỏ gọn (< 5KB) để load nhanh trên 4G.

---

## 13. Unified install & manage script `bin/devstation.sh`

### 13.1 Mục tiêu

Một script duy nhất chạy trên cả 3 node để: bootstrap tools, gộp env, up/down stack đúng node, xem log, status, backup. Idempotent — chạy nhiều lần không hỏng gì.

### 13.2 Cú pháp

```
devstation.sh [--node mini|studio|air] [--profile core|monitoring|ingress|ai-control|full]
              [--dry-run] <action> [args...]

Actions:
  bootstrap     install homebrew, docker, tailscale, ollama (studio), gh CLI, opencode (studio)
  up            docker compose up -d (chỉ services thuộc node + profile chỉ định)
  down          docker compose down
  restart       down + up
  update        git pull + docker compose pull + up
  logs [svc]    tail logs (cả node, hoặc 1 service)
  status        in healthcheck + tóm tắt CPU/RAM/disk
  ps            docker compose ps
  backup        pg_dump + tar volumes → ~/devstation/backups/
  restore <f>   restore từ backup file
  doctor        kiểm tra DNS, Tailscale, Cloudflare Tunnel, port, healthchecks
  models        (studio only) ollama pull + verify model list
  open          mở Grafana / n8n / Mini App trong browser tương ứng node
```

### 13.3 Auto-detect node nếu thiếu `--node`

Dựa hostname mac:

- hostname chứa `mini` hoặc `Mac-mini` → mini
- hostname chứa `studio` hoặc model = `MacStudio` → studio
- hostname chứa `Air` hoặc model = `MacBookAir` → air

### 13.4 Implementation (rút gọn)

```bash
#!/usr/bin/env bash
# bin/devstation.sh — unified manager. POSIX-bash, macOS 12+.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY=""; NODE=""; PROFILE="core"

log()  { printf "\033[1;36m[devstation]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[devstation] %s\033[0m\n" "$*" >&2; exit 1; }
run()  { [ -n "$DRY" ] && { echo "+ $*"; return; }; eval "$*"; }

detect_node() {
  local h
  h="$(scutil --get ComputerName 2>/dev/null || hostname)"
  case "$h" in
    *Mini*|*mini*)        echo mini ;;
    *Studio*|*studio*)    echo studio ;;
    *Air*|*air*)          echo air ;;
    *) fail "cannot auto-detect node, pass --node";;
  esac
}

parse_args() {
  ACTION=""; ARGS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)    NODE="$2"; shift 2;;
      --profile) PROFILE="$2"; shift 2;;
      --dry-run) DRY=1; shift;;
      -h|--help) usage; exit 0;;
      *)         if [ -z "$ACTION" ]; then ACTION="$1"; else ARGS+=("$1"); fi; shift;;
    esac
  done
  [ -z "$NODE" ]   && NODE="$(detect_node)"
  [ -z "$ACTION" ] && { usage; exit 1; }
}

merge_env() {
  mkdir -p "$ROOT/.run"
  local out="$ROOT/.run/.env.$NODE"
  cat "$ROOT/env/.env.shared" "$ROOT/env/.env.$NODE" > "$out"
  printf "NODE_NAME=%s\n" "$NODE" >> "$out"
  echo "$out"
}

compose_files() {
  echo "-f $ROOT/compose/common.yml -f $ROOT/compose/$NODE.yml"
}

profiles_args() {
  if [ "$PROFILE" = "full" ]; then
    echo "--profile core --profile monitoring --profile ingress --profile ai-control"
  else
    IFS=',' read -ra arr <<<"$PROFILE"
    local s=""; for p in "${arr[@]}"; do s="$s --profile $p"; done; echo "$s"
  fi
}

docker_compose() {
  local env_file; env_file="$(merge_env)"
  run "docker compose --env-file '$env_file' $(compose_files) $(profiles_args) $*"
}

bootstrap() {
  log "bootstrapping $NODE"
  command -v brew >/dev/null || run '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  run "brew bundle --no-lock --file=$ROOT/Brewfile.$NODE"
  run "sudo tailscale up --ssh --accept-routes || true"
  if [ "$NODE" = studio ]; then
    command -v ollama >/dev/null || run "brew install ollama"
    run "brew services start ollama"
    run "$0 --node studio models"
  fi
  run "docker version >/dev/null 2>&1 || open -a OrbStack || open -a Docker"
}

cmd_up()      { docker_compose "up -d"; }
cmd_down()    { docker_compose "down"; }
cmd_restart() { docker_compose "down"; docker_compose "up -d"; }
cmd_update()  { run "git -C $ROOT pull --ff-only"; docker_compose "pull"; docker_compose "up -d"; }
cmd_logs()    { docker_compose "logs -f --tail=200 ${ARGS[*]:-}"; }
cmd_ps()      { docker_compose "ps"; }
cmd_status()  { docker_compose "ps"; run "docker stats --no-stream"; run "df -h | grep -E '/$|/Volumes'"; }

cmd_backup() {
  local dir="$ROOT/backups/$(date +%Y%m%d_%H%M%S)"
  run "mkdir -p '$dir'"
  if [ "$NODE" = mini ]; then
    run "docker exec devstation-mini-postgres-1 pg_dump -U devstation devstation | gzip > '$dir/postgres.sql.gz'"
    run "docker run --rm -v devstation-mini_n8n_data:/d -v '$dir':/b alpine tar czf /b/n8n.tgz -C /d ."
    run "docker run --rm -v devstation-mini_grafana_data:/d -v '$dir':/b alpine tar czf /b/grafana.tgz -C /d ."
  fi
  log "backup written to $dir"
}

cmd_doctor() {
  log "checking DNS";       run "dig +short ${PUBLIC_DOMAIN:-devstn.xyz} | head -1"
  log "checking Tailscale";  run "tailscale status | head -5"
  log "checking docker";     run "docker version --format '{{.Server.Version}}'"
  log "checking healthchecks"
  docker_compose "ps --format '{{.Service}} {{.Status}}'"
  if [ "$NODE" = studio ]; then run "curl -fsS http://localhost:11434/api/tags | head -c 200; echo"; fi
}

cmd_models() {
  [ "$NODE" = studio ] || fail "models is only for studio"
  for m in qwen2.5-coder:32b-instruct-q4_K_M llama3.1:70b-instruct-q4_K_M nomic-embed-text; do
    run "ollama pull '$m'"
  done
  run "ollama list"
}

cmd_open() {
  case "$NODE" in
    mini)   open "https://grafana.${PUBLIC_DOMAIN:-devstn.xyz}";;
    studio) open "http://localhost:11434/api/tags";;
    air)    open "http://127.0.0.1:4000";;
  esac
}

usage() { sed -n '2,40p' "$0"; }

parse_args "$@"
log "node=$NODE profile=$PROFILE action=$ACTION"
case "$ACTION" in
  bootstrap) bootstrap ;;
  up)        cmd_up ;;
  down)      cmd_down ;;
  restart)   cmd_restart ;;
  update)    cmd_update ;;
  logs)      cmd_logs ;;
  ps)        cmd_ps ;;
  status)    cmd_status ;;
  backup)    cmd_backup ;;
  doctor)    cmd_doctor ;;
  models)    cmd_models ;;
  open)      cmd_open ;;
  *)         usage; exit 1 ;;
esac
```

Sử dụng nhanh:

```bash
# Trên Mac Mini
./bin/devstation.sh bootstrap                        # 1 lần
./bin/devstation.sh --profile core up                # bật ops core
./bin/devstation.sh --profile core,monitoring,ingress,ai-control up   # full
./bin/devstation.sh status
./bin/devstation.sh logs ai-gate

# Trên Mac Studio
./bin/devstation.sh bootstrap
./bin/devstation.sh models                           # pull Ollama models
./bin/devstation.sh up                               # CrewAI + OpenCode runner + RAG

# Trên MacBook Air
./bin/devstation.sh up                               # AI router local
./bin/devstation.sh open                             # mở http://127.0.0.1:4000
```

Idempotent: chạy `up` lần 2 chỉ pull image mới + recreate container thay đổi. `bootstrap` skip những gì đã có.

---

## 14. Roadmap thực thi (đề xuất 4 tuần)

| Tuần | Hạng mục | Định nghĩa hoàn thành |
|---|---|---|
| 1 | Mac Mini: `devstation.sh --profile core,monitoring,ingress up` | Linear webhook chạm n8n; rows xuất hiện ở `webhook_events`; dashboard `DevStation Overview` có dữ liệu 8 panel. |
| 2 | Mac Studio: bootstrap + models + crewai + opencode-runner + rag-indexer | `XADD tickets` → CrewAI pickup, mở PR test. `memory_chunks` > 5k rows. |
| 3 | AI control: ai-router + ai-gate + telegram-bridge + iphone-pwa | 70%+ request local; Telegram Mini App approve PR thành công trên iPhone. |
| 4 | iPhone Shortcuts + alert rules + auto-rollback | "Hey Siri, DevStation queue" trả lời đúng; prod fail giả lập → tự revert; alert về Telegram. |

---

## 15. Tham chiếu

- CrewAI: <https://docs.crewai.com>
- LiteLLM proxy: <https://docs.litellm.ai/docs/proxy/quick_start>
- Ollama API: <https://github.com/ollama/ollama/blob/main/docs/api.md>
- pgvector: <https://github.com/pgvector/pgvector>
- Linear API: <https://developers.linear.app>
- OpenCode: <https://github.com/opencode-ai/opencode>
- n8n queue mode: <https://docs.n8n.io/hosting/scaling/queue-mode/>
- Traefik v3: <https://doc.traefik.io/traefik/>
- Grafana provisioning: <https://grafana.com/docs/grafana/latest/administration/provisioning/>
- Telegram Mini App: <https://core.telegram.org/bots/webapps>
- Apple Shortcuts URL scheme: <https://support.apple.com/guide/shortcuts/welcome/ios>
- Anthropic Claude (Sonnet 4.5/4.6): <https://docs.anthropic.com>

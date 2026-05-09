# Architecture & Network Flow

**Stack:** Linear → n8n → Pipeline (CrewAI) → 9router → Providers → GitHub PR
**Hosts:** Mac Mini 2015 (orchestrator), Mac Studio M1 Max (worker), MacBook Air M1 (client)
**Network:** Tailscale Mesh VPN — zero public ports

## 1. Node topology

| Node | Role | Hardware | Always on | Services |
|---|---|---|---|---|
| **Mac Mini** | Orchestrator | Intel i7, 16 GB | Yes | Docker: postgres, redis, n8n, telegram-bridge |
| **Mac Studio** | Worker | M1 Max, 64 GB | Yes | Ollama (brew), Docker: pipeline, opencode, **9router** |
| **MacBook Air** | Client | M1, 8 GB | On-demand | OpenCode CLI, VS Code, git |
| **iPhone** | Remote control | — | Yes | Telegram app |

## 2. Trust zones

| Zone | Members | Reachability |
|---|---|---|
| **Mesh** | All 3 Macs via Tailscale | `100.x.x.x` (Tailscale IPs only) |
| **Loopback** | Docker containers on Mini/Studio | Localhost only |
| **Cloud** | DeepSeek, MiniMax, Claude, GitHub APIs | Outbound HTTPS only |

**Zero public ports.** No reverse proxy, no Cloudflare Tunnel, no open firewall rules. All inter-node communication is over Tailscale Mesh.

## 3. Flow diagram

```mermaid
flowchart LR
  subgraph CLOUD["Cloud APIs"]
    DS["DeepSeek API"]
    MX["MiniMax API"]
    NV["NVIDIA API"]
    KR["Kiro AI (free)"]
    CL["Claude API"]
    GH["GitHub"]
    LN["Linear"]
  end

  subgraph MINI["Mac Mini 2015 (orchestrator)"]
    PG["Postgres<br/>:5432"]
    RD["Redis<br/>:6379"]
    N8N["n8n<br/>:5678"]
    TB["Telegram Bridge<br/>:7700"]
  end

  subgraph STUDIO["Mac Studio M1 Max (worker)"]
    OL["Ollama<br/>:11434"]
    PL["Pipeline<br/>:8001"]
    OC["OpenCode"]
    R9["9router<br/>:20128"]
  end

  subgraph AIR["MacBook Air (client)"]
    OC2["OpenCode CLI"]
    VS["VS Code"]
  end

  subgraph IPHONE["iPhone"]
    TG["Telegram App"]
  end

  LN --> N8N
  N8N --> PL
  PL --> R9
  R9 --> KR
  R9 --> MX
  R9 --> NV
  R9 --> DS
  R9 --> OL
  PL --> GH
  PL --> TB
  TB --> TG
  OC2 --> OL
  OC2 --> R9
  OC2 --> GH

  MINI --- |Tailscale| STUDIO
  AIR --- |Tailscale| STUDIO
  AIR --- |Tailscale| MINI
```

## 4. 9router & LLM fallback chain

**9router** (port 20128) provides:
- Token compression (RTK) - saves 20-40%
- Auto-fallback: Subscription → Cheap → Free
- Real-time quota tracking
- Dashboard for provider management

```
Pipeline → 9router → Kiro AI (free, unlimited)
              → MiniMax M2.7 ($0.2/1M)
              → NVIDIA Nemotron ($1.5/1M)
              → DeepSeek V4 ($0.14/1M)
              → Studio Ollama (local, free)
```

## 5. Self-healing properties

| Layer | Mechanism | Recovery time |
|---|---|---|
| Docker containers | `restart: unless-stopped` | < 5 s |
| Ollama | `brew services start ollama` | < 10 s |
| 9router | `docker compose up -d 9router` | < 10 s |
| Tailscale mesh | macOS managed extension | < 10 s |
| Power failure | `sudo pmset -a autorestart 1` | < 60 s |
| Git push failure | Pipeline retries up to 3 times | Per attempt |
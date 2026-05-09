# Self-Healing Strategy

Goal: all services recover automatically from failures without human intervention.

## 1. Docker containers

All containers use `restart: unless-stopped`. Docker will restart them on crash.

```bash
# Verify all containers are healthy
./bin/devstation.sh doctor
```

## 2. Ollama (Mac Studio)

Managed by `brew services`. Auto-starts on boot.

```bash
brew services start ollama
brew services list | grep ollama
```

## 3. 6-Agent pipeline retry logic

| Failure | Handler | Max retries |
|---|---|---|
| LLM timeout | Provider fallback (Ollama → DeepSeek → Claude) | 3 |
| Git push conflict | Rebase + retry | 3 |
| Reviewer: blocked | Stop, notify human | 0 |
| Reviewer: changes_requested | Healer Agent auto-fixes issues | 3 iterations |
| All LLM providers down | Grafana alert P1 | — |

### Healer Agent flow

```
Reviewer finds issues → Healer generates fixes → apply → re-review
  ├── Pass → commit
  ├── Still failing → Healer retry (up to 3)
  └── Exhausted → Telegram + PWA "needs human review"
```

## 4. Observability alerts

Prometheus alert rules (in `prometheus/alerts.yml`):

| Alert | Condition | Severity |
|---|---|---|
| PipelineHighFailureRate | >50% failure rate over 5m | critical |
| LLMAllProvidersDown | All LLM providers unreachable | critical |
| HealerExhausted | Healer retries > 3 for one task | warning |

Alerts fire → Admins see on:
- Grafana dashboard (`http://{TAILSCALE_IP}:3000`)
- iPhone PWA dashboard (`http://{TAILSCALE_IP}:8080`)

## 5. Power failure recovery

```bash
# Mac Mini / Mac Studio — auto-restart after power loss
sudo pmset -a autorestart 1
sudo systemsetup -setrestartpowerfailure on
```

Docker starts automatically via `Docker.app` login item. `brew services` resume on boot.

## 6. Manual health check

```bash
./bin/devstation.sh doctor
```

This checks:
- All Docker containers running
- Ollama responding
- Pipeline (:8001/healthz) responding
- Grafana (:3000), Prometheus (:9090), PWA (:8080) reachable
- Tailscale connectivity between nodes
- Git auth working

## 7. Auto-upgrade

```bash
# Add to cron (Mac Mini):
0 * * * * cd /path/to/AITools && ./bin/devstation.sh --node mini auto-update
```

On update: git pull → docker compose pull → rebuild → health check → rollback on failure.

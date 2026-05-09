# Execution Guide — Operating the Dev Station

## Daily operations

### Check status

```bash
# On each machine
./bin/devstation.sh status

# Via Telegram (from iPhone or any device)
/status
```

### View logs

```bash
# All services
./bin/devstation.sh logs

# Specific service
./bin/devstation.sh logs pipeline
```

### Start / stop

```bash
# On Mac Mini or Mac Studio
./bin/devstation.sh up
./bin/devstation.sh down
./bin/devstation.sh restart
```

### Run the pipeline manually

```bash
# On Mac Studio
docker exec devstation-studio-pipeline-1 python pipeline.py \
  --issue "AI-123" \
  --repo "user/repo" \
  --title "Fix login timeout" \
  --body "Description of the issue"
```

### Use OpenCode on MacBook Air

```bash
# Uses Studio's Ollama by default
opencode "fix the race condition in process.py"

# Override LLM source
opencode "add error handling" --model deepseek
opencode "refactor this module" --model claude
```

## Remote control via iPhone

Open Telegram, message your bot:

| Command | What happens |
|---|---|
| `/status` | Bot replies with current pipeline state |
| `/pause` | Pipeline stops processing new tickets |
| `/resume` | Pipeline resumes |
| `/approve` | Current task approved, PR is finalized |
| `/reject` | Current task rejected, healer retries |
| `/llm cloud` | Switch to cloud APIs |
| `/llm studio` | Switch to Studio's local Ollama |

## Backup & restore

```bash
# On Mac Mini
./bin/devstation.sh backup

# Restore
./bin/devstation.sh restore backups/db_20260427_143022.sql
```

## Update

```bash
# Pull latest code + update Docker images
./bin/devstation.sh update
```

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


# AIOps & GitOps Strategy

## GitOps: automated deployment

When changes are merged to this repo's `main` branch, the update propagates automatically:

```bash
# On each machine, devstation.sh update pulls latest + restarts
./bin/devstation.sh update
```

This can be automated via a cron job or GitHub webhook:

```bash
# Every hour, check for updates (add to crontab)
0 * * * * cd /path/to/AITools && git pull --ff-only && ./bin/devstation.sh update
```

## AIOps: automated maintenance

The pipeline self-manages:

| Task | Trigger | Action |
|---|---|---|
| Ollama model update | Weekly | `ollama pull` latest models |
| Git branch cleanup | After PR merge | Delete remote branches |
| Log rotation | Daily | Docker log limits |
| Cost tracking | Per task | Max $1/task budget guard |

## Safety guardrails

- Git identity includes machine name: `"Name (studio)"` so you know who committed
- PRs are opened as drafts — you approve via Telegram before merge
- Each task has a max budget of $1.00 in API costs
- Pipeline retries 3 times then notifies you


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
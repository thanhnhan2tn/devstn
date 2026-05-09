# Linear → n8n → Pipeline — Operator Verification Checklist

Target: **Mac Mini 2015 (macOS 12.7.4)** orchestrator on **Tailscale** mesh.
Admin device: **MacBook Air M1**.

Admin UI ingress: `http://<TAILSCALE_IP>:5678`

---

## 0. One-time secrets

```bash
export LINEAR_API_KEY='lin_api_xxxxxxxxxxxx'    # https://linear.app/settings/api
export LINEAR_TEAM_ID='xxxxxxxx-xxxx-xxxx-...'  # Linear team UUID
export STUDIO_TAILSCALE_IP='100.x.x.x'          # Mac Studio Tailscale IP
```

No `LINEAR_WEBHOOK_SECRET` is required: n8n's native **Linear Trigger** node auto-registers the webhook and verifies signatures internally.

---

## 1. Prerequisites

- n8n running in Docker on Mac Mini (`./bin/devstation.sh up`)
- Pipeline serving on Mac Studio at `http://{STUDIO_TAILSCALE_IP}:8001/healthz`
- `projects.yaml` configured with your Linear team → GitHub repo mapping

---

## 2. n8n service health

| Check | Command | Expected |
|---|---|---|
| Container running | `docker ps \| grep n8n` | `n8n` container up |
| HTTP responding | `curl -fsS http://${TAILSCALE_IP}:5678/healthz` | `{"status":"ok"}` |

---

## 3. Linear credential + workflow activation

### 3a. Create the Linear API credential in n8n

1. n8n UI (`http://{TAILSCALE_IP}:5678`) → **Credentials → New** → **Linear API**.
2. Paste your `LINEAR_API_KEY`. Save.
3. Note the credential ID — edit `linear_to_opencode.json` to match.

### 3b. Activate the workflow

1. In n8n UI, import `linear-pipeline/linear_to_opencode.json`
2. Set the Linear Trigger credential to the one created in step 3a
3. Flip workflow to **Active**

### 3c. Verify

In Linear, create or update an issue with tag **"plan"** or **"implement"**. Within ~5s, n8n's *Executions* tab shows a new run triggering `POST /webhook` to the pipeline.

---

## 4. n8n workflow import

```bash
# Import via n8n CLI in Docker
docker exec -i devstation-mini-n8n-1 n8n import:workflow --input=- < ./linear_to_opencode.json
```

Then in the n8n UI:

- Open the imported workflow → **Linear Trigger** node → set **Credential** to *Linear API*
- Open the **Comment on Linear** node → set the same credential
- Ensure env vars `LINEAR_TEAM_ID` and `STUDIO_TAILSCALE_IP` are set on the n8n container

---

## 5. End-to-end verification

1. Create an issue in Linear with tag **"plan"**
2. Within ~10s, n8n shows a successful execution
3. Within ~60s, pipeline creates a draft PR with plan.md
4. The Linear comment posted by n8n includes `📋 Plan doc: <github blob URL>`
   pointing at `docs/plans/{issue_id}.md` on the plan commit.
5. Add tag **"implement"** to the same issue
6. Within ~120s, pipeline generates code, commits to the same branch, updates PR
7. The implement Linear comment re-references the same plan-doc URL so the
   plan is one click away when reviewing the implementation.

### Pipeline response contract

`POST /webhook` returns JSON with the following fields (see `pipeline.py`):

| Field | Phase | Description |
|---|---|---|
| `status` | both | `ok` \| `error` \| `ignored` |
| `phase` | both | `plan` \| `implement` |
| `pr_url` | both | URL of the draft / final PR |
| `plan_url` | both | GitHub blob permalink to `docs/plans/{issue_id}.md` (commit-pinned). May be `null` on implement if no plan was generated. |
| `verdict` | implement | Reviewer verdict (`approve`, `changes_requested`, …) |
| `spec` | plan | Spec dict from the PM agent |
| `error` | error | Failure message |

The n8n `Format Result` node in `linear_to_opencode.json` consumes this
contract and renders a Linear comment of the form:

```
🤖 AI Dev Station — *{issue_id}* — Plan complete

📋 Plan doc: {plan_url}
🔗 PR: {pr_url}
```

(The implement comment also includes `✅ Verdict: {verdict}`.)

## 6. Common failure modes & fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Webhook returns 404 | Pipeline not running | `curl http://{STUDIO_TAILSCALE_IP}:8001/healthz` — restart pipeline |
| Linear Trigger won't activate | Missing credential | Set Linear API credential in n8n UI |
| Pipeline returns error | LLM providers down | Check `OLLAMA_URL`, `DEEPSEEK_API_KEY`, `CLAUDE_API_KEY` in .env |
| PR already exists | Same branch open | Idempotent — pipeline finds existing PR |

---

## 7. File reference

| Path | Purpose |
|---|---|
| `linear-pipeline/linear_to_opencode.json` | Importable n8n workflow (tag-based Linear → pipeline trigger) |

## 8. Security posture

- n8n binds to **Tailscale IP only** (`{TAILSCALE_IP}:5678`).
- Pipeline binds to port 8001 on Mac Studio (Tailscale mesh).
- Linear webhook signature is verified by n8n's Linear Trigger node automatically.
- Admin UI reachable only over Tailscale mesh.

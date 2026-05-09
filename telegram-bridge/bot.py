"""Telegram bridge — runs on Mac Mini. Forwards notifications + handles commands with inline menus."""

import asyncio
import logging
import os

import httpx
import redis as _redis
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram-bridge")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API = f"https://api.telegram.org/bot{TOKEN}"
R = _redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))

STUDIO_IP = os.environ.get("STUDIO_TAILSCALE_IP", "")
PIPELINE_URL = f"http://{STUDIO_IP}:8001" if STUDIO_IP else "http://studio:8001"

app = FastAPI(title="Telegram Bridge")


def persistent_menu():
    return {
        "keyboard": [
            [{"text": "Status"}, {"text": "Pause"}, {"text": "Resume"}],
            [{"text": "Approve"}, {"text": "Reject"}, {"text": "Rerun"}, {"text": "LLM Mode"}],
        ],
        "resize_keyboard": True,
        "persistent": True,
    }


def llm_inline_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "Studio (Local)", "callback_data": "llm studio"},
                {"text": "Cloud", "callback_data": "llm cloud"},
                {"text": "Auto", "callback_data": "llm auto"},
            ],
        ]
    }


async def api(method: str, **kwargs):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{API}/{method}", json=kwargs)
        return r.json()


async def send(text: str, parse_mode: str = "Markdown", markup: dict = None):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode}
    payload["reply_markup"] = markup if markup else persistent_menu()
    await api("sendMessage", **payload)


async def edit(chat_id: str, msg_id: int, text: str, markup: dict = None):
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if markup:
        payload["reply_markup"] = markup
    await api("editMessageText", **payload)


async def answer_cb(cb_id: str, text: str = ""):
    await api("answerCallbackQuery", callback_query_id=cb_id, text=text)


async def set_commands():
    await api("setMyCommands", commands=[
        {"command": "start", "description": "Show main menu"},
        {"command": "menu", "description": "Show main menu"},
        {"command": "status", "description": "Check pipeline status"},
        {"command": "pause", "description": "Pause the pipeline"},
        {"command": "resume", "description": "Resume the pipeline"},
        {"command": "approve", "description": "Approve pending review"},
        {"command": "reject", "description": "Reject pending review"},
        {"command": "rerun", "description": "Rerun pipeline for issue"},
        {"command": "llm", "description": "Set LLM mode: studio, cloud, or auto"},
        {"command": "help", "description": "Show all commands"},
    ])


async def pipeline_get(path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{PIPELINE_URL}{path}")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        log.warning("pipeline GET %s failed: %s", path, e)
    return {}


async def pipeline_post(path: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{PIPELINE_URL}{path}")
            return r.status_code == 200
    except Exception as e:
        log.warning("pipeline POST %s failed: %s", path, e)
    return False


# --- Command Handlers ---

async def cmd_start():
    await send(
        "AI Dev Station Bot\n\n"
        "I manage your AI-powered development pipeline.\n"
        "Tap buttons below to control the bot."
    )


async def cmd_menu():
    await send("Main Menu")


async def cmd_help():
    await send(
        "/start  Show welcome + menu\n"
        "/menu   Show main menu\n"
        "/status Pipeline status\n"
        "/pause  Pause pipeline\n"
        "/resume Resume pipeline\n"
        "/approve Approve pending review\n"
        "/reject  Reject pending review\n"
        "/rerun [issue_id] Rerun pipeline (latest or specify)\n"
        "/llm studio|cloud|auto  Set LLM mode\n"
        "/help   This message"
    )


async def cmd_status():
    health = await pipeline_get("/healthz")
    reviews = await pipeline_get("/pending-reviews")
    paused = R.get("pipeline:paused")
    llm_mode = (R.get("air_llm_mode") or b"auto").decode()

    lines = [
        "Pipeline Status",
        "",
        f"Service: {'Running' if health.get('status') == 'ok' else 'Offline'}",
        f"Pipeline: {'Paused' if paused else 'Active'}",
        f"LLM Mode: {llm_mode}",
        f"Active Tasks: {len(health.get('tasks', []))}",
        f"Pending Reviews: {len(reviews)}",
    ]

    tasks = health.get("tasks", [])
    if tasks:
        lines.append("")
        lines.append("Tasks:")
        for t in tasks:
            lines.append(f"  {t.get('issue_id')} - {t.get('status', '?')}")

    if reviews:
        lines.append("")
        lines.append("Pending Reviews:")
        for r in reviews:
            lines.append(f"  {r.get('issue_id')} - {r.get('summary', '')[:80]}")

    await send("\n".join(lines))


async def cmd_pause():
    R.set("pipeline:paused", "1")
    await send("Pipeline Paused\n\nUse /resume to continue.")


async def cmd_resume():
    R.delete("pipeline:paused")
    await send("Pipeline Resumed")


async def cmd_approve():
    reviews = await pipeline_get("/pending-reviews")
    if not reviews:
        await send("No pending reviews to approve.")
        return
    rid = reviews[0].get("id") or reviews[0].get("issue_id")
    if await pipeline_post(f"/review/{rid}/approve"):
        await send(f"Approved {rid}\n\nProceeding with the task.")
    else:
        await send("Failed to approve. Is the pipeline running?")


async def cmd_reject():
    reviews = await pipeline_get("/pending-reviews")
    if not reviews:
        await send("No pending reviews to reject.")
        return
    rid = reviews[0].get("id") or reviews[0].get("issue_id")
    if await pipeline_post(f"/review/{rid}/reject"):
        await send(f"Rejected {rid}\n\nHealer will retry.")
    else:
        await send("Failed to reject. Is the pipeline running?")


async def cmd_rerun(issue_id: str = None):
    """Rerun the pipeline for an issue. If no issue_id, uses latest completed task."""
    if not issue_id:
        health = await pipeline_get("/healthz")
        tasks = health.get("tasks", [])
        if tasks:
            issue_id = tasks[-1].get("issue_id", "")
        if not issue_id:
            await send("No active tasks. Please specify issue ID: /rerun AI-123")
            return

    await send(f"Rerunning pipeline for {issue_id}...")

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{PIPELINE_URL}/task/{issue_id}")
            if r.status_code == 200:
                task = r.json()
                current_phase = task.get("phase", "implement")
                current_status = task.get("status", "")
                if current_status in ("completed", "approved"):
                    current_phase = "implement"
                elif current_status in ("pending", "needs_review"):
                    pass
                log.info(f"Rerun: current phase={current_phase}, status={current_status}")
            else:
                current_phase = "implement"
    except Exception:
        current_phase = "implement"

    project = {"github_repo": "thanhnhan2tn/mini-dev-station", "base_branch": "main", "name": "telegram"}
    payload = {
        "issue_id": issue_id,
        "title": f"Rerun from Telegram: {issue_id}",
        "description": "Rerun via Telegram bot",
        "phase": current_phase,
        "repo": "thanhnhan2tn/mini-dev-station",
        "team_id": "",
        "labels": ["rerun"],
    }

    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{PIPELINE_URL}/webhook", json=payload)
            if r.status_code in (200, 202):
                await send(f"✅ Pipeline rerun started for {issue_id} (phase: {current_phase})")
            else:
                await send(f"❌ Failed: {r.status_code} {r.text}")
    except Exception as e:
        await send(f"❌ Error: {e}")


async def cmd_llm(args: str):
    mode = args.strip().lower()
    if mode in ("studio", "cloud", "auto"):
        R.set("air_llm_mode", mode)
        await send(f"LLM Mode set to {mode}")
    else:
        await send("Usage: /llm studio | cloud | auto", markup=llm_inline_menu())


async def cmd_llm_menu():
    await send("Select LLM Mode\n\nChoose which provider the Air node should use:", markup=llm_inline_menu())


# --- Update Handler ---

async def handle_update(update: dict):
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        data = cb.get("data", "")
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
        msg_id = cb.get("message", {}).get("message_id")

        if chat_id != CHAT_ID:
            await answer_cb(cb_id)
            return

        if data == "status":
            await answer_cb(cb_id, "Checking status...")
            health = await pipeline_get("/healthz")
            reviews = await pipeline_get("/pending-reviews")
            paused = R.get("pipeline:paused")
            llm_mode = (R.get("air_llm_mode") or b"auto").decode()
            lines = [
                "Pipeline Status",
                "",
                f"Service: {'Running' if health.get('status') == 'ok' else 'Offline'}",
                f"Pipeline: {'Paused' if paused else 'Active'}",
                f"LLM Mode: {llm_mode}",
                f"Active Tasks: {len(health.get('tasks', []))}",
                f"Pending Reviews: {len(reviews)}",
            ]
            await edit(chat_id, msg_id, "\n".join(lines))

        elif data == "pause":
            R.set("pipeline:paused", "1")
            await answer_cb(cb_id, "Pipeline paused")
            await edit(chat_id, msg_id, "Pipeline Paused")

        elif data == "resume":
            R.delete("pipeline:paused")
            await answer_cb(cb_id, "Pipeline resumed")
            await edit(chat_id, msg_id, "Pipeline Resumed")

        elif data == "approve":
            await answer_cb(cb_id, "Approving...")
            await cmd_approve()

        elif data == "reject":
            await answer_cb(cb_id, "Rejecting...")
            await cmd_reject()

        elif data == "rerun":
            await answer_cb(cb_id, "Rerunning...")
            await cmd_rerun()

        elif data == "llm_menu":
            await answer_cb(cb_id)
            await edit(chat_id, msg_id, "Select LLM Mode", markup=llm_inline_menu())

        elif data.startswith("llm "):
            mode = data.replace("llm ", "")
            R.set("air_llm_mode", mode)
            await answer_cb(cb_id, f"LLM mode set to {mode}")
            await edit(chat_id, msg_id, f"LLM Mode set to {mode}")

        elif data == "menu":
            await answer_cb(cb_id)
            await edit(chat_id, msg_id, "Main Menu")

        else:
            await answer_cb(cb_id)
        return

    msg = update.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "")

    if chat_id != CHAT_ID:
        return

    t = text.lower().strip().lstrip("/")
    log.info(f"Received command: '{text}' -> parsed: '{t}'")

    commands = {
        "start": cmd_start,
        "menu": cmd_menu,
        "status": cmd_status,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "rerun": cmd_rerun,
        "help": cmd_help,
    }

    if t in commands:
        await commands[t]()
    elif t == "rerun" or t.startswith("rerun "):
        issue_id = t.replace("rerun", "").strip() or None
        log.info(f"rerun command with issue_id: {issue_id}")
        await cmd_rerun(issue_id)
    elif t == "llm mode":
        await cmd_llm_menu()
    elif t.startswith("llm") or t.startswith("/llm"):
        await cmd_llm(t.replace("llm", "").replace("/llm", "").strip())
    else:
        await send(f"Unknown: {text}")


async def poll_loop():
    offset = 0
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            try:
                r = await c.get(
                    f"{API}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
                )
                if not r.is_success:
                    log.warning("poll HTTP error: %d %s", r.status_code, r.text[:200])
                    await asyncio.sleep(5)
                    continue
                data = r.json()
                if not data.get("ok"):
                    err = data.get("error_code")
                    desc = data.get("description", "")
                    if err == 409:
                        log.warning("poll conflict - another instance running, waiting 10s")
                        await asyncio.sleep(10)
                    else:
                        log.warning("poll API error: %d %s", err, desc)
                    continue
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    await handle_update(update)
            except Exception as e:
                log.warning("poll error: %s %s", type(e).__name__, e)
            await asyncio.sleep(1)


@app.on_event("startup")
async def startup():
    await set_commands()
    asyncio.create_task(poll_loop())
    log.info("Telegram bridge started with persistent menu")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/notify")
async def notify(request: Request):
    body = await request.json()
    await api("sendMessage", chat_id=CHAT_ID, text=body.get("message", ""), parse_mode="Markdown", reply_markup=persistent_menu())
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    await handle_update(update)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7700)

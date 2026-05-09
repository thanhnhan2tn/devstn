# 9router Setup Guide

9router is an AI router that provides:
- Token compression (RTK) - saves 20-40%
- Auto-fallback: Subscription → Cheap → Free
- Real-time quota tracking
- Web dashboard

## 1. Start 9router

```bash
# On Mac Studio
docker compose -f compose/studio.yml up -d 9router
```

Access dashboard: `http://localhost:20128/dashboard`
Password: `123456`

## 2. Add Providers via Dashboard

### Option A: Free Providers (Recommended)

**Kiro AI** (FREE unlimited - Claude 4.5 + GLM-5 + MiniMax)
1. Go to Dashboard → Providers
2. Click "Connect" on Kiro
3. OAuth login with GitHub/Google/AWS
4. Done - free unlimited!

**OpenCode Free** (No auth required)
1. Go to Dashboard → Providers
2. Click "Connect" on OpenCode Free
3. No login needed

### Option B: Paid Providers

**MiniMax** ($0.2/1M tokens)
1. Get API key from https://platform.minimax.io
2. Go to Dashboard → Providers → Add
3. Select MiniMax
4. Enter API key and base URL: `https://api.minimax.chat/v1`
5. Save

**DeepSeek** ($0.14/1M tokens)
1. Get API key from https://platform.deepseek.com
2. Go to Dashboard → Providers → Add
3. Select DeepSeek
4. Enter API key
5. Save

**NVIDIA** ($1.5/1M tokens)
1. Get API key from https://build.nvidia.com
2. Go to Dashboard → Providers → Add
3. Select NVIDIA
4. Enter API key
5. Save

## 3. Create Combo

Recommended combo for AI Dev Station:

1. Go to Dashboard → Combos → Create New
2. Name: `devstation-stack`
3. Add models in priority order:

```
1. kr/claude-sonnet-4.5    (free, unlimited)
2. minimax/MiniMax-M2.7   (cheap, $0.2/1M)
3. deepseek/deepseek-chat  (fallback, $0.14/1M)
```

## 4. Use in Pipeline

Pipeline automatically uses 9router. Configure in `pipeline.py`:

```python
LIGHT_ROUTER_URL = "http://9router:20128"

LLM_CONFIG = {
    "minimax": {
        "url": LIGHT_ROUTER_URL + "/v1",
        "model": "minimax/MiniMax-M2.7",
    },
    "deepseek": {
        "url": LIGHT_ROUTER_URL + "/v1",
        "model": "deepseek/deepseek-chat",
    },
}
```

## 5. Pipeline Tier Mapping

| Tier | Provider | Model |
|---|---|---|
| simple | studio-coder | ollama/qwen2.5-coder:32b |
| medium | minimax | minimax/MiniMax-M2.7 |
| complex | nvidia | nvidia/llama-3.1-nemotron-70b |
| premium | deepseek | deepseek/deepseek-chat |

## Cost Comparison

| Provider | Cost | Notes |
|---|---|---|
| Kiro AI | $0 | Free unlimited Claude 4.5 |
| OpenCode Free | $0 | No auth required |
| MiniMax M2.7 | $0.2/1M | Cheapest option |
| DeepSeek V4 | $0.14/1M | Good fallback |
| NVIDIA Nemotron | $1.5/1M | Premium fallback |

## Troubleshooting

**No providers available:**
```
Error: "No active credentials for provider"
```
→ Add providers in dashboard first

**Provider not working:**
1. Check dashboard → Providers → Test button
2. Verify API key is correct
3. Check account has credits/balance

**Dashboard not accessible:**
```bash
# Check 9router is running
docker ps | grep 9router

# Restart if needed
docker restart devstation-studio-9router-1
```
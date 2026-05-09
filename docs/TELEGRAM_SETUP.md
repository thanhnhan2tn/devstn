# Telegram Bot Setup

## 1. Create a bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`
3. Choose a name (e.g., `AI Dev Station`)
4. Choose a username (e.g., `ai_dev_station_bot`)
5. Copy the token — it looks like: `1234567890:ABCdefGHIjklmNOPqrStuVWXyz`

## 2. Get your chat ID

1. Start a chat with your new bot
2. Send any message (e.g., `/start`)
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Look for `"chat":{"id":<YOUR_CHAT_ID>}` in the response

## 3. Configure

Add these to `.env` on the Mac Mini:

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrStuVWXyz
TELEGRAM_CHAT_ID=123456789
```

## 4. Available commands

| Command | Action |
|---|---|
| `/start` | Show welcome message + main menu |
| `/menu` | Show main interactive menu |
| `/status` | Check pipeline status, active tasks, pending reviews |
| `/pause` | Pause pipeline |
| `/resume` | Resume pipeline |
| `/approve` | Approve current pending review |
| `/reject` | Reject current pending review |
| `/llm studio` | Use Studio's Ollama |
| `/llm cloud` | Use cloud API |
| `/llm auto` | Auto-select |
| `/help` | List all commands |

The bot also has **inline keyboards** for quick actions — tap the buttons
right in the chat instead of typing commands.
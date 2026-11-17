# Saviron FAQ Bot

Telegram FAQ bot for groups. Uses Telegram webhooks, Flask, and PostgreSQL.

## Render Web Service settings

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --bind 0.0.0.0:$PORT bot:app`
- Plan: Free

## Environment variables

Add these in Render → your service → Environment:

- `BOT_TOKEN` — token from @BotFather
- `OWNER_ID` — your numeric Telegram user ID
- `DATABASE_URL` — PostgreSQL connection string from Neon
- `WEBHOOK_SECRET` — random string of at least 20 characters; use only letters, digits, `_` and `-`

`RENDER_EXTERNAL_URL` is provided automatically by Render.

## Database

Create a free PostgreSQL project at https://neon.tech/.
In Neon, click **Connect**, select the database and role, and copy the connection string.
Paste it into Render as `DATABASE_URL`. Do not publish this value or put it in GitHub.

## Telegram

In @BotFather use `/setprivacy`, select the bot, then choose `Disable`, so it can read ordinary group messages.
Add the bot to your group and allow it to send messages.

## Matching

The bot replies when a saved question matches the message exactly or appears as a phrase inside it. It does not use AI semantic matching.

## Free plan note

Render Free web services can sleep after 15 minutes without inbound traffic and may take around a minute to wake up. Neon Free PostgreSQL has usage limits. Check both providers' current limits before using the bot for important/critical service.

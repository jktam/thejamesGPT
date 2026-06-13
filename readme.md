# The James Bot

A Discord bot with a small set of useful slash commands for group chats, food lookup, and AI helpers.

## Quick Use

Most commands support `visibility: public` or `visibility: private`.

- AI commands default to private
- Utility and social commands default to public
- `/ask` keeps short-lived memory per channel for follow-ups

## Commands

### General
- `/help` - Show the command guide
- `/choose` - Pick one option from a comma-separated list

### AI
- `/ask` - Ask a question with Discord-friendly answers
- `/rewrite` - Rewrite text in a chosen tone
- `/explain` - Explain text more clearly
- `/translate` - Translate text into another language
- `/img` - Generate an image from a prompt
- `Rewrite Message` - Right-click a message to rewrite it

### Food
- `/eats` - Find nearby restaurants
- `/addy` - Look up a restaurant address

## Notes

- `/ask` is tuned for short follow-ups in the same channel
- `/img` can fall back to an attached image if the API does not return a URL
- Slash commands are synced on startup
- The bot uses a shared `aiohttp` session

## Environment Variables

### Required
- `DISCORD_BOT_API_KEY`
- `CHATGPT_API_KEY`

### Optional
- `GOOGLE_GEO_PLACES_API_KEY`
- `GUILD_ID`
- `OPENAI_CHAT_MODEL`
- `OPENAI_IMAGE_MODEL`
- `BOT_STATUS_TEXT`

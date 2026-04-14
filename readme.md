# The James Bot

A Discord bot built around **slash commands with optional visibility control**.

## Interaction Model

All features are exposed via `/commands`.

Each command may optionally specify:
- `visibility: public`
- `visibility: private`

### Default behavior

| Command Type | Default Visibility |
|------------|------------------|
| Help / AI | Private |
| Utility / Social | Public |

---

## Commands

### `/help`
Show command guide.

Default: **private**

---

### `/choose`
Pick one option from a comma-separated list.

Example:
/choose choices:pizza, sushi, burgers


---

### `/ask`
Ask the bot a question (OpenAI).

Supports **reply context**:
- Reply to a message, then run `/ask`
- The bot will use that message as context

Example:
/ask prompt:explain this better


---

### `/img`
Generate an image from a prompt.

Example:
/img prompt:cyberpunk city at night



---

### `/eats`
Find restaurants near a location.

Example:
/eats city:Fremont radius:5 category:sushi


---

### `/addy`
Find restaurant address + Google Maps link.

Example:
/addy restaurant:In-N-Out city:Fremont


---

## Features

- OpenAI text + image generation
- Google Places integration
- Automatic RedNote/Xiaohongshu link previews
- Visibility-aware responses (public/private)
- Reply-aware AI commands

---

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

---

## Notes

- Slash commands are synced on startup
- Bot uses a shared `aiohttp` session
- Clean shutdown implemented via subclassed bot
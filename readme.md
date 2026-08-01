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

### Finance
- `/finance` - Show ETF/Bitcoin changes, upcoming US economic events, headlines, and card categories
- `/watch add` - Add a ticker and optional company name to the shared server watchlist
- `/watch remove` - Remove a ticker from the watchlist
- `/watch list` - Show the server watchlist
- Posts a Friday digest and Mon–Thu threshold alerts at 5 PM Eastern when `FINANCE_CHANNEL_ID` is set and the bot is online

## Notes

- `/ask` is tuned for short follow-ups in the same channel
- `/img` can fall back to an attached image if the API does not return a URL
- Finance data comes from Alpha Vantage, Finnhub, CoinGecko, CNBC, the New York Times, BBC, and the Federal Reserve
- News is limited to the last 72 hours, sorted newest-first, and deduplicated across sources and scheduled posts
- One finance-news section prioritizes watchlist and US rate-cut/FOMC headlines; watchlists persist per server in SQLite
- Current and disclosed upcoming Chase Freedom categories refresh from Chase's calendar; active Ultimate Rewards transfer bonuses come from a current-month third-party tracker and should be verified in Chase
- Market data may be delayed; ETF prices are proxies and output is informational, not investment advice
- Finance requests are cached for 5 minutes and `/finance` is limited to once per user per minute
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
- `FINANCE_CHANNEL_ID` - Channel for scheduled digests and alerts
- `ALPHA_VANTAGE_API_KEY` - Enables weekly ETF data
- `FINNHUB_API_KEY` - Enables SPY alerts; economic calendar access may require a paid Finnhub plan

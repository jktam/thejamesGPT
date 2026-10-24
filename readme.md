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
- `/finance` - Show ETF/Bitcoin and watchlist performance, upcoming US events, headlines, and Chase rewards
- `/finance_status` - Server managers: inspect the last weekday check, scheduler state, and source failures
- `/watch add` - Add a ticker and optional company name for news and price tracking (25 per server)
- `/watch remove` - Remove a ticker from the watchlist
- `/watch list` - Show the server watchlist
- Posts a Friday digest and Mon–Thu market, economic-event, and material-earnings alerts at 5 PM Eastern when `FINANCE_CHANNEL_ID` is set and the bot is online

## Notes

- `/ask` is tuned for short follow-ups in the same channel
- `/img` can fall back to an attached image if the API does not return a URL
- Finance data comes from Alpha Vantage, Finnhub, CoinGecko, CNBC, the New York Times, BBC, and the Federal Reserve
- General news covers 72 hours; targeted Finnhub company news covers 7 days; earnings alerts cover 24 hours. Scheduled posts deduplicate previously sent headlines
- News prioritizes watched companies and US rate/FOMC coverage, with at most two headlines per publisher. Common words like "cost" no longer count as tickers; known company aliases also work for existing ticker-only entries
- Mon–Thu alerts include watched stocks moving at least 3% from the previous close, once per symbol/date. Checks run at 5 PM Eastern, not continuously; unavailable quotes are reported
- Friday lists every watched stock's current price and daily move. Weekly price changes use saved prior-week closes (unadjusted for splits); until a baseline exists, they show "week pending". Keep `data/finance.db` across deploys
- Current/upcoming Freedom 5% categories refresh from Chase; Ultimate Rewards bonuses come from PointsYeah's current offers. If verification fails, the Chase section stays visible with source links. Activate categories and verify transfer offers in Chase
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
- `FINNHUB_API_KEY` - Enables SPY/watchlist quotes and targeted company news; economic calendar access may require a paid plan. Requests are paced below 60/minute and cached for five minutes

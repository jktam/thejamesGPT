from __future__ import annotations

import logging

from config import SETTINGS
from core_bot import JamesBot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    bot = JamesBot(SETTINGS)
    bot.run(SETTINGS.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
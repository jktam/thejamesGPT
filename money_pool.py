# money_pool.py
import json
import asyncio
import atexit
from pathlib import Path
from typing import Dict, Optional

FILEPATH = Path("money_pool.json")
AUTOSAVE_INTERVAL = 300  # seconds
DEFAULT_START_BALANCE = 1000

class MoneyPool:
    def __init__(self, path: Path = FILEPATH):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Dict] = {}
        self._autosave_task: Optional[asyncio.Task] = None

    async def load(self):
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    async def start_autosave(self, loop: asyncio.AbstractEventLoop):
        if self._autosave_task:
            return
        self._autosave_task = loop.create_task(self._autosave_loop())

    async def _autosave_loop(self):
        while True:
            await asyncio.sleep(AUTOSAVE_INTERVAL)
            await self.save()

    async def save(self):
        async with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)

    def save_sync(self):
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    async def ensure_user(self, user_id: str):
        async with self._lock:
            if user_id not in self._data:
                self._data[user_id] = {"current": DEFAULT_START_BALANCE, "historical_high": DEFAULT_START_BALANCE}

    async def get_balance(self, user_id: str) -> int:
        async with self._lock:
            return self._data.get(user_id, {}).get("current", DEFAULT_START_BALANCE)

    async def add(self, user_id: str, amount: int):
        async with self._lock:
            if user_id not in self._data:
                self._data[user_id] = {"current": DEFAULT_START_BALANCE, "historical_high": DEFAULT_START_BALANCE}
            self._data[user_id]["current"] += amount
            if self._data[user_id]["current"] > self._data[user_id]["historical_high"]:
                self._data[user_id]["historical_high"] = self._data[user_id]["current"]

    async def subtract(self, user_id: str, amount: int) -> bool:
        async with self._lock:
            if user_id not in self._data:
                self._data[user_id] = {"current": DEFAULT_START_BALANCE, "historical_high": DEFAULT_START_BALANCE}
            if self._data[user_id]["current"] < amount:
                return False
            self._data[user_id]["current"] -= amount
            return True

    async def set_balance(self, user_id: str, amount: int):
        async with self._lock:
            self._data[user_id] = {"current": amount, "historical_high": max(self._data.get(user_id, {}).get("historical_high", 0), amount)}

    async def get_leaderboard(self, limit: int = 10):
        async with self._lock:
            items = sorted(self._data.items(), key=lambda kv: kv[1].get("historical_high", 0), reverse=True)
            return items[:limit]

# Singleton accessor + atexit sync
_pool_singleton: Optional[MoneyPool] = None

def get_money_pool_singleton() -> MoneyPool:
    global _pool_singleton
    if _pool_singleton is None:
        _pool_singleton = MoneyPool()
        atexit.register(_pool_singleton.save_sync)
    return _pool_singleton

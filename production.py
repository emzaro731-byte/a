import asyncio
import os
import time
from collections import defaultdict

FAILURE_THRESHOLD = max(1, int(os.getenv("AI_FAILURE_THRESHOLD", "3")))
COOLDOWN_SECONDS = max(1, int(os.getenv("AI_PROVIDER_COOLDOWN_SECONDS", "30")))

_failures: dict[str, int] = defaultdict(int)
_open_until: dict[str, float] = {}


def provider_available(provider: str) -> bool:
    until = _open_until.get(provider, 0)
    if until <= time.time():
        if until:
            _open_until.pop(provider, None)
            _failures[provider] = 0
        return True
    return False


def record_success(provider: str) -> None:
    _failures[provider] = 0
    _open_until.pop(provider, None)


def record_failure(provider: str) -> None:
    _failures[provider] += 1
    if _failures[provider] >= FAILURE_THRESHOLD:
        _open_until[provider] = time.time() + COOLDOWN_SECONDS


async def retry_async(operation, attempts: int = 3, base_delay: float = 0.5):
    last = None
    for index in range(max(1, attempts)):
        try:
            return await operation()
        except Exception as exc:
            last = exc
            if index + 1 < attempts:
                await asyncio.sleep(base_delay * (2 ** index))
    raise last

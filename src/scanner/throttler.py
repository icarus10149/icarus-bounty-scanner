# src/scanner/throttler.py
from aiolimiter import AsyncLimiter
from typing import Dict, Any


class ProgramThrottler:
    def __init__(self, config: Dict[str, Any]):
        self.default_rps = float(config["default_rps"])
        self.overrides = config.get("program_overrides", {})

        # Cache created limiters
        self._limiters: Dict[str, AsyncLimiter] = {}

    def get(self, program: str) -> AsyncLimiter:
        if program in self._limiters:
            return self._limiters[program]

        rps = float(self.overrides.get(program, {}).get("rps", self.default_rps))
        limiter = AsyncLimiter(rps, 1)
        self._limiters[program] = limiter
        return limiter

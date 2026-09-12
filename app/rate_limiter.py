import time
from collections import defaultdict
from typing import Dict, List
from fastapi import HTTPException, Request, status
from app.config import settings


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter per client key."""

    def __init__(self, max_requests: int = settings.RATE_LIMIT_PER_MINUTE, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, client_key: str = "global") -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old timestamps
        timestamps = [ts for ts in self.requests[client_key] if ts > cutoff]
        self.requests[client_key] = timestamps

        if len(timestamps) >= self.max_requests:
            return False

        self.requests[client_key].append(now)
        return True

    def check_rate_limit(self, client_key: str = "global"):
        if not self.is_allowed(client_key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: maximum {self.max_requests} queries per {self.window_seconds} seconds.",
            )


# Global rate limiter
rate_limiter = SlidingWindowRateLimiter()


async def rate_limit_dependency(request: Request):
    """FastAPI dependency to enforce rate limiting by client IP."""
    client_ip = request.client.host if request.client else "anonymous"
    rate_limiter.check_rate_limit(client_ip)

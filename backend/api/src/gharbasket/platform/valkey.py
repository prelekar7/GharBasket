"""Valkey client (Redis protocol).

Used for rate limits, the session deny-list, caching and, later, the Taskiq queue.
"""

from redis.asyncio import Redis


def create_valkey(url: str) -> Redis:
    # Short timeouts: a slow Valkey must fail fast instead of holding up requests.
    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)

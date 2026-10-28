"""The Redis client, built once per process (DI-01), with the standard's timeouts (RDS-07)."""

from redis.asyncio import Redis

from ai_gateway.core.config import RedisSettings


def create_redis(settings: RedisSettings) -> Redis:
    return Redis.from_url(
        settings.url.get_secret_value(),
        socket_timeout=2,
        socket_connect_timeout=0.5,
        health_check_interval=30,
        decode_responses=True,
    )

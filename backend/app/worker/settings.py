from urllib.parse import unquote

from arq.connections import RedisSettings

from app.core.config import get_settings


async def ping(ctx: dict[str, object]) -> str:
    return "pong"


def redis_settings_from_url(url: str) -> RedisSettings:
    settings = RedisSettings.from_dsn(url)
    if settings.username is not None:
        settings.username = unquote(settings.username)
    if settings.password is not None:
        settings.password = unquote(settings.password)
    if settings.ssl:
        settings.ssl_check_hostname = True
    return settings


class WorkerSettings:
    functions = (ping,)
    redis_settings = redis_settings_from_url(get_settings().redis_url.get_secret_value())
    health_check_interval = 10

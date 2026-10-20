import pytest

from app.worker.settings import redis_settings_from_url


@pytest.mark.parametrize(
    "url,username,password,ssl",
    [
        ("redis://cache:6380/2", None, None, False),
        ("redis://worker:plain@cache:6380/2", "worker", "plain", False),
        (
            "rediss://worker%2Bname:p%40ss%3Aword%25@cache:6380/2",
            "worker+name",
            "p@ss:word%",
            True,
        ),
    ],
)
def test_worker_redis_credentials_are_decoded(
    url: str, username: str | None, password: str | None, ssl: bool
) -> None:
    settings = redis_settings_from_url(url)
    assert settings.username == username
    assert settings.password == password
    assert settings.ssl is ssl
    if ssl:
        assert settings.ssl_check_hostname
    assert settings.host == "cache"
    assert settings.port == 6380
    assert settings.database == 2

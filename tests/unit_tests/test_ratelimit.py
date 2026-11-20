import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.ratelimit import (
    Limit,
    RateLimiter,
    client_ip,
    environ_headers,
    limit_message,
    parse_limit,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(("raw", "expected"), [
    ("20/600", Limit(20, 600.0)), ("5", Limit(5, 60.0)), (None, Limit(20, 600.0)), ("", Limit(20, 600.0)),
    ("0", None),
])
def test_parse_limit(raw: str | None, expected: Limit | None) -> None:
    assert parse_limit(raw) == expected


def test_parse_limit_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        parse_limit("-1/60")


def test_per_ip_sliding_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(Limit(3, 60), None, clock=clock)
    assert all(limiter.check("1.1.1.1").allowed for _ in range(3))
    denied = limiter.check("1.1.1.1")
    assert not denied.allowed and denied.scope == "ip" and 0 < denied.retry_after_s <= 60
    assert limiter.check("2.2.2.2").allowed  # IP lain tidak terpengaruh
    clock.now += 61
    assert limiter.check("1.1.1.1").allowed


def test_denied_request_is_not_counted() -> None:
    clock = FakeClock()
    limiter = RateLimiter(Limit(1, 60), None, clock=clock)
    assert limiter.check("ip").allowed
    for _ in range(5):
        assert not limiter.check("ip").allowed
    clock.now += 60.5
    assert limiter.check("ip").allowed


def test_global_daily_limit_blocks_everyone() -> None:
    clock = FakeClock()
    limiter = RateLimiter(Limit(100, 60), 2, clock=clock)
    assert limiter.check("a").allowed and limiter.check("b").allowed
    denied = limiter.check("c")
    assert not denied.allowed and denied.scope == "global"
    clock.now += 86_401
    assert limiter.check("c").allowed


def test_disabled_limiter_always_allows() -> None:
    limiter = RateLimiter(None, None)
    assert all(limiter.check("x").allowed for _ in range(1000))


def test_client_ip_trusts_proxy_headers_only_when_configured() -> None:
    headers = {"X-Forwarded-For": "9.9.9.9, 10.0.0.1", "CF-Connecting-IP": "8.8.8.8"}
    assert client_ip(headers, "10.0.0.1", trust_proxy=False) == "10.0.0.1"
    assert client_ip(headers, "10.0.0.1", trust_proxy=True) == "8.8.8.8"
    assert client_ip({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "10.0.0.1", trust_proxy=True) == "9.9.9.9"
    assert client_ip({}, None, trust_proxy=True) == "unknown"


def test_environ_headers_from_socket_environ() -> None:
    environ = {"REMOTE_ADDR": "1.2.3.4", "HTTP_X_FORWARDED_FOR": "5.6.7.8", "HTTP_CF_CONNECTING_IP": "7.7.7.7"}
    assert environ_headers(environ) == {"x-forwarded-for": "5.6.7.8", "cf-connecting-ip": "7.7.7.7"}


def test_limit_message_mentions_wait_time() -> None:
    limiter = RateLimiter(Limit(1, 600), None)
    limiter.check("x")
    msg = limit_message(limiter.check("x"))
    assert "minute" in msg


def test_api_returns_429_after_limit(no_database, fake_llm, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm("Hello! I can help with DarGlobal projects.", "Hello again.")
    limiter = RateLimiter(Limit(1, 600), None)
    monkeypatch.setattr("server.chat.get_limiter", lambda: limiter)
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"message": "hi"})
        second = client.post("/api/chat", json={"message": "hi again"})
    assert first.status_code == 200
    assert second.status_code == 429
    body = second.json()
    assert body["error"] and body["retry_after_s"] > 0
    assert int(second.headers["retry-after"]) == body["retry_after_s"]

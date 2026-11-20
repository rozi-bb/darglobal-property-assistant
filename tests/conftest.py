import os
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

# Unit test tidak boleh kirim trace / pakai DB asli.
os.environ["LANGSMITH_TRACING"] = "false"


class FakeToolModel(GenericFakeChatModel):
    """Fake chat model yang bisa dipakai `create_agent` (bind_tools tidak mengubah apa-apa)."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolModel":
        return self


def make_tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    """Pesan AI yang memanggil satu tool."""
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


@pytest.fixture
def tool_call() -> Callable[..., AIMessage]:
    return make_tool_call


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit test tanpa API luar: tanpa rerank Jev, tanpa model cadangan."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("agent.graph.get_fallback_llm", lambda: None)
    # Rate limit mati di test (test rate limit memasang limiter sendiri)
    from server.ratelimit import RateLimiter

    monkeypatch.setattr("server.ratelimit.get_limiter", lambda: RateLimiter(None, None))
    monkeypatch.setattr("server.chat.get_limiter", lambda: RateLimiter(None, None))
    monkeypatch.setattr("server.ui.get_limiter", lambda: RateLimiter(None, None))


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeToolModel]:
    """Ganti LLM asli dengan fake model yang membalas pesan tetap (teks atau tool call)."""

    def _install(*replies: str | AIMessage) -> FakeToolModel:
        model = FakeToolModel(messages=iter([r if isinstance(r, AIMessage) else AIMessage(r) for r in replies]))
        monkeypatch.setattr("agent.graph.get_llm", lambda: model)
        return model

    return _install


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Paksa checkpointer in-memory (tanpa Postgres)."""
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "")
    yield

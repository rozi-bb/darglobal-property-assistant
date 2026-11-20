import os
from collections.abc import Callable, Iterator

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

# Unit test tidak boleh kirim trace / pakai DB asli.
os.environ["LANGSMITH_TRACING"] = "false"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., GenericFakeChatModel]:
    """Ganti LLM OpenRouter dengan fake model yang membalas teks tetap."""

    def _install(*replies: str) -> GenericFakeChatModel:
        model = GenericFakeChatModel(messages=iter([AIMessage(r) for r in replies]))
        monkeypatch.setattr("agent.nodes.get_llm", lambda: model)
        return model

    return _install


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Paksa checkpointer in-memory (tanpa Postgres)."""
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "")
    yield

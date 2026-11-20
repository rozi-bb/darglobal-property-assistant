import json

from fastapi.testclient import TestClient

from server.main import app
from server.runtime import get_graph


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_health(no_database) -> None:
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_root_redirects_to_ui(no_database) -> None:
    with TestClient(app) as client:
        res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/chat"


def test_case_study_is_served(no_database) -> None:
    with TestClient(app) as client:
        redirect = client.get("/case-study", follow_redirects=False)
        page = client.get("/case-study/")
        image = client.get("/case-study/chat-stats.png")
        pdf = client.get("/case-study/Rozi-DarGlobal-case-study.pdf")
    assert redirect.status_code in (302, 307)
    assert redirect.headers["location"] == "/case-study/"
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


def test_chat_sends_final_answer(no_database, fake_llm) -> None:
    fake_llm("Trump Tower Jeddah is a DarGlobal project")
    with TestClient(app) as client:
        res = client.post("/api/chat", json={"message": "What is Trump Tower Jeddah?"})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "end"
    assert kinds.count("answer") == 1  # jawaban final dikirim utuh setelah lolos cek
    assert "error" not in kinds
    assert dict(events)["answer"]["text"]
    assert events[0][1]["thread_id"]


def test_chat_keeps_memory_per_thread(no_database, fake_llm) -> None:
    fake_llm("first", "second")
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": "one", "thread_id": "abc"})
        client.post("/api/chat", json={"message": "two", "thread_id": "abc"})
        state = get_graph().get_state({"configurable": {"thread_id": "abc"}})
    assert len(state.values["messages"]) == 4


def test_chat_rejects_bad_input(no_database) -> None:
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"message": ""}).status_code == 422
        assert client.post("/api/chat", json={"message": "hi", "thread_id": "../etc"}).status_code == 422


def test_chat_llm_error_is_friendly(no_database, monkeypatch) -> None:
    def boom():
        raise RuntimeError("LLM_API_KEY belum diisi")

    monkeypatch.setattr("agent.graph.get_llm", boom)
    with TestClient(app) as client:
        res = client.post("/api/chat", json={"message": "hi"})
    kinds = [e for e, _ in _events(res.text)]
    assert "error" in kinds and kinds[-1] == "end"
    assert "LLM_API_KEY" not in res.text  # detail internal tidak bocor ke user


def test_chainlit_ui_settings_load(no_database, monkeypatch) -> None:
    # Regression: Chainlit menyalakan data layer sendiri kalau ada env DATABASE_URL.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        res = client.get("/chat/project/settings?language=en-US")
    assert res.status_code == 200
    assert res.json()["ui"]["name"] == "DarGlobal Property Assistant"


def test_chat_ask_event_then_resume(no_database, fake_llm, tool_call) -> None:
    fake_llm(tool_call("ask_user", {"question": "Which city?", "options": ["Riyadh", "Jeddah"]}),
             "Thanks, noted: Riyadh.")
    with TestClient(app) as client:
        first = _events(client.post("/api/chat", json={"message": "villas?", "thread_id": "q1"}).text)
        second = _events(client.post("/api/chat", json={"message": "Riyadh", "thread_id": "q1"}).text)
    assert ("ask", {"question": "Which city?", "options": ["Riyadh", "Jeddah"]}) in first
    assert "answer" not in dict(first)
    assert dict(second)["answer"]["text"]

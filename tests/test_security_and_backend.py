from __future__ import annotations

import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from campsage.answer import DRIFT_LIMIT, DRIFT_NUDGE, OFFTOPIC_ANSWER
from campsage.app import MAX_AUDIO_BYTES, MIN_AUDIO_BYTES, create_app
from campsage.gemma import LIVE_MODEL_ID, GemmaClient, ModelResult
from campsage.ingest import Chunk
from campsage.keystore import KeyStore, encryption_key_from_password, hash_password, verify_password
from campsage.retrieve import retrieve
from campsage.settings import Settings


class FakeModel:
    """Stands in for GemmaClient: generate(api_key, prompt, system_instruction) and transcribe(api_key, pcm)."""

    def __init__(self, response: str, transcript: str = "what is my fee deadline"):
        self.response = response
        self.transcript = transcript
        self.calls: list[str] = []
        self.system_instructions: list[str | None] = []
        self.transcribed: list[bytes] = []

    def validate_key(self, api_key: str):
        assert api_key == "student-key"
        return ModelResult("ok", "gemma-test")

    def generate(self, api_key: str, prompt: str, system_instruction: str | None = None):
        assert api_key == "student-key"
        self.calls.append(prompt)
        self.system_instructions.append(system_instruction)
        source_id = re.search(r"\[([^]]+)\] SOURCE=", prompt).group(1)
        return ModelResult(self.response.replace("SOURCE_ID", source_id), "gemma-test")

    def transcribe(self, api_key: str, pcm: bytes) -> str:
        assert api_key == "student-key"
        self.transcribed.append(pcm)
        return self.transcript


def login_with_key(app, student_id="demo-a", password="camp-a-2026"):
    client = TestClient(app)
    assert client.post("/api/login", json={"student_id": student_id, "password": password}).status_code == 200
    assert client.post("/api/connect-key", json={"api_key": "student-key"}).status_code == 200
    return client


def settings(tmp_path):
    return Settings(
        database_path=tmp_path / "camp.sqlite3",
        index_path=tmp_path / "index.json",
    )


def test_password_hash_and_encryption_are_not_plaintext(tmp_path):
    password = "camp-a-2026"
    encoded = hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password("wrong", encoded)

    store = KeyStore(tmp_path / "keys.sqlite3")
    key = encryption_key_from_password(password, b"0123456789abcdef")
    store.connect_key("demo-a", key, "student-key")
    raw = repr(store.raw_row("demo-a"))
    assert "student-key" not in raw


def test_retrieval_keeps_public_and_current_student_context_only():
    chunks = [Chunk("fee-1", "fee_notice.txt", None, "Fee payment", "clear semester fee by 30 October")]
    result = retrieve("fee payment", {}, {"fees": {"status": "partial"}}, chunks)
    assert result[0].source == "fee_notice.txt"
    assert any(item.kind == "student_record" for item in result)
    assert all("other student" not in item.text for item in result)


def test_retrieval_ignores_question_stop_words(tmp_path):
    chunks = [
        Chunk("attendance-1", "attendance_notice.txt", None, "Attendance", "Attendance below the threshold affects eligibility."),
        Chunk("fee-1", "fee_notice.txt", None, "Fee payment", "The fee deadline is 30 October."),
    ]
    result = retrieve("What is my attendance?", {}, {"attendance": {"overall": "82%"}}, chunks)
    assert result[0].source == "attendance_notice.txt"
    assert all(item.source != "fee_notice.txt" for item in result)


def test_login_key_connect_relogin_and_grounded_answer(tmp_path):
    fake = FakeModel("Your fee status is partial and 18,500 rupees is outstanding.")
    app = create_app(settings(tmp_path), fake)
    client = TestClient(app)

    response = client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    assert response.status_code == 200
    assert response.json()["needs_key"] is True
    assert "campsage_session" in response.cookies

    response = client.post("/api/connect-key", json={"api_key": "student-key"})
    assert response.status_code == 200
    assert "student-key" not in response.text

    response = client.post("/api/ask", json={"question": "What is my fee deadline?"})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["sources"]

    client.post("/api/logout")
    response = client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    assert response.json()["needs_key"] is False


def test_model_refusal_is_reported_as_offtopic_without_sources(tmp_path):
    refusal = "I'd rather keep our chat to college and study topics. I can help with your attendance and fees."
    fake = FakeModel(refusal)
    client = login_with_key(create_app(settings(tmp_path), fake))

    body = client.post("/api/ask", json={"question": "Do you have a girlfriend?"}).json()

    assert body["status"] == "offtopic"
    assert body["sources"] == []
    assert body["answer"].startswith("I'd rather keep")


def test_punctuation_does_not_make_attendance_off_topic(tmp_path):
    fake = FakeModel("Your attendance is shown in your student record.")
    app = create_app(settings(tmp_path), fake)
    client = TestClient(app)
    client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    client.post("/api/connect-key", json={"api_key": "student-key"})

    response = client.post("/api/ask", json={"question": "What is my attendance?"})

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert fake.calls


def test_plain_model_reply_is_grounded_without_json(tmp_path):
    fake = FakeModel("The fee deadline is 30 October [SOURCE_ID].")
    app = create_app(settings(tmp_path), fake)
    client = TestClient(app)
    client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    client.post("/api/connect-key", json={"api_key": "student-key"})

    response = client.post("/api/ask", json={"question": "What is my fee deadline?"})

    assert response.json()["answer"].startswith("The fee deadline")
    assert response.json()["sources"]


def test_live_adapter_returns_transcript_and_json_safe_pcm(monkeypatch):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send_client_content(self, **kwargs):
            self.turn = kwargs

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="hello"),
                    model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"\x01\x02"))]),
                    turn_complete=True,
                )
            )

    class FakeClient:
        def __init__(self):
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=self.connect))
            self.model = None
            self.config = None

        def connect(self, *, model, config):
            self.model = model
            self.config = config
            return FakeSession()

    fake_client = FakeClient()
    client = GemmaClient(timeout_ms=10_000)
    monkeypatch.setattr(client, "_client", lambda api_key: fake_client)

    result = client.generate("student-key", "say hello")

    assert result.text == "hello"
    assert result.audio_bytes == b"\x01\x02"
    assert result.audio_base64 == "AQI="
    assert fake_client.model == LIVE_MODEL_ID
    assert fake_client.config.output_audio_transcription is not None
    assert fake_client.config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"


def test_general_knowledge_question_is_answered_as_general(tmp_path):
    fake = FakeModel("New Delhi is the capital of India.")
    client = login_with_key(create_app(settings(tmp_path), fake))

    response = client.post("/api/ask", json={"question": "What is the capital of India?"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "general"
    assert body["answer"] == "New Delhi is the capital of India."
    assert body["sources"] == []
    assert len(fake.calls) == 1
    assert fake.system_instructions[0] and "CampSage" in fake.system_instructions[0]


def test_unsupported_campus_claim_is_downgraded_to_unknown(tmp_path):
    fake = FakeModel("The hostel curfew is at nine in the evening.")
    client = login_with_key(create_app(settings(tmp_path), fake))

    body = client.post("/api/ask", json={"question": "What is the hostel curfew?"}).json()

    assert body["status"] == "unknown"
    assert body["sources"] == []
    assert body["audio_base64"] == ""


def test_repeated_off_topic_turns_trigger_polite_refusal(tmp_path):
    fake = FakeModel("New Delhi is the capital of India.")
    client = login_with_key(create_app(settings(tmp_path), fake))

    for _ in range(DRIFT_LIMIT):
        assert client.post("/api/ask", json={"question": "What is the capital of India?"}).json()["status"] == "general"
    assert len(fake.calls) == DRIFT_LIMIT

    body = client.post("/api/ask", json={"question": "What is the capital of India?"}).json()
    assert body["status"] == "offtopic"
    assert body["answer"] == OFFTOPIC_ANSWER
    assert body["sources"] == []
    assert len(fake.calls) == DRIFT_LIMIT  # refused without calling the model

    # A campus question still gets through and resets the drift counter.
    fake.response = "Your attendance is 82% overall."
    body = client.post("/api/ask", json={"question": "What is my attendance?"}).json()
    assert body["status"] == "answered"
    assert len(fake.calls) == DRIFT_LIMIT + 1
    fake.response = "New Delhi is the capital of India."
    assert client.post("/api/ask", json={"question": "What is the capital of India?"}).json()["status"] == "general"


def test_drift_nudge_is_added_to_prompt_after_several_off_topic_turns(tmp_path):
    fake = FakeModel("New Delhi is the capital of India.")
    client = login_with_key(create_app(settings(tmp_path), fake))

    for _ in range(DRIFT_NUDGE + 1):
        client.post("/api/ask", json={"question": "What is the capital of India?"})

    assert "gently invite them back" not in fake.calls[0]
    assert "gently invite them back" in fake.calls[DRIFT_NUDGE]


def test_follow_up_question_uses_conversation_history(tmp_path):
    fake = FakeModel("Your DBMS attendance is 78%.")
    client = login_with_key(create_app(settings(tmp_path), fake))

    client.post("/api/ask", json={"question": "What is my attendance in DBMS?"})
    fake.response = "Your overall attendance is 82%."
    client.post("/api/ask", json={"question": "and overall?"})

    assert "(this is the first message)" in fake.calls[0]
    assert "Student: What is my attendance in DBMS?" in fake.calls[1]
    assert "CampSage: Your DBMS attendance is 78%." in fake.calls[1]
    assert "and overall?" in fake.calls[1]


def test_session_history_clears_on_logout(tmp_path):
    fake = FakeModel("Your attendance is 82% overall.")
    app = create_app(settings(tmp_path), fake)
    client = login_with_key(app)
    cookie = client.cookies.get("campsage_session")

    client.post("/api/ask", json={"question": "What is my attendance?"})
    session = app.state.sessions.get(cookie)
    assert len(session.history) == 2

    client.post("/api/logout")
    assert session.history == []
    assert session.api_key is None
    assert app.state.sessions.get(cookie) is None

    # The next login starts with a clean conversation.
    client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    client.post("/api/ask", json={"question": "What is my attendance?"})
    assert "(this is the first message)" in fake.calls[-1]


PCM_OK = b"\x01\x00" * (MIN_AUDIO_BYTES // 2 + 100)


def post_pcm(client, body: bytes):
    return client.post("/api/transcribe", content=body, headers={"Content-Type": "application/octet-stream"})


def test_transcribe_requires_session(tmp_path):
    fake = FakeModel("unused")
    client = TestClient(create_app(settings(tmp_path), fake))

    response = post_pcm(client, PCM_OK)

    assert response.status_code == 401
    assert response.json()["ok"] is False
    assert fake.transcribed == []


def test_transcribe_requires_connected_key(tmp_path):
    fake = FakeModel("unused")
    client = TestClient(create_app(settings(tmp_path), fake))
    client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})

    response = post_pcm(client, PCM_OK)

    assert response.status_code == 400
    assert fake.transcribed == []


def test_transcribe_rejects_oversize_body(tmp_path):
    fake = FakeModel("unused")
    client = login_with_key(create_app(settings(tmp_path), fake))

    response = post_pcm(client, b"\x00\x00" * (MAX_AUDIO_BYTES // 2 + 1))

    assert response.status_code == 413
    assert response.json()["ok"] is False
    assert fake.transcribed == []


def test_transcribe_rejects_too_short_and_odd_length_bodies(tmp_path):
    fake = FakeModel("unused")
    client = login_with_key(create_app(settings(tmp_path), fake))

    for body in (b"", b"\x00\x00" * 10, b"\x00" * (MIN_AUDIO_BYTES - 2)):
        response = post_pcm(client, body)
        assert response.status_code == 400
        assert response.json()["ok"] is False

    odd = post_pcm(client, b"\x00" * (MIN_AUDIO_BYTES + 1))
    assert odd.status_code == 400
    assert odd.json()["ok"] is False
    assert fake.transcribed == []


def test_transcribe_returns_text_from_client(tmp_path):
    fake = FakeModel("unused", transcript="what is my fee deadline")
    client = login_with_key(create_app(settings(tmp_path), fake))

    response = post_pcm(client, PCM_OK)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "text": "what is my fee deadline"}
    assert fake.transcribed == [PCM_OK]


def test_transcribe_failure_is_reported_without_leaking_details(tmp_path):
    fake = FakeModel("unused")

    def boom(api_key, pcm):
        raise RuntimeError("secret-internal-detail student-key")

    fake.transcribe = boom
    client = login_with_key(create_app(settings(tmp_path), fake))

    response = post_pcm(client, PCM_OK)

    assert response.status_code == 502
    assert response.json()["ok"] is False
    assert "secret-internal-detail" not in response.text
    assert "student-key" not in response.text


def test_wrong_password_and_unknown_student_are_rejected(tmp_path):
    client = TestClient(create_app(settings(tmp_path), FakeModel("unused")))

    for student_id, password in (("demo-a", "wrong"), ("demo-a", "camp-b-2026"), ("nobody", "camp-a-2026"), ("", "")):
        response = client.post("/api/login", json={"student_id": student_id, "password": password})
        assert response.status_code == 401
        assert response.json()["ok"] is False
        assert "campsage_session" not in response.cookies
    assert client.get("/api/me").json() == {"logged_in": False}
    assert client.post("/api/ask", json={"question": "What is my attendance?"}).status_code == 401


def test_students_see_only_their_own_records_history_and_key(tmp_path):
    fake = FakeModel("Your attendance is 82% overall.")
    app = create_app(settings(tmp_path), fake)
    client_a = login_with_key(app)
    client_a.post("/api/ask", json={"question": "What is my fee outstanding?"})

    # Same app, second student: nothing of student A may reach B's prompt.
    client_b = TestClient(app)
    login = client_b.post("/api/login", json={"student_id": "demo-b", "password": "camp-b-2026"}).json()
    assert login["needs_key"] is True  # A's connected key is not B's
    assert login["profile"]["name"] != client_a.get("/api/me").json()["profile"]["name"]
    client_b.post("/api/connect-key", json={"api_key": "student-key"})
    client_b.post("/api/ask", json={"question": "What is my attendance?"})

    prompt_a, prompt_b = fake.calls[0], fake.calls[1]
    assert "18,500" in prompt_a
    assert "Aarav" not in prompt_a and "Zoya" not in prompt_b  # names never reach the model
    for secret in ("18,500", "Aarav", "CSE-A", "Data Mining", "demo-a", "camp-a-2026"):
        assert secret not in prompt_b
    assert "Student: What is my fee outstanding?" not in prompt_b
    assert "(this is the first message)" in prompt_b
    # A forged session cookie gets nowhere.
    forged = TestClient(app)
    forged.cookies.set("campsage_session", "not-a-real-session")
    assert forged.post("/api/ask", json={"question": "What is my attendance?"}).status_code == 401

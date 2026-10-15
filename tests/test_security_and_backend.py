from __future__ import annotations

import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from campsage.app import create_app
from campsage.gemma import LIVE_MODEL_ID, GemmaClient, ModelResult
from campsage.ingest import Chunk
from campsage.keystore import KeyStore, encryption_key_from_password, hash_password, verify_password
from campsage.retrieve import retrieve
from campsage.settings import Settings


class FakeModel:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def validate_key(self, api_key: str):
        assert api_key == "student-key"
        return ModelResult("ok", "gemma-test")

    def generate(self, api_key: str, prompt: str):
        assert api_key == "student-key"
        self.calls.append(prompt)
        source_id = re.search(r"\[([^]]+)\] SOURCE=", prompt).group(1)
        return ModelResult(self.response.replace("SOURCE_ID", source_id), "gemma-test")


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
    fake = FakeModel('{"status":"answered","answer":"The fee deadline is in the notice.","sources":[{"source_id":"SOURCE_ID"}]}')
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


def test_off_topic_question_does_not_call_model(tmp_path):
    fake = FakeModel('{"status":"answered","answer":"wrong","sources":[]}')
    app = create_app(settings(tmp_path), fake)
    client = TestClient(app)
    client.post("/api/login", json={"student_id": "demo-a", "password": "camp-a-2026"})
    client.post("/api/connect-key", json={"api_key": "student-key"})
    response = client.post("/api/ask", json={"question": "What is the capital of France?"})
    assert response.json()["status"] == "offtopic"
    assert fake.calls == []


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

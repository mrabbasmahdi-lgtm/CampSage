from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from campsage.app import create_app
from campsage.gemma import ModelResult
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
        model_id="gemma-test",
        fallback_model_ids=(),
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

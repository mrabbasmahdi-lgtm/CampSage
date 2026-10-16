"""FastAPI entrypoint for the local CampSage prototype."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .answer import AnswerResult, answer_question
from .college import DemoCollegeProvider, make_demo_students
from .gemma import GemmaClient, classify_error
from .ingest import build_index, load_index
from .keystore import KeyStore
from .retrieve import retrieve
from .session import SessionStore
from .settings import ROOT, Settings


# 16 kHz mono PCM16 is 32,000 bytes a second: allow 0.1 s to about 30 s of speech.
MIN_AUDIO_BYTES = 3_200
MAX_AUDIO_BYTES = 1_000_000
# Fields that are identity or credentials, never evidence for the model.
_PRIVATE_FIELDS = {"student_id", "password_hash", "name", "semester", "class", "branch"}

_KEY_MESSAGES = {
    "quota": "This Google AI Studio key has reached its free quota. Try again later or use another key.",
    "unavailable": "Google's service is busy right now. Please try again in a moment.",
}
_KEY_FALLBACK = "The key could not be validated. Check it and try again."


def create_app(settings: Settings | None = None, model_client: GemmaClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    students_path = ROOT / "data" / "demo_students.json"
    notices_path = ROOT / "data" / "notices"
    make_demo_students(students_path)
    notices_path.mkdir(parents=True, exist_ok=True)

    provider = DemoCollegeProvider(students_path)
    keystore = KeyStore(settings.database_path)
    sessions = SessionStore(settings.session_ttl_seconds, settings.session_idle_seconds)
    client = model_client or GemmaClient()
    chunks = load_index(settings.index_path)
    if not chunks and any(notices_path.iterdir()):
        chunks = build_index(notices_path, settings.index_path)

    app = FastAPI(title="CampSage", version="0.2.0")
    app.state.provider = provider
    app.state.keystore = keystore
    app.state.sessions = sessions
    app.state.model_client = client
    app.state.chunks = chunks

    def current_session(request: Request):
        return sessions.get(request.cookies.get("campsage_session"))

    def _login(student_id: str, password: str):
        if not provider.verify_login(student_id, password):
            return None
        encryption_key = keystore.prepare_encryption_key(student_id, password)
        api_key = keystore.decrypt_key(student_id, encryption_key) if keystore.has_key(student_id) else None
        # A password change or an interrupted demo setup can leave ciphertext
        # that the verified password cannot unlock. Let the student re-key;
        # connect_key replaces the old ciphertext only after validation.
        return sessions.create(student_id, encryption_key, api_key), api_key

    @app.post("/api/login")
    async def login(payload: dict):
        student_id = str(payload.get("student_id", "")).strip()
        password = str(payload.get("password", ""))
        # Scrypt is deliberately slow; keep it off the event loop.
        outcome = await run_in_threadpool(_login, student_id, password)
        if outcome is None:
            return JSONResponse({"ok": False, "error": "Invalid student ID or password"}, status_code=401)
        session_id, api_key = outcome
        response = JSONResponse({"ok": True, "needs_key": api_key is None, "profile": provider.get_profile(student_id)})
        response.set_cookie("campsage_session", session_id, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_ttl_seconds)
        return response

    @app.get("/api/me")
    async def me(request: Request):
        session = current_session(request)
        if not session:
            return {"logged_in": False}
        return {"logged_in": True, "needs_key": session.api_key is None, "profile": provider.get_profile(session.student_id)}

    @app.post("/api/connect-key")
    async def connect_key(request: Request, payload: dict):
        session = current_session(request)
        if not session:
            return JSONResponse({"ok": False, "error": "Login required"}, status_code=401)
        api_key = str(payload.get("api_key", "")).strip()
        if not api_key or len(api_key) > 512:
            return JSONResponse({"ok": False, "error": _KEY_FALLBACK}, status_code=400)
        try:
            await run_in_threadpool(client.validate_key, api_key)
            await run_in_threadpool(keystore.connect_key, session.student_id, session.encryption_key, api_key)
            session.api_key = api_key
            return {"ok": True, "message": "Key connected"}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": _KEY_MESSAGES.get(classify_error(exc), _KEY_FALLBACK)}, status_code=400)

    @app.post("/api/transcribe")
    async def transcribe(request: Request):
        """Turn one spoken utterance (raw PCM16, mono, 16 kHz) into text for the chat."""
        session = current_session(request)
        if not session:
            return JSONResponse({"ok": False, "error": "Please log in again."}, status_code=401)
        if not session.api_key:
            return JSONResponse({"ok": False, "error": "Connect your Google AI Studio key first."}, status_code=400)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
            return JSONResponse({"ok": False, "error": "That recording was too long."}, status_code=413)
        pcm = await request.body()
        if len(pcm) > MAX_AUDIO_BYTES:
            return JSONResponse({"ok": False, "error": "That recording was too long."}, status_code=413)
        if len(pcm) < MIN_AUDIO_BYTES or len(pcm) % 2:
            return JSONResponse({"ok": False, "error": "That recording was too short."}, status_code=400)
        try:
            text = await run_in_threadpool(client.transcribe, session.api_key, pcm)
        except Exception as exc:
            category = classify_error(exc)
            return JSONResponse({"ok": False, "error": f"I couldn't process the audio ({category}). Please try again."}, status_code=502)
        return {"ok": True, "text": text}

    def _ask(session, question: str):
        student = provider.get_student(session.student_id)
        records = {key: value for key, value in student.items() if key not in _PRIVATE_FIELDS}
        # The model sees the student's academic profile, never their name or ID.
        profile = {key: student[key] for key in ("semester", "class", "branch")}
        # A short follow-up such as "and DBMS?" borrows the previous question for retrieval.
        search_text = question
        if len(question.split()) <= 4:
            previous = [turn["text"] for turn in session.history if turn["role"] == "student"]
            if previous:
                search_text = f"{previous[-1]} {question}"
        contexts = retrieve(search_text, profile, records, app.state.chunks)
        return answer_question(
            session.api_key, question, profile, contexts, client,
            history=list(session.history), drift=session.drift,
        )

    @app.post("/api/ask")
    async def ask(request: Request, payload: dict):
        session = current_session(request)
        if not session:
            return JSONResponse({"status": "error", "answer": "Please log in again.", "sources": []}, status_code=401)
        if not session.api_key:
            return JSONResponse({"status": "error", "answer": "Connect your Google AI Studio key first.", "sources": []}, status_code=400)
        question = str(payload.get("question", "")).strip()
        if not question or len(question) > 2_000:
            return JSONResponse({"status": "error", "answer": "Please enter a short question.", "sources": []}, status_code=400)
        want_audio = payload.get("audio", True) is not False
        try:
            result: AnswerResult = await run_in_threadpool(_ask, session, question)
        except Exception as exc:
            category = classify_error(exc)
            return {"status": "error", "answer": f"The assistant is temporarily unavailable ({category}). Please try again.", "sources": []}
        session.remember(question, result.answer, result.status)
        return {
            "status": result.status,
            "answer": result.answer,
            "sources": result.sources,
            "model": result.model_id,
            "audio_base64": result.audio_base64 if want_audio else "",
        }

    @app.post("/api/logout")
    async def logout(request: Request):
        sessions.destroy(request.cookies.get("campsage_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("campsage_session")
        return response

    static_dir = ROOT / "campsage" / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()

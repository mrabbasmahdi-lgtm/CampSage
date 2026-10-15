"""FastAPI entrypoint for the local CampSage prototype."""

from __future__ import annotations

from fastapi import FastAPI, Request
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

    app = FastAPI(title="CampSage", version="0.1.0")
    app.state.provider = provider
    app.state.keystore = keystore
    app.state.sessions = sessions
    app.state.model_client = client
    app.state.chunks = chunks

    def current_session(request: Request):
        return sessions.get(request.cookies.get("campsage_session"))

    @app.post("/api/login")
    async def login(payload: dict):
        student_id = str(payload.get("student_id", "")).strip()
        password = str(payload.get("password", ""))
        if not provider.verify_login(student_id, password):
            return JSONResponse({"ok": False, "error": "Invalid student ID or password"}, status_code=401)
        encryption_key = keystore.prepare_encryption_key(student_id, password)
        has_key = keystore.has_key(student_id)
        api_key = keystore.decrypt_key(student_id, encryption_key) if has_key else None
        # A password change or an interrupted demo setup can leave ciphertext
        # that the verified password cannot unlock. Let the student re-key;
        # connect_key replaces the old ciphertext only after validation.
        session_id = sessions.create(student_id, encryption_key, api_key)
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
            return JSONResponse({"ok": False, "error": "The key could not be validated. Check it and try again."}, status_code=400)
        try:
            client.validate_key(api_key)
            keystore.connect_key(session.student_id, session.encryption_key, api_key)
            session.api_key = api_key
            return {"ok": True, "message": "Key connected"}
        except Exception as exc:
            classify_error(exc)
            return JSONResponse({"ok": False, "error": "The key could not be validated. Check it and try again."}, status_code=400)

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
        student = provider.get_student(session.student_id)
        records = {key: value for key, value in student.items() if key not in {"student_id", "password_hash", "semester", "class", "branch"}}
        profile = provider.get_profile(session.student_id)
        contexts = retrieve(question, profile, records, app.state.chunks)
        try:
            result: AnswerResult = answer_question(session.api_key, question, profile, contexts, client)
            return {
                "status": result.status,
                "answer": result.answer,
                "sources": result.sources,
                "model": result.model_id,
                "audio_base64": result.audio_base64,
            }
        except Exception as exc:
            category = classify_error(exc)
            return {"status": "error", "answer": f"The assistant is temporarily unavailable ({category}). Please try again.", "sources": []}

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

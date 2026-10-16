"""Streaming voice turn over a WebSocket (``/ws/voice``).

One student utterance per socket.  The student's microphone audio is forwarded to a Live
transcription session *while they speak*, and the answering Live session is opened in
parallel, so after "end" only two short steps remain: the transcript (about half a second)
and the first spoken audio chunk (about a second).  Answer audio is relayed chunk by chunk
as it is generated.

Protocol (see the task contract):

  client -> server   binary PCM16 mono 16 kHz frames, then text {"type":"end"} or {"type":"cancel"}
  server -> client   {"type":"transcript","text":...,"final":true}, {"type":"thinking"},
                     binary PCM16 mono 24 kHz chunks, {"type":"done",...} or {"type":"error",...}

Retrieval, prompt, conversation history, drift counting and answer classification are the
same code paths as ``/api/ask``, so statuses and sources match.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket
from fastapi.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .answer import DRIFT_LIMIT, OFFTOPIC_ANSWER, AnswerResult, _classify, build_live_prompt, is_campus_question
from .gemma import LIVE_MODEL_ID, classify_error
from .retrieve import retrieve

router = APIRouter()
log = logging.getLogger("campsage.voice")

CLOSE_UNAUTHENTICATED = 4401
# A student who opens the socket and then says nothing must not hold two Live sessions open.
RECORDING_IDLE_S = 60.0
TRANSCRIPT_TIMEOUT_S = 6.0

# The reply is spoken, so skip the "anything else?" sign-off that only adds seconds of audio.
VOICE_STYLE = (
    "\n\nThis reply is spoken in a live voice conversation. Answer directly and stop. "
    "Do not end with a question or an offer of further help."
    "\n\nLANGUAGE RULE FOR VOICE (this replaces any earlier language rule): "
    "If the student speaks Hindi (in Devanagari or in Roman letters) or Hinglish, reply in Hinglish: "
    "natural Hindi written in Roman letters, mixed with common English words such as fee, exam, "
    "attendance, form, office and deadline. Never use Devanagari script in the reply. "
    "If the student speaks any other language, including English, reply in clear simple English, "
    "even when the student's language is Urdu, Bengali, Tamil, Punjabi or anything else. "
    "Keep names, room numbers, dates and amounts exactly as in the evidence. "
    "The one exception is when the evidence does not support an answer: then say the exact English "
    "sentence required for that case, whatever the language."
)


class _Gone(Exception):
    """The browser went away; stop quietly."""


async def _send_json(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except (WebSocketDisconnect, RuntimeError, OSError) as exc:
        raise _Gone from exc


async def _send_bytes(ws: WebSocket, data: bytes) -> None:
    try:
        await ws.send_bytes(data)
    except (WebSocketDisconnect, RuntimeError, OSError) as exc:
        raise _Gone from exc


async def _close(ws: WebSocket, code: int = 1000) -> None:
    if ws.application_state == WebSocketState.CONNECTED:
        try:
            await ws.close(code=code)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass


async def _fail(ws: WebSocket, message: str) -> None:
    """Send the contract's error frame, then close."""

    try:
        await _send_json(ws, {"type": "error", "message": message})
    except _Gone:
        return
    await _close(ws)


def _speaker_rules() -> str:
    # The same system rules /api/ask uses, taken from the shared helper, plus the voice style.
    return build_live_prompt("", {}, [], [], 0)[1] + VOICE_STYLE


def _prepare(app, session, question: str) -> tuple[list, str]:
    """Retrieve evidence and build the prompt exactly as /api/ask does (blocking; run in a thread)."""

    from .app import _PRIVATE_FIELDS  # imported late: campsage.app imports this module

    student = app.state.provider.get_student(session.student_id)
    records = {key: value for key, value in student.items() if key not in _PRIVATE_FIELDS}
    profile = {key: student[key] for key in ("semester", "class", "branch")}
    search_text = question
    if len(question.split()) <= 4:
        previous = [turn["text"] for turn in session.history if turn["role"] == "student"]
        if previous:
            search_text = f"{previous[-1]} {question}"
    contexts = retrieve(search_text, profile, records, app.state.chunks)
    prompt, _ = build_live_prompt(question, profile, contexts, list(session.history), session.drift)
    return contexts, prompt


async def _record(ws: WebSocket, transcriber, audio: bytearray, limit: int) -> str:
    """Forward microphone frames until the student ends or cancels; returns how it ended."""

    carry = b""
    while True:
        try:
            message = await asyncio.wait_for(ws.receive(), timeout=RECORDING_IDLE_S)
        except asyncio.TimeoutError:
            return "idle"
        if message.get("type") == "websocket.disconnect":
            return "gone"
        data = message.get("bytes")
        if data is not None:
            data = carry + data
            carry = b""
            if len(data) % 2:  # keep sample alignment if a frame was split mid-sample
                data, carry = data[:-1], data[-1:]
            if not data:
                continue
            if len(audio) + len(data) > limit:
                return "toolong"
            audio.extend(data)
            transcriber.push(data)
            continue
        text = message.get("text")
        if text is None:
            continue
        try:
            kind = json.loads(text).get("type")
        except (ValueError, AttributeError):
            continue
        if kind == "end":
            return "end"
        if kind == "cancel":
            return "cancel"


async def _final_transcript(client, api_key: str, transcriber, pcm: bytes) -> str:
    text = ""
    try:
        text = await transcriber.finish(TRANSCRIPT_TIMEOUT_S)
    except Exception as exc:
        log.warning("streaming transcription failed (%s)", classify_error(exc))
    if text:
        return text
    # One-shot fallback on a fresh session (padded with silence); only after a miss.
    try:
        return await run_in_threadpool(client.transcribe, api_key, pcm)
    except Exception as exc:
        log.warning("fallback transcription failed (%s)", classify_error(exc))
        return ""


async def _answer_turn(ws: WebSocket, app, session, speaker, question: str, started: float) -> None:
    contexts, prompt = await run_in_threadpool(_prepare, app, session, question)
    # Same guard as answer_question: after a long run of off-topic turns CampSage stops engaging.
    declining = session.drift >= DRIFT_LIMIT and not is_campus_question(question)
    if declining:
        prompt = f'Say exactly the following sentence aloud and nothing else: "{OFFTOPIC_ANSWER}"'

    reply: list[str] = []
    first_audio_at = None
    async for kind, data in speaker.stream(prompt):
        if kind == "audio":
            if first_audio_at is None:
                first_audio_at = time.monotonic()
            await _send_bytes(ws, data)  # relayed the moment it is generated
        else:
            reply.append(data)

    if declining:
        result = AnswerResult("offtopic", OFFTOPIC_ANSWER, [], LIVE_MODEL_ID)
    else:
        result = _classify("".join(reply).strip(), question, contexts, LIVE_MODEL_ID, "")
    session.remember(question, result.answer, result.status)
    await _send_json(
        ws,
        {"type": "done", "status": result.status, "answer": result.answer, "sources": result.sources, "model": LIVE_MODEL_ID},
    )
    log.info(
        "voice turn: first_audio=%s done=%.2fs (since final transcript)",
        "none" if first_audio_at is None else f"{first_audio_at - started:.2f}s",
        time.monotonic() - started,
    )


@router.websocket("/ws/voice")
async def voice(ws: WebSocket) -> None:
    # Accept first so the 4401 close code reaches the browser (closing before accept is an HTTP 403).
    await ws.accept()
    app = ws.app
    session = app.state.sessions.get(ws.cookies.get("campsage_session"))
    if session is None:
        await _close(ws, CLOSE_UNAUTHENTICATED)
        return
    if not session.api_key:
        await _fail(ws, "Connect your Google AI Studio key first.")
        return

    from .app import MAX_AUDIO_BYTES, MIN_AUDIO_BYTES  # imported late: campsage.app imports this module

    client = app.state.model_client
    transcriber = speaker = None
    background: list[asyncio.Task] = []
    try:
        transcriber = client.transcriber(session.api_key)
        speaker = client.speaker(session.api_key, _speaker_rules())
        # Both Live sessions connect while the student is still talking.
        transcriber.begin()
        speaker.begin()

        audio = bytearray()
        outcome = await _record(ws, transcriber, audio, MAX_AUDIO_BYTES)
        if outcome == "gone":
            return
        if outcome == "cancel":
            await _close(ws)
            return
        if outcome == "toolong":
            await _fail(ws, "That recording was too long.")
            return
        if outcome == "idle":
            await _fail(ws, "I didn't hear anything. Please try again.")
            return
        if len(audio) < MIN_AUDIO_BYTES:
            await _fail(ws, "That recording was too short.")
            return

        ended = time.monotonic()
        question = (await _final_transcript(client, session.api_key, transcriber, bytes(audio)))[:2_000].strip()
        # The relay session has served its purpose; stop it generating its spoken "OK".
        background.append(asyncio.create_task(transcriber.aclose()))
        if not question:
            await _fail(ws, "I couldn't hear that. Please try again.")
            return
        log.info("voice turn: final transcript %.2fs after end", time.monotonic() - ended)

        await _send_json(ws, {"type": "transcript", "text": question, "final": True})
        await _send_json(ws, {"type": "thinking"})
        await _answer_turn(ws, app, session, speaker, question, time.monotonic())
        await _close(ws)
    except _Gone:
        pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("voice turn failed (%s)", classify_error(exc))
        await _fail(ws, f"The assistant is temporarily unavailable ({classify_error(exc)}). Please try again.")
    finally:
        for live in (transcriber, speaker):
            if live is not None:
                background.append(asyncio.create_task(live.aclose()))
        await asyncio.gather(*background, return_exceptions=True)
        await _close(ws)

"""One-shot Gemini Live adapter used by CampSage.

Every operation opens a fresh Live session with the caller's API key.  There
is intentionally no model ladder here: Live models have their own config and
are not interchangeable with REST text models.

Two operations are exposed:

* ``generate``   - send a text prompt, get the model's transcript plus native audio.
* ``transcribe`` - send a short PCM16 utterance, get back what the student said.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass


LIVE_MODEL_ID = "models/gemini-3.1-flash-live-preview"
LIVE_VOICE = "Charon"
INPUT_SAMPLE_RATE = 16_000

# Live's server-side voice activity detection only decides that an utterance has
# ended when it hears silence after it.  A bare clip followed by audio_stream_end
# never produced a transcript, so every transcription is padded with this tail.
TRAILING_SILENCE_MS = 1_000
# Transcription timing: the student's words arrive before the relay's spoken "OK".
# Once the relay starts, wait briefly for a trailing fragment and stop.
TRANSCRIBE_GRACE_AFTER_MODEL_S = 0.4
TRANSCRIBE_GRACE_NO_TEXT_S = 1.0
TRANSCRIBE_IDLE_AFTER_TEXT_S = 1.5

# Measured against the live model: every audio chunk and every output-transcription
# fragment has arrived by the time ``generation_complete`` is sent, while
# ``turn_complete`` follows 3-6 s later.  Reading stops shortly after generation is
# complete instead of waiting for turn_complete; the settle window only guards a
# straggling transcript fragment.
GENERATION_SETTLE_S = 0.2
CONNECT_TIMEOUT_S = 15.0
HEDGE_AFTER_S = 2.5
# Streaming transcription: the whole utterance's transcript arrives as one fragment
# about 0.4-0.6 s after activity_end; this is the extra wait for a trailing fragment.
STREAM_TRANSCRIPT_GRACE_S = 0.15
STREAM_NO_TEXT_GRACE_S = 0.6

_TRANSCRIBER_RULES = (
    "You are a silent speech-to-text relay inside a campus help desk. "
    "Never answer, explain or react to what you hear. Whatever the speaker says, "
    "reply with the single word: OK."
)


@dataclass(frozen=True)
class ModelResult:
    """The transcript and optional PCM audio returned by one Live turn."""

    text: str
    model_id: str = LIVE_MODEL_ID
    audio_bytes: bytes = b""

    @property
    def transcription(self) -> str:
        """Alias for callers that want to distinguish transcript from audio."""

        return self.text

    @property
    def audio(self) -> bytes:
        """Compatibility alias for the raw PCM payload."""

        return self.audio_bytes

    @property
    def audio_base64(self) -> str:
        """Return audio in a JSON-safe representation."""

        return base64.b64encode(self.audio_bytes).decode("ascii") if self.audio_bytes else ""


def classify_error(error: Exception) -> str:
    message = str(error).lower()
    if "429" in message or "resource_exhausted" in message or "quota" in message:
        return "quota"
    if any(value in message for value in ("500", "503", "504", "unavailable", "deadline_exceeded", "timeout")):
        return "unavailable"
    if any(value in message for value in ("403", "404", "not found", "not supported", "permission", "api key not valid")):
        return "invalid"
    return "error"


def _get(obj, name, default=None):
    """Read ``name`` from an SDK object or a plain dict."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _part_audio(part) -> bytes:
    inline_data = _get(part, "inline_data")
    if inline_data is None:
        return b""
    data = _get(inline_data, "data")
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True)
        except Exception:
            return b""
    return b""


def _response_parts(response) -> tuple[str, bytes, bool]:
    """Extract Live output transcription, PCM bytes, and turn completion."""

    server_content = _get(response, "server_content")
    if server_content is None:
        return "", b"", False
    text = _get(_get(server_content, "output_transcription"), "text")
    model_turn = _get(server_content, "model_turn")
    parts = _get(model_turn, "parts") or []
    audio = b"".join(_part_audio(part) for part in parts)
    complete = bool(_get(server_content, "turn_complete", False))
    return str(text or ""), audio, complete


def _generation_done(response) -> bool:
    """True when the model reports it has finished generating (audio and text included)."""

    return bool(_get(_get(response, "server_content"), "generation_complete", False))


async def _turn_events(session, timeout_s: float) -> AsyncIterator[tuple[str, object]]:
    """Yield ``("text", str)`` and ``("audio", bytes)`` for one model turn, as they arrive.

    Ends at turn_complete, or shortly after generation_complete (see GENERATION_SETTLE_S),
    so a reply is never held back for the server's trailing playback bookkeeping.
    Raises TimeoutError when the model stays silent past ``timeout_s``.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    stream = session.receive().__aiter__()
    settling = False
    while True:
        remaining = deadline - loop.time()
        wait = min(GENERATION_SETTLE_S, remaining) if settling else remaining
        if wait <= 0:
            if settling:
                return
            raise TimeoutError("Gemini Live turn timed out")
        try:
            response = await asyncio.wait_for(stream.__anext__(), timeout=wait)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            if settling:
                return
            raise TimeoutError("Gemini Live turn timed out") from None
        text, pcm, complete = _response_parts(response)
        if text:
            yield "text", text
        if pcm:
            yield "audio", pcm
        if complete:
            return
        if _generation_done(response):
            settling = True


# Fire-and-forget close tasks must be referenced, or the loop may drop them mid-flight.
_BACKGROUND: set[asyncio.Task] = set()


async def _open_live(owner: "GemmaClient", api_key: str, config):
    """Open a Live session, hedging against a stalled handshake.

    Connecting takes 1.0-2.3 s; very occasionally the handshake simply never completes.
    If the first attempt has not finished after HEDGE_AFTER_S a second one is started and
    whichever connects first wins (a failed first attempt also triggers the second).
    Returns ``(context_manager, session)``; the caller owns ``__aexit__``.
    """

    client = owner._client(api_key)

    async def attempt():
        cm = client.aio.live.connect(model=LIVE_MODEL_ID, config=config)
        return cm, await cm.__aenter__()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + CONNECT_TIMEOUT_S
    pending = {asyncio.create_task(attempt())}
    hedged = False
    winner = None
    last_error: BaseException | None = None
    try:
        while pending and winner is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            wait = remaining if hedged else min(HEDGE_AFTER_S, remaining)
            done, pending = await asyncio.wait(pending, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if error is None and winner is None:
                    winner = task.result()
                elif error is None:
                    # Both finished in the same tick: keep one, close the other.
                    spare_cm, _ = task.result()
                    closer = asyncio.create_task(spare_cm.__aexit__(None, None, None))
                    _BACKGROUND.add(closer)
                    closer.add_done_callback(_BACKGROUND.discard)
                else:
                    last_error = error
            if winner is None and not hedged and (not done or not pending):
                pending.add(asyncio.create_task(attempt()))
                hedged = True
    finally:
        for task in pending:
            task.cancel()
    if winner is None:
        raise last_error if last_error is not None else TimeoutError("Gemini Live connection timed out")
    return winner


def _input_text(response) -> str:
    """Extract the transcription of what the student said, if this message has any."""

    server_content = _get(response, "server_content")
    return str(_get(_get(server_content, "input_transcription"), "text") or "")


class GemmaClient:
    """Compatibility-named client backed exclusively by Gemini Live."""

    def __init__(self, timeout_ms: int = 30_000):
        self.timeout_ms = max(10_000, timeout_ms)

    def _client(self, api_key: str):
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1beta", timeout=self.timeout_ms),
        )

    def _config(
        self,
        *,
        system_instruction: str | None = None,
        transcribe_input: bool = False,
        manual_activity: bool = False,
    ):
        from google.genai import types

        kwargs = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE)
                )
            ),
        )
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if transcribe_input:
            kwargs["input_audio_transcription"] = {}
        if manual_activity:
            # The caller signals activity_start / activity_end itself, so the server
            # never waits for trailing silence to decide that the student has finished.
            kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
            )
        return types.LiveConnectConfig(**kwargs)

    async def _live_turn(self, api_key: str, prompt: str, system_instruction: str | None = None) -> ModelResult:
        client = self._client(api_key)
        config = self._config(system_instruction=system_instruction)
        transcript: list[str] = []
        audio = bytearray()
        async with client.aio.live.connect(model=LIVE_MODEL_ID, config=config) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": prompt}]},
                turn_complete=True,
            )

            async for kind, data in _turn_events(session, self.timeout_ms / 1000):
                if kind == "text":
                    transcript.append(data)
                else:
                    audio.extend(data)
        return ModelResult("".join(transcript).strip(), LIVE_MODEL_ID, bytes(audio))

    async def _transcribe_turn(self, api_key: str, pcm: bytes) -> str:
        from google.genai import types

        client = self._client(api_key)
        config = self._config(system_instruction=_TRANSCRIBER_RULES, transcribe_input=True)
        heard: list[str] = []
        silence = b"\x00\x00" * (INPUT_SAMPLE_RATE * TRAILING_SILENCE_MS // 1000)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_ms / 1000
        async with client.aio.live.connect(model=LIVE_MODEL_ID, config=config) as session:
            # The silence tail is what lets the server's VAD close the utterance.
            await session.send_realtime_input(
                audio=types.Blob(data=pcm + silence, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
            )
            await session.send_realtime_input(audio_stream_end=True)

            stream = session.receive().__aiter__()
            model_started = False
            while True:
                # The student's words arrive before the relay's spoken "OK", so never
                # wait for the "OK": once the relay starts (or text has stopped
                # flowing) allow a short grace for a trailing fragment, then stop.
                if model_started:
                    wait = TRANSCRIBE_GRACE_AFTER_MODEL_S if heard else TRANSCRIBE_GRACE_NO_TEXT_S
                elif heard:
                    wait = TRANSCRIBE_IDLE_AFTER_TEXT_S
                else:
                    wait = deadline - loop.time()
                wait = min(wait, deadline - loop.time())
                if wait <= 0:
                    break
                try:
                    response = await asyncio.wait_for(stream.__anext__(), timeout=wait)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
                fragment = _input_text(response)
                if fragment:
                    heard.append(fragment)
                text, pcm_out, complete = _response_parts(response)
                if text or pcm_out:
                    model_started = True
                if complete:
                    break
        return " ".join("".join(heard).split())

    def _run_live(self, coro_factory, budget_s: float):
        """Run an async Live call away from any caller-owned event loop."""

        result: dict = {}
        failure: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(coro_factory())
            except BaseException as exc:  # preserve SDK exception type for the route
                failure["value"] = exc

        thread = threading.Thread(target=worker, name="campsage-gemini-live", daemon=True)
        thread.start()
        thread.join(budget_s + 5)
        if thread.is_alive():
            raise TimeoutError("Gemini Live request timed out")
        if "value" in failure:
            raise failure["value"]
        return result["value"]

    def transcriber(self, api_key: str) -> "LiveTranscriber":
        """A streaming transcriber for one utterance (call ``begin()`` from the event loop)."""

        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return LiveTranscriber(self, api_key)

    def speaker(self, api_key: str, system_instruction: str | None = None) -> "LiveSpeaker":
        """A pre-openable answering session that streams audio chunks as they are generated."""

        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return LiveSpeaker(self, api_key, system_instruction)

    def validate_key(self, api_key: str) -> ModelResult:
        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return self.generate(api_key, "Reply briefly with OK to confirm this connection.")

    def generate(self, api_key: str, prompt: str, system_instruction: str | None = None) -> ModelResult:
        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return self._run_live(lambda: self._live_turn(api_key, prompt, system_instruction), self.timeout_ms / 1000)

    def transcribe(self, api_key: str, pcm: bytes) -> str:
        """Turn one PCM16 mono 16 kHz utterance into text using the student's key."""

        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        if not pcm or len(pcm) % 2:
            raise ValueError("invalid audio")
        # One retry: a Live session occasionally closes the utterance without a
        # transcript, or stalls; a fresh session almost always recovers.
        text = ""
        for attempt in range(2):
            try:
                text = self._run_live(lambda: self._transcribe_turn(api_key, pcm), self.timeout_ms / 1000)
            except TimeoutError:
                if attempt:
                    raise
                continue
            if text:
                return text
        return text


class LiveTranscriber:
    """Transcribe one utterance on a Live session that is open *while the student speaks*.

    ``begin()`` starts connecting at once, ``push()`` queues PCM16 16 kHz chunks as they
    arrive (they are forwarded as soon as the session is up), and ``finish()`` signals
    the end of speech.  Activity is signalled manually (activity_start / activity_end,
    automatic detection off), so there is no trailing-silence padding and no idle wait:
    the transcript, one fragment for the whole utterance, arrives about half a second
    after ``finish()``.  The relay's spoken "OK" is never waited for.
    """

    def __init__(self, owner: "GemmaClient", api_key: str):
        self._owner = owner
        self._api_key = api_key
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._heard: list[str] = []
        self._model_started = False
        self._reader_done = False
        self._error: Exception | None = None
        self._changed = asyncio.Event()
        self._sender: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._cm = None
        self._session = None

    def begin(self) -> None:
        if self._sender is None:
            self._sender = asyncio.create_task(self._run())

    def push(self, pcm: bytes) -> None:
        self._queue.put_nowait(pcm)

    async def _run(self) -> None:
        from google.genai import types

        try:
            config = self._owner._config(
                system_instruction=_TRANSCRIBER_RULES, transcribe_input=True, manual_activity=True
            )
            self._cm, self._session = await _open_live(self._owner, self._api_key, config)
            self._reader = asyncio.create_task(self._read())
            await self._session.send_realtime_input(activity_start=types.ActivityStart())
            while True:
                pcm = await self._queue.get()
                if pcm is None:
                    await self._session.send_realtime_input(activity_end=types.ActivityEnd())
                    return
                await self._session.send_realtime_input(
                    audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = exc
            self._changed.set()

    async def _read(self) -> None:
        try:
            async for response in self._session.receive():
                fragment = _input_text(response)
                if fragment:
                    self._heard.append(fragment)
                text, pcm_out, complete = _response_parts(response)
                if text or pcm_out:
                    self._model_started = True
                if fragment or text or pcm_out or complete:
                    self._changed.set()
                if complete:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = exc
        finally:
            self._reader_done = True
            self._changed.set()

    async def finish(self, timeout_s: float) -> str:
        """Signal the end of speech and return the transcript ('' when nothing was heard)."""

        self._queue.put_nowait(None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            remaining = deadline - loop.time()
            if self._heard:
                if self._model_started or self._reader_done or remaining <= 0:
                    break
                # The transcript is a single fragment; wait briefly for a trailing one.
                wait = min(STREAM_TRANSCRIPT_GRACE_S, remaining)
            elif self._error is not None:
                raise self._error
            elif self._reader_done or remaining <= 0:
                break
            elif self._model_started:
                wait = min(STREAM_NO_TEXT_GRACE_S, remaining)
            else:
                wait = remaining
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=wait)
            except asyncio.TimeoutError:
                if self._heard or self._model_started or deadline - loop.time() <= 0:
                    break
        return " ".join("".join(self._heard).split())

    async def aclose(self) -> None:
        tasks = [task for task in (self._sender, self._reader) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._cm is not None and self._session is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass


class LiveSpeaker:
    """An answering Live session opened ahead of time, so the ~1.3 s setup is hidden.

    ``begin()`` starts connecting while the student is still speaking; ``stream(prompt)``
    then sends the prompt and yields ``("text", str)`` / ``("audio", bytes)`` events as the
    model produces them, ending right after generation completes.
    """

    def __init__(self, owner: "GemmaClient", api_key: str, system_instruction: str | None):
        self._owner = owner
        self._api_key = api_key
        self._system_instruction = system_instruction
        self._open_task: asyncio.Task | None = None
        self._cm = None
        self._session = None

    def begin(self) -> None:
        if self._open_task is None:
            self._open_task = asyncio.create_task(self._open())

    async def _open(self) -> None:
        config = self._owner._config(system_instruction=self._system_instruction)
        self._cm, self._session = await _open_live(self._owner, self._api_key, config)

    async def _ready(self):
        self.begin()
        try:
            await self._open_task
        except asyncio.CancelledError:
            raise
        except Exception:
            # The early connection failed; one fresh attempt before giving up.
            self._cm = self._session = None
            await self._open()
        return self._session

    async def stream(self, prompt: str) -> AsyncIterator[tuple[str, object]]:
        session = await self._ready()
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": prompt}]},
            turn_complete=True,
        )
        async for event in _turn_events(session, self._owner.timeout_ms / 1000):
            yield event

    async def aclose(self) -> None:
        task = self._open_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._cm is not None and self._session is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass

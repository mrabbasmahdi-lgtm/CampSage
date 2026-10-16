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

    def _config(self, *, system_instruction: str | None = None, transcribe_input: bool = False):
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

            async def receive_turn() -> None:
                async for response in session.receive():
                    text, pcm, complete = _response_parts(response)
                    if text:
                        transcript.append(text)
                    audio.extend(pcm)
                    if complete:
                        return

            await asyncio.wait_for(receive_turn(), timeout=self.timeout_ms / 1000)
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

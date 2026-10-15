"""One-shot Gemini Live adapter used by CampSage.

Every operation opens a fresh Live session with the caller's API key.  There
is intentionally no model ladder here: Live models have their own config and
are not interchangeable with REST text models.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from dataclasses import dataclass


LIVE_MODEL_ID = "models/gemini-3.1-flash-live-preview"
LIVE_VOICE = "Charon"


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


def _part_audio(part) -> bytes:
    inline_data = getattr(part, "inline_data", None)
    if inline_data is None and isinstance(part, dict):
        inline_data = part.get("inline_data")
    if inline_data is None:
        return b""
    data = getattr(inline_data, "data", None)
    if data is None and isinstance(inline_data, dict):
        data = inline_data.get("data")
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

    server_content = getattr(response, "server_content", None)
    if server_content is None and isinstance(response, dict):
        server_content = response.get("server_content")
    if server_content is None:
        return "", b"", False

    transcription = getattr(server_content, "output_transcription", None)
    if transcription is None and isinstance(server_content, dict):
        transcription = server_content.get("output_transcription")
    text = getattr(transcription, "text", None) if transcription is not None else None
    if text is None and isinstance(transcription, dict):
        text = transcription.get("text")

    model_turn = getattr(server_content, "model_turn", None)
    if model_turn is None and isinstance(server_content, dict):
        model_turn = server_content.get("model_turn")
    parts = getattr(model_turn, "parts", None) if model_turn is not None else None
    if parts is None and isinstance(model_turn, dict):
        parts = model_turn.get("parts")
    audio = b"".join(_part_audio(part) for part in (parts or []))
    complete = bool(getattr(server_content, "turn_complete", False))
    if isinstance(server_content, dict):
        complete = bool(server_content.get("turn_complete", complete))
    return str(text or ""), audio, complete


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

    async def _live_turn(self, api_key: str, prompt: str) -> ModelResult:
        from google.genai import types

        client = self._client(api_key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE)
                )
            ),
        )
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

    def _run_live(self, api_key: str, prompt: str) -> ModelResult:
        """Run the async Live API away from any caller-owned event loop."""

        result: dict[str, ModelResult] = {}
        failure: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(self._live_turn(api_key, prompt))
            except BaseException as exc:  # preserve SDK exception type for the route
                failure["value"] = exc

        thread = threading.Thread(target=worker, name="campsage-gemini-live", daemon=True)
        thread.start()
        thread.join(self.timeout_ms / 1000 + 5)
        if thread.is_alive():
            raise TimeoutError("Gemini Live request timed out")
        if "value" in failure:
            raise failure["value"]
        return result["value"]

    def validate_key(self, api_key: str) -> ModelResult:
        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return self._run_live(api_key, "Reply briefly with OK to confirm this connection.")

    def generate(self, api_key: str, prompt: str) -> ModelResult:
        if not api_key or len(api_key) > 512:
            raise ValueError("invalid key")
        return self._run_live(api_key, prompt)

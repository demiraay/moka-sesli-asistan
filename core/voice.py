from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from core.config import Config

if TYPE_CHECKING:
    from core.orchestrator import AgentOrchestrator


class WhisperTranscriber:
    """Lokal whisper STT (fallback). torch/whisper importu tembeldir:
    Groq STT yolundayken agir import maliyeti odenmez."""

    _loaded_models: dict[tuple[str, str], Any] = {}

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def _require_ffmpeg(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                "Local Whisper icin ffmpeg gerekiyor. Lutfen ffmpeg kurup tekrar deneyin."
            )

    def _get_model(self) -> Any:
        import whisper  # lazy: torch yuku sadece gercekten gerekince

        model_name = self.config.whisper_model
        device = self.config.whisper_device or "cpu"
        cache_key = (model_name, device)
        if cache_key not in self._loaded_models:
            self._loaded_models[cache_key] = whisper.load_model(model_name, device=device)
        return self._loaded_models[cache_key]

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Ses dosyasi bulunamadi: {audio_file}")

        self._require_ffmpeg()
        model = self._get_model()
        target_language = language or self.config.whisper_language or None
        result = model.transcribe(
            str(audio_file),
            language=target_language,
            fp16=False,
        )
        return {
            "text": str(result.get("text", "")).strip(),
            "language": result.get("language") or target_language,
            "segments": result.get("segments", []),
            "source_audio_path": str(audio_file),
            "engine": "whisper-local",
        }


class GroqWhisperTranscriber:
    """Groq'un Whisper API'si (whisper-large-v3-turbo): ucretsiz tier, ~0.5s.

    MediaRecorder'in webm/opus ciktisini dogrudan kabul eder — ffmpeg gerekmez.
    """

    MODEL = "whisper-large-v3-turbo"

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def is_configured(self) -> bool:
        return bool(self.config.groq_api_key)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Ses dosyasi bulunamadi: {audio_file}")
        if not self.config.groq_api_key:
            raise RuntimeError("GROQ_API_KEY .env icinde tanimli olmali.")

        boundary = uuid.uuid4().hex
        target_language = language or self.config.whisper_language or "tr"

        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode("utf-8")
            )

        add_field("model", self.MODEL)
        add_field("language", target_language)
        add_field("response_format", "json")
        add_field("temperature", "0")
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{audio_file.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(audio_file.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        url = f"{self.config.groq_base_url.rstrip('/')}/audio/transcriptions"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.groq_api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                # Cloudflare, ciplak Python-urllib UA'sini 403'luyor.
                "User-Agent": "moka-voice-agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Groq STT istegi basarisiz oldu: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Groq STT baglantisi kurulamadi: {error}") from error

        return {
            "text": str(result.get("text", "")).strip(),
            "language": target_language,
            "segments": [],
            "source_audio_path": str(audio_file),
            "engine": "groq-whisper",
        }


class CompositeTranscriber:
    """Groq STT varsa onu kullanir; hata/eksik anahtar durumunda lokal whisper'a duser."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.groq = GroqWhisperTranscriber(self.config)
        self.local = WhisperTranscriber(self.config)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        if self.groq.is_configured():
            try:
                return self.groq.transcribe(audio_path, language=language)
            except Exception as error:
                print(f"Groq STT fallback: {error}")
        return self.local.transcribe(audio_path, language=language)


class ElevenLabsSynthesizer:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def is_configured(self) -> bool:
        return bool(self.config.elevenlabs_api_key and self.config.elevenlabs_voice_id)

    def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> str:
        if not self.config.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY .env icinde tanimli olmali.")
        active_voice_id = voice_id or self.config.elevenlabs_voice_id
        if not active_voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID .env icinde tanimli olmali.")

        target_path = Path(output_path) if output_path else self._default_output_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        url = (
            f"{self.config.elevenlabs_base_url}/text-to-speech/{active_voice_id}"
            f"?output_format={self.config.elevenlabs_output_format}"
        )
        payload = {
            "text": text,
            "model_id": model_id or self.config.elevenlabs_model_id,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "xi-api-key": self.config.elevenlabs_api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                target_path.write_bytes(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ElevenLabs istegi basarisiz oldu: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"ElevenLabs baglantisi kurulamadi: {error}") from error

        return str(target_path)

    def _default_output_path(self) -> Path:
        return Path(self.config.voice_output_dir) / f"reply-{uuid.uuid4().hex}.mp3"


class VoiceTurnProcessor:
    def __init__(
        self,
        orchestrator: "AgentOrchestrator",
        transcriber: Optional[Any] = None,
        synthesizer: Optional[ElevenLabsSynthesizer] = None,
    ):
        self.orchestrator = orchestrator
        self.transcriber = transcriber or CompositeTranscriber(orchestrator.config)
        self.synthesizer = synthesizer or ElevenLabsSynthesizer(orchestrator.config)

    def process_audio_turn(
        self,
        audio_path: str,
        user_id: str = "default_user",
        channel: str = "voice",
        output_audio_path: Optional[str] = None,
        synthesize_reply: bool = True,
    ) -> Dict[str, Any]:
        transcription = self.transcriber.transcribe(audio_path)
        transcript_text = transcription.get("text", "").strip()
        if not transcript_text:
            raise ValueError("Whisper ses dosyasindan okunabilir bir metin cikaramadi.")

        turn_result = self.orchestrator.process_turn(
            transcript_text,
            user_id=user_id,
            channel=channel,
        )

        audio_reply_path = None
        if synthesize_reply:
            audio_reply_path = self.synthesizer.synthesize(
                turn_result["agent_response"],
                output_path=output_audio_path,
            )

        return {
            "transcription": transcription,
            "user_input": transcript_text,
            "agent_response": turn_result["agent_response"],
            "router_decision": turn_result["router_decision"],
            "context": turn_result["context"],
            "reply_audio_path": audio_reply_path,
        }

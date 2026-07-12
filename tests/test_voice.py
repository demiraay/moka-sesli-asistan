import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.orchestrator import AgentOrchestrator
from core.voice import ElevenLabsSynthesizer, VoiceTurnProcessor, WhisperTranscriber


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestVoice(unittest.TestCase):
    def setUp(self):
        Config._instance = None
        WhisperTranscriber._loaded_models = {}

    def test_whisper_transcriber_uses_large_model_and_returns_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "input.wav"
            audio_path.write_bytes(b"fake-wave")

            os.environ["WHISPER_MODEL"] = "large"
            os.environ["WHISPER_LANGUAGE"] = "tr"
            os.environ["WHISPER_DEVICE"] = "cpu"

            model = MagicMock()
            model.transcribe.return_value = {
                "text": "Merhaba",
                "language": "tr",
                "segments": [{"id": 0}],
            }

            # whisper importu artik tembel (torch maliyeti); sahte modulu
            # sys.modules'e enjekte ederek lazy import'u yakaliyoruz.
            fake_whisper = MagicMock()
            fake_whisper.load_model.return_value = model
            with patch("core.voice.shutil.which", return_value="/usr/bin/ffmpeg"):
                with patch.dict(sys.modules, {"whisper": fake_whisper}):
                    WhisperTranscriber._loaded_models.clear()
                    transcriber = WhisperTranscriber()
                    result = transcriber.transcribe(str(audio_path))
            load_model = fake_whisper.load_model

            load_model.assert_called_once_with("large", device="cpu")
            model.transcribe.assert_called_once_with(str(audio_path), language="tr", fp16=False)
            self.assertEqual(result["text"], "Merhaba")

    def test_elevenlabs_synthesizer_writes_mp3_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "reply.mp3"

            os.environ["ELEVENLABS_API_KEY"] = "test-key"
            os.environ["ELEVENLABS_VOICE_ID"] = "voice-123"
            os.environ["ELEVENLABS_MODEL_ID"] = "eleven_multilingual_v2"
            Config._instance = None

            synthesizer = ElevenLabsSynthesizer()

            with patch(
                "core.voice.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(b"mp3-bytes"),
            ) as urlopen:
                result_path = synthesizer.synthesize("Merhaba dunya", output_path=str(output_path))

            self.assertEqual(result_path, str(output_path))
            self.assertEqual(output_path.read_bytes(), b"mp3-bytes")
            request = urlopen.call_args.args[0]
            self.assertIn("/text-to-speech/voice-123", request.full_url)
            self.assertEqual(request.headers["Xi-api-key"], "test-key")

    def test_voice_turn_processor_runs_transcribe_then_agent_then_tts(self):
        orchestrator = AgentOrchestrator()
        orchestrator.process_turn = MagicMock(
            return_value={
                "user_input": "merhaba",
                "agent_response": "Size nasil yardimci olabilirim?",
                "router_decision": {"tool": "answer_general", "args": {"category": "greeting"}},
                "context": {"handoff": {"required": False, "reason": "", "missing_info": []}},
            }
        )

        transcriber = MagicMock()
        transcriber.transcribe.return_value = {
            "text": "merhaba",
            "language": "tr",
            "segments": [],
            "source_audio_path": "input.wav",
        }
        synthesizer = MagicMock()
        synthesizer.synthesize.return_value = "reply.mp3"

        processor = VoiceTurnProcessor(
            orchestrator=orchestrator,
            transcriber=transcriber,
            synthesizer=synthesizer,
        )

        result = processor.process_audio_turn("input.wav", user_id="alice", output_audio_path="reply.mp3")

        orchestrator.process_turn.assert_called_once_with("merhaba", user_id="alice", channel="voice")
        synthesizer.synthesize.assert_called_once_with(
            "Size nasil yardimci olabilirim?",
            output_path="reply.mp3",
        )
        self.assertEqual(result["reply_audio_path"], "reply.mp3")


if __name__ == "__main__":
    unittest.main()

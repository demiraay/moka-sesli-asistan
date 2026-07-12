"""Sesli arama HTTP katmani testleri (STT/TTS/LLM sahte)."""

import io
import json

import pytest

from core import voice as voice_module
from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator


class FakeLLM:
    def __init__(self):
        self.router_queue = []

    def push(self, tool, args=None, card=None):
        self.router_queue.append(json.dumps({"tool": tool, "args": args or {}, "card": card or {}}))

    def generate(self, system_prompt, user_prompt, json_mode=False, profile="default"):
        if json_mode:
            return self.router_queue.pop(0) if self.router_queue else json.dumps(
                {"tool": "answer_general", "args": {}, "card": {}}
            )
        return "ok cevap"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # STT: sabit Turkce metin; TTS: bos mp3 dosyasi yazar.
    monkeypatch.setattr(
        voice_module.CompositeTranscriber,
        "transcribe",
        lambda self, path, language=None: {"text": "Param ne zaman yatacak?", "engine": "fake"},
    )
    monkeypatch.setattr(
        voice_module.ElevenLabsSynthesizer, "is_configured", lambda self: True
    )

    def fake_synthesize(self, text, output_path=None, voice_id=None, model_id=None):
        from pathlib import Path
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ID3fakemp3")
        return str(target)

    monkeypatch.setattr(voice_module.ElevenLabsSynthesizer, "synthesize", fake_synthesize)

    from core.config import Config
    monkeypatch.setattr(Config(), "voice_output_dir", str(tmp_path / "voice_out"))

    orch = AgentOrchestrator()
    orch.admin_store = AdminStore(db_path=str(tmp_path / "test.sqlite3"))
    orch.llm_client = FakeLLM()
    orch.conversation_histories.clear()
    orch.user_profiles.clear()
    orch.active_sessions.clear()

    from admin_panel.app import create_app
    app = create_app(store=orch.admin_store, orchestrator=orch)
    app.config["TESTING"] = True
    return app.test_client(), orch


def test_call_start_inbound(client):
    http, orch = client
    resp = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["call_id"].startswith("call-")
    assert "Mehmet Bey" in data["reply_text"]
    assert data["audio_url"].startswith("/call/audio/")
    # Uretilen ses dosyasi gercekten servis ediliyor mu?
    audio = http.get(data["audio_url"])
    assert audio.status_code == 200


def test_call_turn_with_text_input(client):
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("get_settlement_status", {"period": "latest"})

    resp = http.post("/call/turn", data={"call_id": start["call_id"], "text": "Param ne zaman yatacak?"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tool"] == "get_settlement_status"
    assert data["reply_text"].lower() == "ok cevap"
    assert data["handoff"] is False
    assert data["latency_ms"]["total"] >= 0


def test_call_turn_with_audio_upload(client):
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("get_settlement_status", {"period": "latest"})

    resp = http.post(
        "/call/turn",
        data={
            "call_id": start["call_id"],
            "audio": (io.BytesIO(b"fake-webm-bytes"), "turn.webm"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["transcript"] == "Param ne zaman yatacak?"
    assert data["stt_engine"] == "fake"
    assert data["audio_url"].startswith("/call/audio/")


def test_call_turn_handoff_flag(client):
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("trigger_handoff", {"reason": "Öfkeli müşteri"})

    resp = http.post("/call/turn", data={"call_id": start["call_id"], "text": "Yeter artık!"})
    data = resp.get_json()
    assert data["handoff"] is True


def test_call_audio_rejects_traversal(client):
    http, _ = client
    resp = http.get("/call/audio/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_call_end_outbound_logs_event(client):
    http, orch = client
    start = http.post("/call/start", json={"mode": "outbound", "merchant_id": "M-1007"}).get_json()
    resp = http.post("/call/end", json={
        "call_id": start["call_id"], "mode": "outbound", "outcome": "offer_accepted",
    })
    assert resp.get_json()["ok"] is True
    events = orch.admin_store.get_lead_events(start["call_id"])
    assert any(e["event_type"] == "outbound_call_ended" for e in events)

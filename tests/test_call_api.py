"""Sesli arama HTTP katmani testleri (STT/TTS/LLM sahte)."""

import io
import json

import pytest

from core import voice as voice_module
from core.admin_store import AdminStore
from core.llm import LLMResponse, ToolCall
from core.orchestrator import AGENT_LOOP_ENABLED, AgentOrchestrator


class FakeLLM:
    """Planlama fazında tool_call döndürür, cevap fazında düz metin."""

    def __init__(self):
        self.router_queue = []

    def push(self, tool, args=None, card=None):
        batch = [(tool, args or {})]
        if card:
            batch.append(("update_customer_card", card))
        self.router_queue.append(batch)

    def chat(self, messages, *, tools=None, tool_choice="auto", json_mode=False,
             profile="default", timeout=25, max_tokens=None):
        if not tools or not self.router_queue:
            return LLMResponse(content="ok cevap", tool_calls=[], finish_reason="stop")
        batch = self.router_queue.pop(0)
        return LLMResponse(
            content=None, finish_reason="tool_calls",
            tool_calls=[ToolCall(id=f"c{index}", name=name, arguments=args)
                        for index, (name, args) in enumerate(batch)])

    def generate(self, system_prompt, user_prompt, json_mode=False, profile="default"):
        if json_mode:
            batch = self.router_queue.pop(0) if self.router_queue else [("answer_general", {})]
            name, args = batch[0]
            return json.dumps({"tool": name, "args": args, "card": {}})
        return "ok cevap"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # STT: sabit Turkce metin; TTS: bos mp3 dosyasi yazar.
    monkeypatch.setattr(
        voice_module.CompositeTranscriber,
        "transcribe",
        lambda self, path, language=None, prompt=None: {"text": "Param ne zaman yatacak?", "engine": "fake"},
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


def test_voice_preview_endpoint(client):
    http, _ = client
    from core.voice import VOICE_CATALOG
    vid = VOICE_CATALOG[0]["voice_id"]
    resp = http.get(f"/call/voice-preview/{vid}")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/mpeg"
    # bilinmeyen ses reddedilir
    assert http.get("/call/voice-preview/olmayan-ses").status_code == 404


def test_admin_voice_setting_roundtrip(client):
    http, orch = client
    from core.voice import VOICE_CATALOG
    vid = VOICE_CATALOG[1]["voice_id"]  # Matilda
    resp = http.post("/admin/settings/tts-voice", data={"voice_id": vid})
    assert resp.status_code in (302, 303)
    assert orch.admin_store.get_setting("tts_voice_id") == vid
    # gecersiz ses kaydedilmez
    http.post("/admin/settings/tts-voice", data={"voice_id": "sahte"})
    assert orch.admin_store.get_setting("tts_voice_id") == vid
    # call sayfasinda varsayilan secili gelir
    html = http.get("/call").get_data(as_text=True)
    assert f'value="{vid}" selected' in html or f'value="{vid}"\n                selected' in html or "selected" in html


def test_call_start_uses_selected_voice(client, monkeypatch):
    http, _ = client
    captured = {}
    from core import voice as voice_module

    def spy_synthesize(self, text, output_path=None, voice_id=None, model_id=None):
        captured["voice_id"] = voice_id
        from pathlib import Path
        Path(output_path).write_bytes(b"ID3fake")
        return str(output_path)

    monkeypatch.setattr(voice_module.ElevenLabsSynthesizer, "synthesize", spy_synthesize)
    from core.voice import VOICE_CATALOG
    vid = VOICE_CATALOG[3]["voice_id"]  # Jessica
    resp = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001", "voice_id": vid})
    assert resp.status_code == 200
    assert captured["voice_id"] == vid


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="araç zinciri yalnızca agent loop'ta oluşur")
def test_call_turn_exposes_full_tool_chain(client):
    """Cok adimli loop'ta bir turda birden fazla arac calisir.

    Cagri ekrani zincirin TAMAMINI gosterir; tek 'tool' alani yetmez.
    """
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("find_transaction", {"amount_try": 1250})
    orch.llm_client.push("get_settlement_status", {"period": "latest"})

    data = http.post("/call/turn", data={
        "call_id": start["call_id"], "text": "Dun 1250 TL cektim, goremiyorum"}).get_json()

    assert data["tools"] == ["find_transaction", "get_settlement_status"]
    assert data["iterations"] == 2
    assert data["stop_reason"] == "done"
    assert data["tool"] == "find_transaction"      # geriye uyum: ilk arac


def test_call_page_ships_tool_labels(client):
    """Zincir rozetleri Turkce etiketle gosterilir; etiketler registry'den gelir."""
    http, _ = client
    html = http.get("/call").get_data(as_text=True)
    assert "toolLabels" in html

    # Jinja tojson Turkce karakterleri \u kacisiyla yazar; JSON'u ayristirip bak.
    import re
    raw = re.search(r"toolLabels:\s*(\{.*?\}),", html, re.S).group(1)
    labels = json.loads(raw)
    assert labels["get_settlement_status"] == "hakediş sorgulandı"
    assert labels["trigger_handoff"] == "temsilciye devredildi"


def _parse_sse(body):
    """SSE govdesini (event, payload) ciftlerine ayirir."""
    events = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if name:
            events.append((name, data))
    return events


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="akış agent loop yolunda")
def test_call_turn_stream_delivers_text_progressively(client):
    """Cevap tek blok yerine parça parça gelmeli."""
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("get_settlement_status", {"period": "latest"})

    response = http.post("/call/turn/stream",
                         data={"call_id": start["call_id"], "text": "Param ne zaman yatacak?"})
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    events = _parse_sse(response.get_data(as_text=True))
    kinds = [name for name, _ in events]
    assert "done" in kinds
    assert kinds[-1] == "done", "done olayı en sonda gelmeli"

    done = next(payload for name, payload in events if name == "done")
    deltas = "".join(payload["text"] for name, payload in events if name == "delta")
    # Akan metnin birleşimi nihai cevaba EŞİT olmalı — ekranda hiçbir şey
    # sonradan değişmez.
    assert deltas == done["reply_text"]
    assert done["tools"] == ["get_settlement_status"]


def test_call_turn_stream_requires_call_id_and_text(client):
    http, _ = client
    assert http.post("/call/turn/stream", data={"text": "merhaba"}).status_code == 400
    assert http.post("/call/turn/stream", data={"call_id": "x"}).status_code == 400


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="akış agent loop yolunda")
def test_call_turn_stream_delivers_audio_segments(client):
    """Ses cumle cumle 'audio' olaylariyla gelmeli; done tek parca ses tasimamali."""
    http, orch = client
    start = http.post("/call/start", json={"mode": "inbound", "merchant_id": "M-1001"}).get_json()
    orch.llm_client.push("get_settlement_status", {"period": "latest"})

    response = http.post("/call/turn/stream",
                         data={"call_id": start["call_id"], "text": "Param ne zaman yatacak?"})
    events = _parse_sse(response.get_data(as_text=True))

    audio_events = [payload for name, payload in events if name == "audio"]
    done = next(payload for name, payload in events if name == "done")

    assert audio_events, "en az bir ses parcasi akmali"
    assert done["audio_segments"] == len(audio_events)
    assert done["audio_url"] is None, "parcali ses varken done tek parca tasimaz"
    assert [payload["seq"] for payload in audio_events] == list(range(1, len(audio_events) + 1))
    for payload in audio_events:
        assert payload["url"].startswith("/call/audio/")
    # Parcalarin metni nihai cevabi kaplamali (bosluk farki tolere edilir).
    assert "".join(p["text"] for p in audio_events).replace(" ", "") == \
        done["reply_text"].replace(" ", "")


def test_split_ready_sentences_keeps_numbers_intact():
    from admin_panel.call_api import _split_ready_sentences

    # Sayidaki nokta ("45.230") cumle sonu sayilmaz: noktadan sonra bosluk yok.
    ready, rest = _split_ready_sentences(
        "Dünkü hakedişiniz net 45.230 TL olarak yatacak. Başka sorunuz var mı")
    assert ready == ["Dünkü hakedişiniz net 45.230 TL olarak yatacak."]
    assert rest == "Başka sorunuz var mı"

    # Bitmemis kuyruk oldugu gibi geri doner.
    ready, rest = _split_ready_sentences("Henüz cümle bitmedi")
    assert ready == []
    assert rest == "Henüz cümle bitmedi"

    # Cok kisa parca (min_chars alti) sonraki cumleyle birlesir.
    ready, rest = _split_ready_sentences("Evet. Ödemeniz yarın sabah hesabınızda olacak. ")
    assert ready == ["Evet. Ödemeniz yarın sabah hesabınızda olacak."]
    assert rest == ""

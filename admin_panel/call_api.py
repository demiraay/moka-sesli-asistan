"""Sesli arama HTTP katmani.

Tarayicidaki "telefon" ekrani (call.html) bu uclarla konusur:
  GET  /call                  — arama ekrani (inbound/outbound)
  POST /call/start            — cagriyi baslatir; selamlama metni + sesi doner
  POST /call/turn             — bir konusma turu: ses (webm) al, STT->agent->TTS, yanit ver
  GET  /call/audio/<name>     — uretilen mp3'leri servis eder
  POST /call/end              — cagriyi kapatir (outbound sonucu loglanir)

/call/turn ayrica `text` alaniyla metin girisini de kabul eder (mikrofonsuz
test ve otomatik testler icin STT atlanir).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from flask import jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from core.config import Config
from core.voice import CompositeTranscriber, ElevenLabsSynthesizer


def register_call_routes(app, orchestrator) -> None:
    config = Config()
    transcriber = CompositeTranscriber(config)
    synthesizer = ElevenLabsSynthesizer(config)
    voice_dir = Path(config.voice_output_dir)
    voice_dir.mkdir(parents=True, exist_ok=True)

    def _synthesize(text: str, prefix: str) -> tuple[str | None, int]:
        """TTS calistirir; (audio_url, sure_ms) doner. Yapilandirilmamissa None."""
        if not synthesizer.is_configured():
            return None, 0
        started = time.perf_counter()
        filename = f"{prefix}-{uuid.uuid4().hex[:10]}.mp3"
        try:
            synthesizer.synthesize(text, output_path=str(voice_dir / filename))
        except Exception as error:
            print(f"TTS warning: {error}")
            return None, int((time.perf_counter() - started) * 1000)
        return f"/call/audio/{filename}", int((time.perf_counter() - started) * 1000)

    @app.route("/call")
    def call_page():
        mode = request.args.get("mode", "inbound")
        merchant_id = request.args.get("merchant_id", "")
        merchants = [
            {
                "merchant_id": m.get("merchant_id"),
                "business_name": m.get("business_name"),
                "owner_name": m.get("owner_name"),
            }
            for m in config.merchants
        ]
        return render_template(
            "call.html",
            mode=mode,
            merchant_id=merchant_id,
            merchants=merchants,
        )

    @app.route("/call/start", methods=["POST"])
    def call_start():
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode", "inbound")
        merchant_id = payload.get("merchant_id") or None
        goal = payload.get("goal") or None

        # Her cagri taze bir oturum acar: demo sirasinda onceki konusma sizmasin.
        call_id = f"call-{uuid.uuid4().hex[:8]}"

        started = time.perf_counter()
        result = orchestrator.start_call(
            call_id, channel="voice", mode=mode, merchant_id=merchant_id, goal=goal
        )
        llm_ms = int((time.perf_counter() - started) * 1000)
        audio_url, tts_ms = _synthesize(result["reply_text"], f"greet-{call_id}")

        return jsonify({
            "call_id": call_id,
            "reply_text": result["reply_text"],
            "audio_url": audio_url,
            "merchant": result.get("merchant"),
            "mode": mode,
            "latency_ms": {"llm": llm_ms, "tts": tts_ms},
        })

    @app.route("/call/turn", methods=["POST"])
    def call_turn():
        call_id = request.form.get("call_id") or request.args.get("call_id")
        if not call_id:
            return jsonify({"error": "call_id gerekli"}), 400

        text_input = (request.form.get("text") or "").strip()
        stt_ms = 0
        transcript = text_input
        stt_engine = "text"

        if not text_input:
            audio_file = request.files.get("audio")
            if audio_file is None:
                return jsonify({"error": "audio dosyasi ya da text alani gerekli"}), 400
            suffix = Path(secure_filename(audio_file.filename or "turn.webm")).suffix or ".webm"
            input_path = voice_dir / f"in-{uuid.uuid4().hex[:10]}{suffix}"
            audio_file.save(input_path)

            started = time.perf_counter()
            try:
                transcription = transcriber.transcribe(str(input_path))
            except Exception as error:
                print(f"STT error: {error}")
                return jsonify({"error": "Ses cozumlenemedi", "detail": str(error)}), 502
            stt_ms = int((time.perf_counter() - started) * 1000)
            transcript = transcription.get("text", "").strip()
            stt_engine = transcription.get("engine", "unknown")
            if not transcript:
                return jsonify({
                    "transcript": "",
                    "reply_text": None,
                    "audio_url": None,
                    "empty": True,
                    "latency_ms": {"stt": stt_ms, "llm": 0, "tts": 0},
                })

        started = time.perf_counter()
        turn = orchestrator.process_turn(transcript, user_id=call_id, channel="voice")
        llm_ms = int((time.perf_counter() - started) * 1000)

        reply_text = turn["agent_response"]
        audio_url, tts_ms = _synthesize(reply_text, f"reply-{call_id}")

        handoff = bool((turn.get("context") or {}).get("handoff", {}).get("required"))
        return jsonify({
            "transcript": transcript,
            "reply_text": reply_text,
            "audio_url": audio_url,
            "tool": (turn.get("router_decision") or {}).get("tool"),
            "handoff": handoff,
            "stt_engine": stt_engine,
            "latency_ms": {
                "stt": stt_ms,
                "llm": llm_ms,
                "tts": tts_ms,
                "total": stt_ms + llm_ms + tts_ms,
            },
        })

    @app.route("/call/audio/<name>")
    def call_audio(name: str):
        safe_name = secure_filename(name)
        if safe_name != name or not safe_name.endswith(".mp3"):
            return jsonify({"error": "gecersiz dosya"}), 400
        return send_from_directory(voice_dir, safe_name, mimetype="audio/mpeg")

    @app.route("/call/end", methods=["POST"])
    def call_end():
        payload = request.get_json(silent=True) or {}
        call_id = payload.get("call_id")
        outcome = payload.get("outcome") or "ended"
        mode = payload.get("mode") or "inbound"
        if call_id and mode == "outbound":
            try:
                orchestrator.admin_store.record_lead_event(
                    call_id, "outbound_call_ended", {"outcome": outcome}
                )
            except Exception as error:
                print(f"Call end log warning: {error}")

        # Kapanis ozeti: transkript paneline "cagri ozeti" olarak dusurulur.
        summary = ""
        try:
            notes = orchestrator.admin_store.get_user_ai_notes(call_id) if call_id else {}
            summary = notes.get("ai_summary") or ""
        except Exception as error:
            print(f"Call summary warning: {error}")
        return jsonify({"ok": True, "summary": summary})

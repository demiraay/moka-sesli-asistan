import hmac
import os

from flask import Flask, jsonify, request

from core.orchestrator import AgentOrchestrator
from core.phone_utils import normalize_phone_number


orchestrator = AgentOrchestrator()


def _build_whatsapp_follow_up_payload(active_orchestrator: AgentOrchestrator, result: dict) -> dict:
    handoff = (result.get("context") or {}).get("handoff") or {}
    if not handoff.get("required"):
        return {"sales_profile": None, "follow_up_actions": []}

    profile = active_orchestrator.admin_store.get_sales_profile()
    if not any(
        profile.get(key)
        for key in ("consultant_name", "phone_number", "whatsapp_number", "office_name", "office_address", "maps_url", "latitude", "longitude")
    ):
        return {"sales_profile": None, "follow_up_actions": []}

    follow_up_actions = []
    share_contact_details = bool(handoff.get("share_contact_details"))
    share_location = bool(handoff.get("share_location"))
    contact_message_parts = []
    if profile.get("consultant_name"):
        title = profile.get("consultant_title") or "Satis danismani"
        contact_message_parts.append(f"{title}: {profile['consultant_name']}")
    if profile.get("phone_number"):
        contact_message_parts.append(f"Telefon: {profile['phone_number']}")
    if profile.get("whatsapp_number"):
        contact_message_parts.append(f"WhatsApp: {profile['whatsapp_number']}")
    if profile.get("office_address"):
        contact_message_parts.append(f"Adres: {profile['office_address']}")
    if profile.get("maps_url"):
        contact_message_parts.append(f"Harita: {profile['maps_url']}")

    if share_contact_details and contact_message_parts:
        follow_up_actions.append(
            {
                "type": "text",
                "message": "\n".join(contact_message_parts),
            }
        )

    if share_location and profile.get("auto_share_whatsapp_location") and profile.get("latitude") and profile.get("longitude"):
        follow_up_actions.append(
            {
                "type": "location",
                "label": profile.get("location_label") or profile.get("office_name") or "Satis Ofisi",
                "latitude": profile["latitude"],
                "longitude": profile["longitude"],
            }
        )

    safe_profile = {
        key: value
        for key, value in profile.items()
        if key != "updated_at"
    }
    return {
        "sales_profile": safe_profile,
        "follow_up_actions": follow_up_actions,
    }


def _bridge_token_valid() -> bool:
    """WHATSAPP_BRIDGE_TOKEN ayarliysa istekte ayni token'i zorunlu kilar.
    Token bos birakilirsa kopru korumasiz calisir (sadece gelistirme icin)."""
    expected = os.getenv("WHATSAPP_BRIDGE_TOKEN", "").strip()
    if not expected:
        return True

    provided = (request.headers.get("X-Bridge-Token") or "").strip()
    if not provided:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer "):].strip()

    return hmac.compare_digest(provided, expected)


def _extract_payload() -> tuple[str, str]:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        phone = payload.get("phone_number") or payload.get("from") or payload.get("user_id") or ""
        message = payload.get("message") or payload.get("body") or ""
        return normalize_phone_number(phone), str(message).strip()

    phone = request.form.get("From") or request.form.get("phone_number") or request.form.get("from") or ""
    message = request.form.get("Body") or request.form.get("message") or ""
    return normalize_phone_number(phone), str(message).strip()


def register_whatsapp_routes(app: Flask, orchestrator_instance: AgentOrchestrator | None = None) -> Flask:
    active_orchestrator = orchestrator_instance or orchestrator

    @app.get("/whatsapp/health")
    def health() -> tuple[dict, int]:
        return {"ok": True}, 200

    @app.post("/whatsapp/message")
    def receive_message():
        if not _bridge_token_valid():
            return jsonify({"error": "unauthorized"}), 401

        user_id, message = _extract_payload()
        if not message:
            return jsonify({"error": "message is required"}), 400

        store = active_orchestrator.admin_store

        # Istisna listesindeki numaralara AI ASLA yanit uretmez (kalici engel).
        # Ne LLM calisir ne de cevap doner; mesaj sessizce yok sayilir.
        if store.is_blocked(user_id):
            return jsonify(
                {
                    "user_id": user_id,
                    "phone_number": user_id,
                    "reply": None,
                    "blocked": True,
                    "router_decision": {"tool": "blocked", "args": {}},
                    "context": {},
                    "ai_summary": "",
                    "sales_profile": None,
                    "follow_up_actions": [],
                }
            )

        # Insan devralmissa AI cevap uretmez; mesaj yalnizca gecmise islenir
        # ve panel canli devralma ekraninda gorunur.
        if store.is_ai_paused(user_id):
            session_id = store.get_latest_session_id_for_user(user_id, "whatsapp") or f"manual-{user_id}"
            store.log_turn(
                session_id=session_id,
                user_id=user_id,
                channel="whatsapp",
                user_input=message,
                agent_response="",
                router_decision={"tool": "human_takeover", "args": {}},
                context={"message_facts": [], "units": [], "alternatives": [], "price_info": None,
                         "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
            )
            return jsonify(
                {
                    "user_id": user_id,
                    "phone_number": user_id,
                    "reply": None,
                    "paused": True,
                    "router_decision": {"tool": "human_takeover", "args": {}},
                    "context": {},
                    "ai_summary": "",
                    "sales_profile": None,
                    "follow_up_actions": [],
                }
            )

        result = active_orchestrator.process_turn(
            user_input=message,
            user_id=user_id,
            channel="whatsapp",
        )
        notes = store.get_user_ai_notes(user_id)
        follow_up = _build_whatsapp_follow_up_payload(active_orchestrator, result)

        return jsonify(
            {
                "user_id": user_id,
                "phone_number": user_id,
                "reply": result["agent_response"],
                "router_decision": result["router_decision"],
                "context": result["context"],
                "ai_summary": notes.get("ai_summary", ""),
                "sales_profile": follow_up["sales_profile"],
                "follow_up_actions": follow_up["follow_up_actions"],
            }
        )

    @app.get("/whatsapp/outbox")
    def outbox():
        if not _bridge_token_valid():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"messages": active_orchestrator.admin_store.list_pending_outbound()})

    @app.post("/whatsapp/outbox/<int:outbox_id>/ack")
    def outbox_ack(outbox_id: int):
        if not _bridge_token_valid():
            return jsonify({"error": "unauthorized"}), 401
        payload = request.get_json(silent=True) or {}
        try:
            active_orchestrator.admin_store.mark_outbound_sent(outbox_id, ok=bool(payload.get("ok", True)))
        except KeyError as error:
            return jsonify({"error": str(error)}), 404
        return jsonify({"ok": True})

    return app


def create_app(orchestrator_instance: AgentOrchestrator | None = None) -> Flask:
    app = Flask(__name__)
    register_whatsapp_routes(app, orchestrator_instance=orchestrator_instance)
    return app


if __name__ == "__main__":
    app = create_app()
    host = os.getenv("WHATSAPP_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("WHATSAPP_BRIDGE_PORT", "5051"))
    debug = os.getenv("WHATSAPP_BRIDGE_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)

import os
import secrets
import time
from pathlib import Path

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for

from core.admin_store import AdminStore
from core.briefing import generate_briefing
from core.orchestrator import AgentOrchestrator
from whatsapp.app import register_whatsapp_routes
from admin_panel.call_api import register_call_routes


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Panel ici test sohbetinin sabit kimligi; konusmalar bu kullanici altinda loglanir.
PANEL_CHAT_USER_ID = "panel-test"
PANEL_CHAT_CHANNEL = "panel"



def _format_tl(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:,} TL".replace(",", ".")




def _conversation_filters_from_request() -> dict:
    return {
        "query": request.args.get("q", "").strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "handoff_only": request.args.get("handoff_only") == "1",
        "price_only": request.args.get("price_only") == "1",
        "flat_type": request.args.get("flat_type", "").strip(),
        "channel": request.args.get("channel", "").strip(),
    }


def create_app(
    store: AdminStore | None = None,
    orchestrator: AgentOrchestrator | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/admin-static",
    )
    app.secret_key = os.getenv("ADMIN_SECRET_KEY") or secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # foto yukleme ust limiti

    app.add_template_filter(_format_tl, "tl")

    admin_store = store or AdminStore()
    active_orchestrator = orchestrator or AgentOrchestrator()
    active_orchestrator.admin_store = admin_store
    app.extensions["admin_store"] = admin_store
    app.extensions["orchestrator"] = active_orchestrator

    # Nav rozetleri (bekleyen handoff / açık görev). get_handoff_queue tüm turn
    # tablosunu taradığı için sayılar 30 sn'lik cache'ten servis edilir.
    nav_badge_cache = {"at": 0.0, "data": {"handoff": 0, "tasks": 0}}

    def _invalidate_nav_badges() -> None:
        nav_badge_cache["at"] = 0.0

    @app.context_processor
    def inject_nav_badges():
        now = time.monotonic()
        if now - nav_badge_cache["at"] > 30.0:
            try:
                nav_badge_cache["data"] = {
                    "handoff": len(admin_store.get_handoff_queue()),
                    "tasks": len(admin_store.list_tasks(include_done=False)),
                }
            except Exception:
                pass  # rozet hesabi sayfa render'ini asla dusurmesin
            nav_badge_cache["at"] = now
        return {"nav_badges": nav_badge_cache["data"]}

    # Panel kimligi (marka + footer ofis bilgisi) her sayfaya tasinir; sales_profile
    # tek satirlik indexli okuma, proje adi JSON'dan.
    @app.context_processor
    def inject_css_version():
        # CSS degisikligi tarayici onbellegine takilmasin diye dosya mtime'i
        # ile surumler; her guncelleme otomatik taze gelir.
        try:
            return {"css_version": int(os.path.getmtime(STATIC_DIR / "admin.css"))}
        except OSError:
            return {"css_version": 0}

    @app.context_processor
    def inject_panel_identity():
        project_name = "Voice Agent Admin"
        try:
            projects = admin_store._load_json("projects.json")
            if projects:
                project_name = projects[0].get("name") or project_name
        except Exception:
            pass
        try:
            office = admin_store.get_sales_profile()
        except Exception:
            office = {}
        return {"panel_project": project_name, "panel_office": office}

    @app.before_request
    def _require_admin_auth():
        password = os.getenv("ADMIN_PASSWORD", "").strip()
        if not password:
            return None
        if not (request.path == "/" or request.path.startswith("/admin")
                or request.path.startswith("/call")):
            return None
        auth = request.authorization
        if auth and auth.type == "basic" and auth.password == password:
            return None
        return Response(
            "Giris gerekli.",
            401,
            {"WWW-Authenticate": 'Basic realm="Voice Agent Admin"'},
        )

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/admin")
    def dashboard():
        dormant = active_orchestrator.merchant_data.list_dormant_merchants()
        return render_template(
            "dashboard.html",
            analytics=admin_store.get_dashboard_analytics(),
            revenue=admin_store.get_revenue_kpis(),
            dormant_count=len(dormant),
            dormant_risk_try=sum(m.get("lost_volume_try", 0) for m in dormant),
            sales_profile=admin_store.get_sales_profile(),
            followups=admin_store.get_followup_tasks()[:4],
            open_tasks=admin_store.list_tasks()[:4],
            briefing=admin_store.get_latest_briefing(),
        )

    @app.route("/admin/outbound")
    def outbound():
        """Uyuyan isletmeler: hacmi dusen musteriler + tek tikla AI kurtarma aramasi."""
        dormant = active_orchestrator.merchant_data.list_dormant_merchants()
        # Son arama sonuclari: outbound cagri kayitlari lead_events'te call-id
        # kullanicilarinda durur; basitce panelde gosterilmez (PoC kapsami disi).
        return render_template(
            "outbound.html",
            dormant=dormant,
            total_risk_try=sum(m.get("lost_volume_try", 0) for m in dormant),
            recovered_ids=admin_store.get_recovered_merchant_ids(),
        )

    @app.post("/admin/briefing/generate")
    def generate_briefing_route():
        try:
            generate_briefing(admin_store, active_orchestrator.llm_client)
            flash("Günün brifingi hazır.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("dashboard"))

    @app.route("/admin/tasks", methods=["GET", "POST"])
    def tasks():
        if request.method == "POST":
            try:
                admin_store.create_task(
                    title=request.form.get("title", ""),
                    user_id=request.form.get("user_id", ""),
                    due_at=request.form.get("due_at", "").strip() or None,
                )
                _invalidate_nav_badges()
                flash("Görev eklendi.", "success")
            except ValueError as error:
                flash(str(error), "error")
            return redirect(url_for("tasks"))

        return render_template(
            "tasks.html",
            followups=admin_store.get_followup_tasks(),
            manual_tasks=admin_store.list_tasks(include_done=True),
        )

    @app.post("/admin/tasks/<int:task_id>/toggle")
    def toggle_task(task_id: int):
        done = request.form.get("done") == "1"
        try:
            admin_store.set_task_done(task_id, done)
            _invalidate_nav_badges()
        except KeyError:
            flash("Görev bulunamadı.", "error")
        return redirect(url_for("tasks"))

    @app.route("/admin/analytics")
    def analytics():
        return render_template(
            "analytics.html",
            report=admin_store.get_analytics_report(),
            revenue=admin_store.get_revenue_kpis(),
        )

    @app.route("/admin/analytics/export/leads.csv")
    def export_leads_csv():
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "user_id", "isim", "asama", "sicaklik", "skor", "konu",
            "isletme_id", "handoff_bekliyor", "oturum_sayisi", "son_temas",
        ])
        for lead in admin_store.get_leads():
            writer.writerow([
                lead["user_id"], lead.get("name") or "", lead["stage"], lead["temperature"],
                lead["score"], (lead.get("ai_summary") or "")[:80], lead.get("merchant_id") or "",
                "evet" if lead.get("handoff_required") else "hayir",
                lead.get("conversation_count") or 0, lead.get("last_contact_at") or "",
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=musteri-adaylari.csv"},
        )

    @app.route("/admin/analytics/export/conversations.csv")
    def export_conversations_csv():
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["session_id", "user_id", "kanal", "tur_sayisi", "baslangic", "son_mesaj"])
        for item in admin_store.list_conversations():
            writer.writerow([
                item["session_id"], item["user_id"], item["channel"],
                item["turn_count"], item["created_at"], item["last_message_at"],
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=konusmalar.csv"},
        )

    @app.route("/admin/chat")
    def chat():
        return render_template(
            "chat.html",
            history=active_orchestrator.get_history(PANEL_CHAT_USER_ID, PANEL_CHAT_CHANNEL),
        )

    @app.post("/admin/chat/message")
    def chat_message():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            return {"error": "message is required"}, 400

        result = active_orchestrator.process_turn(
            user_input=message,
            user_id=PANEL_CHAT_USER_ID,
            channel=PANEL_CHAT_CHANNEL,
        )
        return {
            "reply": result["agent_response"],
            "router_decision": result["router_decision"],
        }

    @app.post("/admin/chat/reset")
    def chat_reset():
        active_orchestrator.reset_conversation(PANEL_CHAT_USER_ID, PANEL_CHAT_CHANNEL)
        return {"ok": True}

    @app.route("/admin/conversations")
    def conversations():
        filters = _conversation_filters_from_request()
        return render_template(
            "conversations.html",
            user_groups=admin_store.list_users_with_conversations(filters=filters),
            filters=filters,
        )

    @app.route("/admin/sales-profile", methods=["GET", "POST"])
    def sales_profile():
        if request.method == "POST":
            admin_store.update_sales_profile(request.form.to_dict())
            flash("Temsilci profili güncellendi.", "success")
            return redirect(url_for("sales_profile"))

        return render_template(
            "sales_profile.html",
            sales_profile=admin_store.get_sales_profile(),
        )

    @app.route("/admin/exceptions", methods=["GET", "POST"])
    def exceptions():
        from core.phone_utils import normalize_phone_number

        if request.method == "POST":
            raw = request.form.get("user_id", "").strip()
            note = request.form.get("note", "").strip()
            # Telefon gibi gorunuyorsa normalize et; degilse ( or. @lid kimligi) aynen al.
            user_id = normalize_phone_number(raw) if any(c.isdigit() for c in raw) else raw
            if not user_id:
                flash("Geçerli bir numara/kimlik girin.", "error")
            else:
                admin_store.add_to_blocklist(user_id, note)
                flash(f"{user_id} istisna listesine eklendi — AI artik bu numarayla konusmayacak.", "success")
            return redirect(url_for("exceptions"))

        return render_template(
            "exceptions.html",
            blocked=admin_store.list_blocklist(),
        )

    @app.post("/admin/exceptions/<path:user_id>/remove")
    def remove_exception(user_id: str):
        admin_store.remove_from_blocklist(user_id)
        flash(f"{user_id} istisna listesinden cikarildi.", "success")
        return redirect(url_for("exceptions"))

    @app.route("/admin/leads")
    def leads():
        return render_template(
            "leads.html",
            leads=admin_store.get_leads(),
            stages=admin_store.LEAD_STAGES,
            handoff_queue=admin_store.get_handoff_queue(),
        )

    @app.post("/admin/leads/<path:user_id>/claim")
    def claim_handoff(user_id: str):
        admin_store.claim_handoff(user_id)
        _invalidate_nav_badges()
        if request.is_json:
            return {"ok": True, "user_id": user_id}
        flash(f"{user_id} devralindi.", "success")
        return redirect(url_for("leads"))

    @app.post("/admin/leads/<path:user_id>/stage")
    def update_lead_stage(user_id: str):
        stage = request.form.get("stage") or (request.get_json(silent=True) or {}).get("stage", "")
        try:
            admin_store.set_lead_stage(user_id, stage)
        except ValueError as error:
            if request.is_json:
                return {"error": str(error)}, 400
            flash(str(error), "error")
            return redirect(url_for("user_conversations", user_id=user_id))

        if request.is_json:
            return {"ok": True, "user_id": user_id, "stage": stage}
        flash("Aşama güncellendi.", "success")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.route("/admin/users/<path:user_id>/conversations")
    def user_conversations(user_id: str):
        filters = _conversation_filters_from_request()
        try:
            group = admin_store.get_user_conversations(user_id, filters=filters)
        except KeyError:
            abort(404)
        ai_notes = admin_store.get_user_ai_notes(user_id)

        live_turns = []
        latest_session = admin_store.get_latest_session_id_for_user(user_id, "whatsapp")
        if latest_session:
            try:
                live_turns = admin_store.get_conversation(latest_session)["turns"][-6:]
            except KeyError:
                live_turns = []

        return render_template(
            "user_conversations.html",
            group=group,
            filters=filters,
            ai_notes=ai_notes,
            lead_events=admin_store.get_lead_events(user_id),
            stages=admin_store.LEAD_STAGES,
            unit_matches=admin_store.get_unit_matches(ai_notes.get("ai_notes", {})),
            live_turns=live_turns,
        )

    @app.post("/admin/users/<path:user_id>/ai-pause")
    def toggle_ai_pause(user_id: str):
        paused = request.form.get("paused") == "1"
        admin_store.set_ai_paused(user_id, paused)
        flash("AI duraklatıldı — konuşmayı siz yürütüyorsunuz." if paused else "AI tekrar devrede.", "success")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.post("/admin/users/<path:user_id>/send-message")
    def send_message_to_user(user_id: str):
        message = request.form.get("message", "").strip()
        try:
            admin_store.enqueue_outbound_message(user_id, message, sender="panel")
            admin_store.log_human_message(user_id, message)
            flash("Mesaj kuyruğa alındı; WhatsApp botu birkaç saniye içinde gönderecek.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.route("/admin/users/<path:user_id>/notes", methods=["POST"])
    def update_user_notes(user_id: str):
        admin_store.update_manual_notes(user_id, request.form.get("manual_notes", "").strip())
        flash(f"{user_id} icin manuel not kaydedildi.", "success")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.route("/admin/conversations/<session_id>")
    def conversation_detail(session_id: str):
        try:
            payload = admin_store.get_conversation(session_id)
        except KeyError:
            abort(404)
        return render_template(
            "conversation_detail.html",
            session=payload["session"],
            turns=payload["turns"],
            ai_notes=admin_store.get_user_ai_notes(payload["session"]["user_id"]),
        )

    register_whatsapp_routes(app, orchestrator_instance=active_orchestrator)
    register_call_routes(app, active_orchestrator)

    return app

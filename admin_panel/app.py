import os
import secrets
import time
from pathlib import Path

from flask import Flask, Response, flash, redirect, render_template, request, send_from_directory, url_for

from core.admin_store import AdminStore
from core.briefing import generate_briefing
from core.orchestrator import AgentOrchestrator
from core.payment_plan import build_payment_plan
from whatsapp.app import register_whatsapp_routes
from admin_panel.call_api import register_call_routes


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Panel ici test sohbetinin sabit kimligi; konusmalar bu kullanici altinda loglanir.
PANEL_CHAT_USER_ID = "panel-test"
PANEL_CHAT_CHANNEL = "panel"

# Veri dosyalarindaki degerler agent'in arama sozlesmesi oldugu icin Ingilizce kalir
# (core/inventory.py, core/tools.py); Turkce yalnizca goruntuleme katmaninda uygulanir.
TR_STATUS_LABELS = {"available": "Satista", "reserved": "Rezerve", "sold": "Satildi"}
TR_DIRECTION_LABELS = {
    "North": "Kuzey",
    "South": "Guney",
    "East": "Dogu",
    "West": "Bati",
    "North-East": "Kuzeydogu",
    "North-West": "Kuzeybati",
    "South-East": "Guneydogu",
    "South-West": "Guneybati",
}
TR_SUN_LABELS = {"high": "Yuksek", "medium": "Orta", "low": "Az", "none": "Almiyor"}


def _format_tl(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:,} TL".replace(",", ".")


def _sniff_image_extension(data: bytes) -> str | None:
    """Icerigin gercekten resim olup olmadigini imzadan dogrular (uzanti yalan olabilir)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


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
    app.add_template_filter(lambda v: TR_STATUS_LABELS.get(v, v or "-"), "status_tr")
    app.add_template_filter(lambda v: TR_DIRECTION_LABELS.get(v, v or "-"), "direction_tr")
    app.add_template_filter(lambda v: TR_SUN_LABELS.get(v, v or "-"), "sun_tr")

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
        if not (request.path == "/" or request.path.startswith("/admin")):
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
        )

    @app.post("/admin/briefing/generate")
    def generate_briefing_route():
        try:
            generate_briefing(admin_store, active_orchestrator.llm_client)
            flash("Gunun brifingi hazir.", "success")
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
                flash("Gorev eklendi.", "success")
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
            flash("Gorev bulunamadi.", "error")
        return redirect(url_for("tasks"))

    @app.route("/admin/listings")
    def listings():
        query = request.args.get("q", "").strip()

        def _price_arg(name: str):
            raw = request.args.get(name, "").strip().replace(".", "").replace(",", "")
            return int(raw) if raw.isdigit() else None

        filters = {
            "block_id": request.args.get("block", "").strip(),
            "flat_type_id": request.args.get("flat_type", "").strip(),
            "status": request.args.get("status", "").strip(),
            "price_min": _price_arg("price_min"),
            "price_max": _price_arg("price_max"),
        }
        sort = request.args.get("sort", "").strip()
        all_listings = admin_store.list_listings(query=query, filters=filters, sort=sort or None)

        per_page = 30
        total_count = len(all_listings)
        total_pages = max(1, -(-total_count // per_page))
        try:
            page = max(1, min(int(request.args.get("page", "1")), total_pages))
        except ValueError:
            page = 1

        # Sayfa linkleri mevcut filtre/sıralama durumunu korur.
        filter_args = {k: v for k, v in request.args.items() if k != "page" and v}

        return render_template(
            "listings.html",
            listings=all_listings[(page - 1) * per_page : page * per_page],
            query=query,
            reservations=admin_store.get_active_reservations(),
            filters=filters,
            sort=sort,
            page=page,
            total_pages=total_pages,
            total_count=total_count,
            filter_args=filter_args,
            flat_options=admin_store.get_flat_options(),
            block_options=admin_store.get_block_options(),
            cover_map=admin_store.get_cover_photo_map(),
            price_changes=admin_store.get_recent_price_changes(),
            view=request.args.get("view", "table"),
        )

    @app.route("/admin/listings/export.csv")
    def export_listings_csv():
        import csv
        import io

        query = request.args.get("q", "").strip()

        def _price_arg(name: str):
            raw = request.args.get(name, "").strip().replace(".", "").replace(",", "")
            return int(raw) if raw.isdigit() else None

        filters = {
            "block_id": request.args.get("block", "").strip(),
            "flat_type_id": request.args.get("flat_type", "").strip(),
            "status": request.args.get("status", "").strip(),
            "price_min": _price_arg("price_min"),
            "price_max": _price_arg("price_max"),
        }
        sort = request.args.get("sort", "").strip() or None

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "ilan_no", "daire", "tip", "net_m2", "brut_m2", "fiyat_try",
            "durum", "blok", "kat", "kapi", "cephe", "gunes", "gunes_saati",
        ])
        for listing in admin_store.list_listings(query=query, filters=filters, sort=sort):
            writer.writerow([
                listing["inventory_id"],
                f"{listing['block_id']}-{listing['door_number']}",
                listing.get("flat_label") or "",
                (listing.get("flat") or {}).get("net_m2") or "",
                (listing.get("flat") or {}).get("gross_m2") or "",
                listing.get("list_price_try") or "",
                TR_STATUS_LABELS.get(listing.get("status"), listing.get("status")),
                listing.get("block_id"),
                listing.get("floor"),
                listing.get("door_number"),
                TR_DIRECTION_LABELS.get(listing.get("direction"), listing.get("direction")),
                TR_SUN_LABELS.get((listing.get("sunlight") or {}).get("sun_exposure"), ""),
                (listing.get("sunlight") or {}).get("sun_hours_per_day") or "",
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=ilanlar.csv"},
        )

    @app.route("/admin/listings/<inventory_id>")
    def listing_detail(inventory_id: str):
        try:
            listing = admin_store.get_listing(inventory_id)
        except KeyError:
            flash("Ilan bulunamadi.", "error")
            return redirect(url_for("listings"))
        block = next(
            (b for b in admin_store.get_block_options() if b["block_id"] == listing["block_id"]),
            {},
        )
        return render_template(
            "listing_detail.html",
            listing=listing,
            reservation=admin_store.get_active_reservations().get(inventory_id),
            history=admin_store.get_unit_history(inventory_id),
            block=block,
            photo_bundle=admin_store.get_photos_for_listing(listing),
            unit_photos=admin_store.list_photos("unit", inventory_id),
            matching_leads=admin_store.get_matching_leads_for_unit(listing),
            price_history=admin_store.get_price_history(inventory_id),
            block_price=admin_store.get_block_price_context(listing),
        )

    def _handle_photo_upload(scope: str, ref_id: str, redirect_target: str):
        saved = 0
        rejected = 0
        for file in request.files.getlist("photos"):
            if file is None or not file.filename:
                continue
            data = file.read()
            extension = _sniff_image_extension(data) if data else None
            if extension is None:
                rejected += 1
                continue
            admin_store.add_listing_photo(scope, ref_id, data, extension, original_name=file.filename)
            saved += 1
        if saved:
            flash(f"{saved} fotograf yuklendi.", "success")
        if rejected:
            flash(f"{rejected} dosya reddedildi (yalniz JPG/PNG/WebP).", "error")
        if not saved and not rejected:
            flash("Dosya secilmedi.", "error")
        return redirect(redirect_target)

    @app.post("/admin/listings/<inventory_id>/photos")
    def upload_listing_photos(inventory_id: str):
        try:
            admin_store.get_listing(inventory_id)
        except KeyError:
            flash("Ilan bulunamadi.", "error")
            return redirect(url_for("listings"))
        return _handle_photo_upload(
            "unit", inventory_id, url_for("listing_detail", inventory_id=inventory_id)
        )

    @app.post("/admin/listings/<inventory_id>/photos/<int:photo_id>/delete")
    def delete_listing_photo(inventory_id: str, photo_id: int):
        try:
            admin_store.delete_photo(photo_id)
            flash("Fotograf silindi.", "success")
        except KeyError:
            flash("Fotograf bulunamadi.", "error")
        return redirect(url_for("listing_detail", inventory_id=inventory_id))

    @app.post("/admin/listings/<inventory_id>/photos/<int:photo_id>/cover")
    def set_listing_cover(inventory_id: str, photo_id: int):
        try:
            admin_store.set_cover_photo(photo_id)
            flash("Kapak fotografi guncellendi.", "success")
        except KeyError:
            flash("Fotograf bulunamadi.", "error")
        return redirect(url_for("listing_detail", inventory_id=inventory_id))

    @app.route("/admin/flat-types/<flat_type_id>/photos", methods=["GET", "POST"])
    def flat_type_photos(flat_type_id: str):
        flat = next(
            (f for f in admin_store.get_flat_options() if f["flat_type_id"] == flat_type_id),
            None,
        )
        if flat is None:
            flash("Daire tipi bulunamadi.", "error")
            return redirect(url_for("listings"))
        if request.method == "POST":
            return _handle_photo_upload(
                "flat_type", flat_type_id, url_for("flat_type_photos", flat_type_id=flat_type_id)
            )
        return render_template(
            "flat_type_photos.html",
            flat=flat,
            photos=admin_store.list_photos("flat_type", flat_type_id),
            unit_count=len(admin_store.list_listings(filters={"flat_type_id": flat_type_id})),
        )

    @app.post("/admin/flat-types/<flat_type_id>/photos/<int:photo_id>/delete")
    def delete_flat_type_photo(flat_type_id: str, photo_id: int):
        try:
            admin_store.delete_photo(photo_id)
            flash("Fotograf silindi.", "success")
        except KeyError:
            flash("Fotograf bulunamadi.", "error")
        return redirect(url_for("flat_type_photos", flat_type_id=flat_type_id))

    @app.post("/admin/flat-types/<flat_type_id>/photos/<int:photo_id>/cover")
    def set_flat_type_cover(flat_type_id: str, photo_id: int):
        try:
            admin_store.set_cover_photo(photo_id)
            flash("Kapak fotografi guncellendi.", "success")
        except KeyError:
            flash("Fotograf bulunamadi.", "error")
        return redirect(url_for("flat_type_photos", flat_type_id=flat_type_id))

    @app.route("/admin/media/listings/<path:relpath>")
    def listing_media(relpath: str):
        # /admin prefix'i sayesinde Basic Auth kapsaminda; send_from_directory
        # path traversal'i engeller.
        return send_from_directory(admin_store.uploads_dir, relpath)

    @app.route("/admin/stock")
    def stock_board():
        return render_template(
            "stock.html",
            board=admin_store.get_stock_board(),
            velocity=admin_store.get_sales_velocity(),
            type_pressure=admin_store.get_type_pressure(),
        )

    @app.route("/admin/stock/export.csv")
    def export_stock_csv():
        import csv
        import io

        board = admin_store.get_stock_board()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "blok", "kat", "kapi", "ilan_no", "tip", "net_m2",
            "durum", "fiyat_try", "cephe", "gunes", "rezervasyon",
        ])
        for block in board["blocks"]:
            for floor in block["floors"]:
                for unit in floor["units"]:
                    reservation = unit.get("reservation") or {}
                    writer.writerow([
                        unit["block_id"], unit["floor"], unit["door_number"], unit["inventory_id"],
                        unit["flat_label"], unit.get("net_m2") or "",
                        TR_STATUS_LABELS.get(unit["status"], unit["status"]),
                        unit.get("list_price_try") or "",
                        TR_DIRECTION_LABELS.get(unit.get("direction"), unit.get("direction")),
                        TR_SUN_LABELS.get(unit.get("sun_exposure"), ""),
                        ("opsiyon" if reservation.get("kind") == "option" else "kapora") if reservation else "",
                    ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=stok.csv"},
        )

    @app.post("/admin/stock/<inventory_id>/status")
    def stock_update_status(inventory_id: str):
        status = request.form.get("status") or (request.get_json(silent=True) or {}).get("status", "")
        try:
            admin_store.update_listing_status(inventory_id, status)
        except (KeyError, ValueError) as error:
            return {"error": str(error)}, 400
        return {"ok": True, "inventory_id": inventory_id, "status": status}

    @app.post("/admin/stock/<inventory_id>/option")
    def stock_place_option(inventory_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            admin_store.place_option(
                inventory_id,
                user_id=str(payload.get("user_id", "")).strip(),
                hours=int(payload.get("hours", 48)),
                note=str(payload.get("note", "")).strip(),
            )
        except (KeyError, ValueError) as error:
            return {"error": str(error)}, 400
        return {"ok": True}

    @app.post("/admin/stock/<inventory_id>/deposit")
    def stock_place_deposit(inventory_id: str):
        payload = request.get_json(silent=True) or {}
        raw_amount = str(payload.get("amount_try", "")).strip()
        amount = int(raw_amount) if raw_amount.isdigit() else None
        try:
            admin_store.place_deposit(
                inventory_id,
                user_id=str(payload.get("user_id", "")).strip(),
                amount_try=amount,
                note=str(payload.get("note", "")).strip(),
            )
        except (KeyError, ValueError) as error:
            return {"error": str(error)}, 400
        return {"ok": True}

    @app.post("/admin/stock/<inventory_id>/release")
    def stock_release_reservation(inventory_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            admin_store.release_reservation(inventory_id, note=str(payload.get("note", "")).strip())
        except KeyError as error:
            return {"error": str(error)}, 400
        return {"ok": True}

    def _plan_params_from_form() -> dict:
        return {
            "down_payment_pct": float(request.form.get("down_payment_pct", 25) or 25),
            "months": int(request.form.get("months", 24) or 24),
            "balloon_count": int(request.form.get("balloon_count", 0) or 0),
            "balloon_amount": int(str(request.form.get("balloon_amount", 0) or 0).replace(".", "")),
            "annual_rate_pct": float(request.form.get("annual_rate_pct", 0) or 0),
        }

    @app.route("/admin/offers/new", methods=["GET", "POST"])
    def new_offer():
        inventory_id = request.values.get("inventory_id", "").strip()
        try:
            listing = admin_store.get_listing(inventory_id)
        except KeyError:
            flash("Teklif icin gecerli bir daire secin.", "error")
            return redirect(url_for("stock_board"))

        plan = None
        params = {"down_payment_pct": 25, "months": 24, "balloon_count": 0, "balloon_amount": 0, "annual_rate_pct": 0}
        user_id = request.values.get("user_id", "").strip()
        error = None

        if request.method == "POST":
            params = _plan_params_from_form()
            try:
                plan = build_payment_plan(price=int(listing["list_price_try"]), **params)
            except ValueError as exc:
                error = str(exc)

            if plan is not None and request.form.get("action") == "save":
                offer_id = admin_store.create_offer(inventory_id, user_id, plan)
                return redirect(url_for("offer_detail", offer_id=offer_id))

        return render_template(
            "offer_form.html",
            listing=listing,
            plan=plan,
            params=params,
            user_id=user_id,
            error=error,
        )

    @app.route("/admin/offers/<int:offer_id>")
    def offer_detail(offer_id: int):
        try:
            offer = admin_store.get_offer(offer_id)
            listing = admin_store.get_listing(offer["inventory_id"])
        except KeyError:
            flash("Teklif bulunamadi.", "error")
            return redirect(url_for("stock_board"))
        return render_template(
            "offer_print.html",
            offer=offer,
            listing=listing,
            sales_profile=admin_store.get_sales_profile(),
        )

    @app.route("/admin/analytics")
    def analytics():
        return render_template(
            "analytics.html",
            report=admin_store.get_analytics_report(),
        )

    @app.route("/admin/analytics/export/leads.csv")
    def export_leads_csv():
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "user_id", "isim", "asama", "sicaklik", "skor", "tercih_tip",
            "butce_max_try", "handoff_bekliyor", "oturum_sayisi", "son_temas",
        ])
        for lead in admin_store.get_leads():
            writer.writerow([
                lead["user_id"], lead.get("name") or "", lead["stage"], lead["temperature"],
                lead["score"], lead.get("preferred_flat_type") or "", lead.get("budget_max_try") or "",
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

    @app.route("/admin/listings/<inventory_id>/print")
    def listing_print(inventory_id: str):
        try:
            listing = admin_store.get_listing(inventory_id)
        except KeyError:
            flash("Ilan bulunamadi.", "error")
            return redirect(url_for("listings"))
        block = next(
            (b for b in admin_store.get_block_options() if b["block_id"] == listing["block_id"]),
            {},
        )
        photos = admin_store.get_photos_for_listing(listing)["photos"]
        return render_template(
            "listing_print.html",
            listing=listing,
            block=block,
            cover=photos[0] if photos else None,
            sales_profile=admin_store.get_sales_profile(),
        )

    @app.route("/admin/listings/<inventory_id>/edit", methods=["GET", "POST"])
    def edit_listing(inventory_id: str):
        if request.method == "POST":
            admin_store.update_listing(inventory_id, request.form.to_dict())
            flash(f"{inventory_id} güncellendi.", "success")
            return redirect(url_for("listing_detail", inventory_id=inventory_id))

        return render_template(
            "listing_edit.html",
            listing=admin_store.get_listing(inventory_id),
            flat_options=admin_store.get_flat_options(),
            block_options=admin_store.get_block_options(),
            direction_options=list(TR_DIRECTION_LABELS.keys()),
        )

    @app.route("/admin/listings/<inventory_id>/delete", methods=["POST"])
    def delete_listing(inventory_id: str):
        # Kalici veri kaybina karsi metin onayi: ilan numarasi aynen yazilmali.
        confirm = request.form.get("confirm", "").strip()
        if confirm != inventory_id:
            flash("Silme onayi eslesmedi. Ilan numarasini aynen yazin.", "error")
            return redirect(url_for("listing_detail", inventory_id=inventory_id))
        admin_store.delete_listing(inventory_id)
        flash(f"{inventory_id} silindi.", "success")
        return redirect(url_for("listings"))

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
            flash("Satis profili guncellendi.", "success")
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
                flash("Gecerli bir numara/kimlik girin.", "error")
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
        flash("Asama guncellendi.", "success")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.route("/admin/users/<path:user_id>/conversations")
    def user_conversations(user_id: str):
        filters = _conversation_filters_from_request()
        group = admin_store.get_user_conversations(user_id, filters=filters)
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
        flash("AI duraklatildi — konusmayi siz yurutuyorsunuz." if paused else "AI tekrar devrede.", "success")
        return redirect(url_for("user_conversations", user_id=user_id))

    @app.post("/admin/users/<path:user_id>/send-message")
    def send_message_to_user(user_id: str):
        message = request.form.get("message", "").strip()
        try:
            admin_store.enqueue_outbound_message(user_id, message, sender="panel")
            admin_store.log_human_message(user_id, message)
            flash("Mesaj kuyruga alindi; WhatsApp botu birkac saniye icinde gonderecek.", "success")
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
        payload = admin_store.get_conversation(session_id)
        return render_template(
            "conversation_detail.html",
            session=payload["session"],
            turns=payload["turns"],
            ai_notes=admin_store.get_user_ai_notes(payload["session"]["user_id"]),
        )

    register_whatsapp_routes(app, orchestrator_instance=active_orchestrator)
    register_call_routes(app, active_orchestrator)

    return app

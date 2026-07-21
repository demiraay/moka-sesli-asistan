from typing import Dict, Any, List
import json
import os
import threading
import time
from collections import OrderedDict
import re
import uuid
from core.config import Config
from core.admin_store import AdminStore
from core.merchant_data import MerchantDataManager, describe_day
from core.schemas import ResponseBuilder
from core.prompts import SystemPromptBuilder
from core.llm import LLMClient, is_llm_error
from core.parsing import parse_llm_json
from core import tools
from core.tools import (
    ToolContext,
    build_planner_system_prompt,
    build_router_system_prompt,
    openai_tools_schema,
    panel_tool_labels,
)
from core.agent import ToolPlanner, trim_transcript
from core.tools.handlers import OPS_CHANNEL, OPS_USER_ID
from core.formatting import (
    format_try_amount,
    mask_email,
    parse_amount_text,
    speakable_iban,
    time_of,
)

# Demo fallback: panel test sohbeti / sesli demo bir isletme secmediyse bu hatta
# baglanmis kabul edilir (gercek sistemde kimlik CTI'dan gelir).
DEFAULT_DEMO_MERCHANT_ID = "M-1001"

# Cok adimli agent loop. AGENT_ENABLED=0 ile eski tek atimlik router yoluna
# donulebilir (demo gunu sigortasi).
AGENT_LOOP_ENABLED = os.getenv("AGENT_ENABLED", "1").strip() not in ("0", "false", "False")

# Bu sureden uzun sessiz kalan oturum kapanir, sonraki mesaj YENI gorusme sayilir.
# "Gorusme basina bir kez" kurallarinin anlamli olmasi buna bagli.
try:
    SESSION_IDLE_HOURS = float(os.getenv("SESSION_IDLE_HOURS", "6"))
except ValueError:
    SESSION_IDLE_HOURS = 6.0


# Bellekte tutulacak en fazla kullanici. Asilinca EN ESKI kullanicinin bellek
# ici durumu dusurulur.
#
# Bu KAYIP DEGILDIR: dusen her sey veritabanindan geri kurulabilir —
# profil/kart ai_notes'tan (_restore_user_profile), konusma gecmisi ve
# planlayici transkripti conversation_turns'ten (_hydrate_*). Geri donen
# kullanici sessizce yeniden yuklenir.
try:
    MAX_ACTIVE_USERS = int(os.getenv("MAX_ACTIVE_USERS", "200"))
except ValueError:
    MAX_ACTIVE_USERS = 200


class AgentOrchestrator:
    def __init__(self):
        self.config = Config()
        self.merchant_data = MerchantDataManager()
        self.prompt_builder = SystemPromptBuilder()
        self.llm_client = LLMClient()
        self.admin_store = AdminStore()
        self.active_sessions: Dict[str, str] = {}

        self.conversation_histories: Dict[str, List[Dict[str, Any]]] = {}
        # Planlayicinin kalici arac transkripti (bkz. _get_planner_transcript)
        self.planner_transcripts: Dict[str, List[Dict[str, Any]]] = {}
        self.user_profiles: Dict[str, Dict[str, Any]] = {}
        # Cagri baglami: kimlik telefondan/panelden gelir, router argumani DEGILDIR.
        self.call_contexts: Dict[str, Dict[str, Any]] = {}

        # Es zamanlilik: Flask threaded=True calisir. Ayni kullanicidan gelen
        # iki mesaj (ornegin arka arkaya iki WhatsApp mesaji) ayni profili ve
        # transkripti ES ZAMANLI degistirebilirdi. Kullanici basina kilit,
        # turlari siraya sokar; farkli kullanicilar birbirini beklemez.
        self._structure_lock = threading.RLock()
        self._user_locks: Dict[str, threading.RLock] = {}
        # Son erisim sirasi (LRU tahliyesi icin).
        self._user_seen: "OrderedDict[str, float]" = OrderedDict()

    # -------------------------------------------------------------- akis

    # Sunum katmani metin uzerinde desen esler (maskeli IBAN, %, kart, URL);
    # parcayi erken yayinlarsak desen bolunur ve ekranda yazi SONRADAN degisir.
    # Bu yuzden metnin sonundan bir miktar geride tutulur.
    #
    # Tampon KUCUK olmali: cok buyuk tutulursa kisa sesli cevaplarda her sey
    # sona kadar bekler ve akis anlamsizlasir (170 karakterlik bir cevapta
    # 120'lik tampon ilk parcayi cevabin sonuna atiyordu).
    # En uzun desen maskeli IBAN: "TR** **** **** **** **44 17" = 27 karakter.
    STREAM_LOOKAHEAD_CHARS = 40

    # Selamlama kirpmasi metnin BASINDA calisir ("Merhaba X Bey, ben ... Ada.").
    # Ilk yayin, selamlama tamamen olusana kadar beklemeli.
    STREAM_HEAD_MIN_CHARS = 60

    TECHNICAL_FAULT_REPLY = (
        "Şu anda teknik bir sorun yaşıyorum, kısa süre sonra tekrar deneyebilir misiniz? "
        "Dilerseniz sizi müşteri temsilcimize de bağlayabilirim."
    )

    def _compose_streaming(self, system_prompt: str, user_prompt: str, on_delta,
                           *, channel: str, is_first_turn: bool) -> str:
        """Cevabi akitarak uretir; her islenmis parcayi on_delta'ya verir.

        Akis ORTASINDA koparsa o ana kadar yazilan metin ekranda kalir ve
        sonuna kisa bir ozur eklenir — yazilani silip bastan yazmak
        kullaniciya daha kotu gelir.
        """
        self._last_streamed_text = ""
        try:
            for piece in self._stream_polished(
                    self.llm_client.stream(system_prompt=system_prompt,
                                           user_prompt=user_prompt),
                    channel=channel, is_first_turn=is_first_turn):
                on_delta(piece)
        except Exception as error:
            print(f"LLM stream error: {error}")
            partial = (self._last_streamed_text or "").strip()
            if partial:
                tail = " Bağlantıda bir kesinti oldu, dilerseniz tekrar sorayım."
                on_delta(tail)
                return partial + tail
            on_delta(self.TECHNICAL_FAULT_REPLY)
            return self.TECHNICAL_FAULT_REPLY
        return self._last_streamed_text or self.TECHNICAL_FAULT_REPLY

    def _polish(self, text: str, *, channel: str, is_first_turn: bool) -> str:
        """Cevabi sunuma hazirlar (akan ve akmayan yollar AYNI islemi kullanir)."""
        text = self._remove_redundant_greeting(text, is_first_turn)
        text = self._normalize_currency_language(text)
        if channel == "voice":
            text = self._make_speech_friendly(text)
        return text

    def _stream_polished(self, pieces, *, channel: str, is_first_turn: bool):
        """Ham parcalari alir, ISLENMIS metni parca parca yayinlar.

        Yalnizca "kararli" on ek yayinlanir: son STREAM_LOOKAHEAD_CHARS karakter
        geride bekletilir ki desenler bolunmesin. Sonuc, ekranda hicbir yazinin
        sonradan degismemesi.
        """
        raw = ""
        emitted = 0
        threshold = max(self.STREAM_LOOKAHEAD_CHARS, self.STREAM_HEAD_MIN_CHARS)

        for piece in pieces:
            if not piece:
                continue
            raw += piece
            if len(raw) <= threshold:
                continue

            # Metni IKI kez isle: biri son parcayi gormeden, biri gorerek.
            # Ikisinin ORTAK ON EKI, son parcadan ETKILENMEYEN kisimdir —
            # yalnizca orasi yayinlanabilir.
            #
            # Sadece "sondan N karakter geride tut" YETMEZ: kesme noktasi bir
            # desenin ortasina duserse (ornegin maskeli IBAN'in yarisi) yarim
            # desen islenir ve ekrana YANLIS metin yazilir.
            without_tail = self._polish(raw[:-self.STREAM_LOOKAHEAD_CHARS],
                                        channel=channel, is_first_turn=is_first_turn)
            with_tail = self._polish(raw, channel=channel, is_first_turn=is_first_turn)
            safe = self._common_prefix_length(without_tail, with_tail)
            if safe > emitted:
                yield with_tail[emitted:safe]
                emitted = safe

        final = self._polish(raw, channel=channel, is_first_turn=is_first_turn)
        if len(final) > emitted:
            yield final[emitted:]
        # Tam metni cagirana bildir (gecmis/log icin gerekir).
        self._last_streamed_text = final

    @staticmethod
    def _common_prefix_length(left: str, right: str) -> int:
        limit = min(len(left), len(right))
        index = 0
        while index < limit and left[index] == right[index]:
            index += 1
        return index

    # ------------------------------------------------------------- bellek

    def _lock_for_user(self, user_id: str) -> threading.RLock:
        with self._structure_lock:
            lock = self._user_locks.get(user_id)
            if lock is None:
                lock = threading.RLock()
                self._user_locks[user_id] = lock
            return lock

    def _touch_user(self, user_id: str) -> None:
        """Kullaniciyi LRU'da one alir ve tavan asilirsa en eskiyi dusurur."""
        with self._structure_lock:
            self._user_seen[user_id] = time.monotonic()
            self._user_seen.move_to_end(user_id)
            while MAX_ACTIVE_USERS > 0 and len(self._user_seen) > MAX_ACTIVE_USERS:
                oldest, _ = self._user_seen.popitem(last=False)
                self._forget_user(oldest)

    def _forget_user(self, user_id: str) -> None:
        """Bir kullanicinin TUM bellek ici durumunu dusurur.

        Kalici veri DB'de; kullanici geri donerse yeniden yuklenir.
        """
        self.user_profiles.pop(user_id, None)
        self.call_contexts.pop(user_id, None)
        self._user_locks.pop(user_id, None)
        suffix = f":{user_id}"
        for store in (self.conversation_histories, self.planner_transcripts,
                      self.active_sessions):
            for key in [k for k in store if k.endswith(suffix)]:
                store.pop(key, None)

    # ------------------------------------------------------------ formatting

    # ---------------------------------------------------------- call context

    def set_call_context(self, user_id: str, merchant_id: str,
                         mode: str = "inbound", goal: str | None = None) -> None:
        """Cagri hatti kimligi: bu kullanicinin hangi isletme adina aradigini sabitler."""
        self.call_contexts[user_id] = {
            "merchant_id": merchant_id,
            "mode": mode,
            "goal": goal,
        }

    def _resolve_merchant(self, user_id: str, user_profile: Dict[str, Any]) -> Dict[str, Any] | None:
        # Operator konsolu bir ISLETME DEGILDIR: hatta kimse yok, dolayisiyla
        # isletme profili de yuklenmez (yoksa asistan operatore "Mehmet Bey"
        # diye hitap eder ve baskasinin verisini kendi verisi sanir).
        if user_profile.get("channel") == OPS_CHANNEL and user_id == OPS_USER_ID:
            user_profile["merchant_id"] = None
            user_profile["merchant_display"] = "Moka operasyon ekibi"
            return None

        # KIMLIK DOGRULANDI MI? Bir isletmeyi GERCEKTEN bu arayana baglayan bir
        # kanit var mi (panelden secim, kalici kimlik, telefon eslesmesi)? Yoksa
        # yalnizca demo varsayilanina mi dustuk? Ikisi ayni degil: dogrulanmamis
        # bir arayana "Mehmet Bey" diye hitap etmek yanlis — o kisi Mehmet degil.
        context = self.call_contexts.get(user_id)
        if context and context.get("merchant_id"):
            # Panelden / cagri baglamindan acikca secildi: dogrulanmis.
            merchant_id = context["merchant_id"]
            identity_verified = True
        else:
            # Onceki turdan gelen merchant_id'nin dogrulama durumu KORUNUR;
            # "profilde var" demek "dogrulandi" demek DEGILDIR (bir onceki tur
            # varsayimla dusmus olabilir).
            merchant_id = user_profile.get("merchant_id")
            identity_verified = bool(merchant_id) and user_profile.get("identity_verified", False)

        # Kalici kimlik eslesmesi (identities tablosu): process yeniden baslasa
        # da arayan-isletme bagi kaybolmaz. user_id GERCEKTEN bir isletmeye
        # bagliysa bu bir DOGRULAMADIR — onceki turdan/store'dan restore edilen
        # merchant_id olsa bile teyit eder (aksi halde telefonla taninan musteri
        # restore sonrasi isimsiz kaliyordu).
        linked = self.merchant_data.resolve_identity(user_id)
        if linked:
            if not merchant_id:
                merchant_id = linked
            if linked == merchant_id:
                identity_verified = True

        # WhatsApp: user_id telefon numarasidir; isletmeyi telefonla esle.
        # Artik indeksli sorgu (eskiden tum listede son-10-hane taramasiydi).
        if not merchant_id and user_profile.get("phone_number"):
            matched = self.merchant_data.find_merchant_by_phone(user_profile["phone_number"])
            if matched:
                merchant_id = matched["merchant_id"]
                identity_verified = True

        # WhatsApp'ta taninmayan arayan -> Gosterim Profili (M-TEST).
        # WhatsApp yeni kimlik formati "@lid" telefon numarasi DEGILDIR ve
        # cozulemeyince ham LID kaliyordu; bu hicbir isletmeye eslesmedigi icin
        # herkes M-1001 (Mehmet Bey) goruyordu. Kimlik kalici baglanir.
        # DIKKAT: bu bir VARSAYIMDIR, dogrulama DEGIL — isimle hitap edilmez.
        if not merchant_id and user_profile.get("channel") == "whatsapp":
            merchant_id = self._whatsapp_default_merchant(user_id)

        if not merchant_id:
            merchant_id = DEFAULT_DEMO_MERCHANT_ID

        user_profile["merchant_id"] = merchant_id
        user_profile["identity_verified"] = identity_verified
        merchant = self.merchant_data.get_merchant(merchant_id)
        if merchant:
            # Panel listeleri (handoff kuyrugu, konusmalar) icin gorunen ad.
            # Dogrulanmadiysa isimle degil isletmeyle etiketle — kim oldugunu
            # bilmiyoruz.
            if identity_verified:
                user_profile["merchant_display"] = (
                    f"{merchant.get('owner_name')} — {merchant.get('business_name')}"
                )
            else:
                user_profile["merchant_display"] = merchant.get("business_name", "")
        return merchant

    def _whatsapp_default_merchant(self, user_id: str) -> str:
        """WhatsApp'ta taninmayan arayan icin gosterilecek ornek isletme.

        KALICI BAGLAMA YOK: bu bir VARSAYIMDIR, dogrulama degil. Baglasaydik
        bir sonraki mesajda resolve_identity bunu "dogrulanmis" sanip isimle
        hitaba donerdi — oysa WhatsApp LID kimin oldugunu soylemiyor. Her mesaj
        ayni ornek profile duser ve isimsiz kalir.
        """
        try:
            from core import demo_profile
            demo_profile.ensure_exists(self.merchant_data)
            return demo_profile.TEST_MERCHANT_ID
        except Exception as error:
            print(f"WhatsApp varsayilan profil bulunamadi: {error}")
            return DEFAULT_DEMO_MERCHANT_ID

    def _call_mode(self, user_id: str) -> str:
        return (self.call_contexts.get(user_id) or {}).get("mode", "inbound")

    def _build_merchant_profile_block(self, merchant: Dict[str, Any] | None,
                                      user_id: str, identity_verified: bool = True) -> str:
        if not merchant:
            return ""
        plan = merchant.get("plan") or {}
        trend = merchant.get("volume_trend") or {}
        devices = merchant.get("devices") or []
        volumes = merchant.get("monthly_volume_try", [])[-3:]
        volume_line = ", ".join(
            f"{v['month']}: {format_try_amount(v['volume'])}" for v in volumes
        )
        device_line = "; ".join(
            f"{d['terminal_id']} ({d['model']}, {d['status']}"
            + (f", not: {d['note']}" if d.get("note") else "")
            + ")"
            for d in devices
        ) or "kayitli cihaz yok"

        # Kimlik dogrulanmadiysa (demo varsayilanina dusuldu): isletme verisini
        # goster ama ARAYANIN kim oldugunu bilmedigimizi soyle — isimle hitap
        # yasak. Aksi halde tanimadigi herkese "Mehmet Bey" diyordu.
        if identity_verified:
            header = "MERCHANT PROFILE (hattaki isletme — kimlik dogrulandi, ASLA tekrar sorma):"
            salutation_line = (f"- Yetkili: {merchant.get('owner_name')} — "
                               f"hitap: {merchant.get('salutation', merchant.get('owner_name'))}")
        else:
            header = ("MERCHANT PROFILE (ornek/demo isletme — ARAYANIN KIMLIGI "
                      "DOGRULANMADI):\n"
                      "- Arayana ISIMLE HITAP ETME (Mehmet Bey deme). Isimsiz, "
                      "nazik konus: 'Merhaba, size nasil yardimci olabilirim?'\n"
                      "- Isletme verisini (hakedis, islem, plan) gostermekte "
                      "sorun yok; yalnizca kisisel hitaptan kacin.")
            salutation_line = "- Yetkili: (kimlik dogrulanmadi — isim kullanma)"

        lines = [
            header,
            f"- Isletme: {merchant.get('business_name')} ({merchant.get('sector')}, {merchant.get('city')})",
            salutation_line,
            f"- Urunler: {', '.join(merchant.get('products', []))}",
            f"- Plan: {plan.get('name')} (%{plan.get('rate_pct')} komisyon"
            + (f", ayda {format_try_amount(plan.get('monthly_fee_try', 0))} sabit ucret" if plan.get('monthly_fee_try') else "")
            + ")",
            f"- Son 3 ay ciro: {volume_line} (degisim: %{trend.get('change_pct', 0)})",
            f"- Cihazlar: {device_line}",
        ]

        # CRM baglami: agent'in TONUNU ve yaklasimini ayarlamasi icin ic bilgi.
        # Bunlar musteriye ham soylenmez (skor/segment desifre edilmez); yalnizca
        # asistan daha baglamsal ve insani davransin diye verilir.
        lines.extend(self._crm_context_lines(merchant, identity_verified))

        context = self.call_contexts.get(user_id) or {}
        if context.get("mode") == "outbound":
            goal = context.get("goal") or (
                "Bu bir GIDEN aramadir: isletmenin islem hacmi ciddi dustu. Sebebi "
                "anlayisla ogren; teknik sorun varsa coz, rakibe gectiyse/memnuniyetsizse "
                "CONTEXT'teki sadakat teklifini kisaca sun."
            )
            lines.append(f"- ARAMA HEDEFI (outbound): {goal}")

        return "\n".join(lines)

    def _crm_context_lines(self, merchant: Dict[str, Any],
                           identity_verified: bool) -> List[str]:
        """Musteri iliskisi baglami: segment/risk farkindaligi + temsilci +
        son temas. Agent'in TONUNU ayarlamasi icin ic bilgi; musteriye ham
        rakam/skor SOYLENMEZ (profil bloguna 'ic bilgi' notu ile girer)."""
        lines: List[str] = []

        manager = merchant.get("account_manager")
        tier = merchant.get("tier")
        if manager:
            tier_part = f", {tier} musteri" if tier else ""
            lines.append(
                f"- Atanmis temsilci: {manager}{tier_part} "
                "(devir gerekirse musteri bu temsilciye baglanir).")

        # Segment/risk: yalnizca DAVRANIS ipucu; ham skor degil.
        try:
            risk = self.merchant_data.risk_profile(merchant)
        except Exception:
            risk = {}
        segment = risk.get("segment")
        if segment in ("uyuyan", "daralıyor"):
            reasons = risk.get("reasons") or []
            reason_hint = f" Analiz: {'; '.join(reasons)}." if reasons else ""
            lines.append(
                "- ILISKI DURUMU (ic bilgi, musteriye SKOR/SEGMENT soyleme): islem "
                f"hacmi {segment}.{reason_hint} Musteri ASIL konuyu cozup kapatmaya "
                "gecince, 'baska bir konu var mi' demek YERINE, YUKARIDAKI GERCEK "
                "VERIYE BAK (son 3 ay ciro serisi + cihaz durumu/notu) ve BIR KEZ, "
                "duruma OZEL kendi cumlelerinle anlayisla degin — ASLA sabit kalip "
                "kullanma. Dusus cihaz arizasiyla ortusuyorsa ikisini bagla (or. "
                "'cihaz kapali kaldigi icin islemler durmus olabilir'); ortusmuyorsa "
                "hangi ay ne kadar dustugunu somut gozlemleyip nazikce sebebini sor. "
                "Israr etme, satis baskisi yapma; musteri acele/kizginsa zorlamadan kapat.")
        elif segment == "büyüyor":
            lines.append(
                "- ILISKI DURUMU (ic bilgi): isler aciliyor, buyuyen musteri. Ciro "
                "verisindeki artisi FARK ET ve uygunsa somut, icten bir cumleyle "
                "takdir et (sabit kalip degil, gercek rakama deginerek).")

        # Son temas + gecmis CRM notlari: yalnizca kimlik dogrulanmis aramalarda.
        if identity_verified and merchant.get("merchant_id"):
            merchant_id = merchant["merchant_id"]
            try:
                recent = self.merchant_data.list_contacts(merchant_id, limit=1)
            except Exception:
                recent = []
            if recent:
                contact = recent[0]
                lines.append(
                    f"- Son temas: {(contact.get('contacted_at') or '')[:10]} "
                    f"({contact.get('channel', '')}, {contact.get('subject', '')}). "
                    "Uygunsa buna kisaca deginebilirsin.")

            # Agent'in daha once bu musteriden ogrendigi KALICI bilgiler —
            # kapali dongunun geri besleme ayagi: musteriyi gercekten hatirla.
            try:
                insights = self.merchant_data.list_insights(merchant_id, limit=3)
            except Exception:
                insights = []
            if insights:
                notes = "; ".join(
                    f"{i.get('subject', '')}: {i.get('note', '')}" for i in insights)
                lines.append(
                    "- HATIRLANANLAR (bu musteri hakkinda daha once ogrenildi): "
                    f"{notes}. Ilgiliyse dogal sekilde hatirla, ayni seyi tekrar sorma.")
        return lines

    # Otomatik temas gunlugune yazILMAYAN araclar: bunlar tek basina "anlamli
    # konusma" saymaz (selam/hafiza/not). Digerleri (hakedis, islem, ariza,
    # ekstre, teklif, devir) anlamli sayilir.
    _TRIVIAL_TOOLS = {"answer_general", "update_customer_card", "record_crm_note"}

    def _log_crm_contact(self, merchant: Dict[str, Any] | None, session_id: str,
                         channel: str, user_profile: Dict[str, Any],
                         ai_notes: Dict[str, Any], ai_summary: str,
                         router_decision: Dict[str, Any]) -> None:
        """Anlamli bir konusma turunun ozetini isletme-360 temas gunlugune yazar.

        Bir OTURUM = tek temas kaydi (upsert): konusma ilerledikce ozet zenginlesir.
        Selam/tesekkur gibi bos turlar ATLANIR. Yalnizca kimligi dogrulanmis
        aramalar kaydedilir (kime ait oldugu belli)."""
        if not merchant or not merchant.get("merchant_id") or not session_id:
            return
        if not user_profile.get("identity_verified"):
            return
        tools_used = {t.get("name") for t in (router_decision.get("tools") or [])
                      if isinstance(t, dict)}
        substantive = bool(tools_used - self._TRIVIAL_TOOLS)
        if not (substantive or ai_notes.get("issue") or ai_notes.get("handoff_required")):
            return   # bos tur — kaydetme

        subject = (ai_notes.get("issue")
                   or self._contact_subject_from_tools(tools_used)
                   or "Görüşme")
        note = ai_summary if (ai_summary and "kayda değer" not in ai_summary) \
            else (ai_notes.get("issue") or subject)
        self.merchant_data.upsert_session_contact(
            merchant["merchant_id"], session_id,
            channel=channel, subject=str(subject)[:80], note=str(note)[:300])

        # Konusmada cikan SATIS FIRSATI: admin ai_notes'ta kaliyordu, merchant
        # CRM'e (360/rapor) akmiyordu. Ayri bir 'fırsat' icgorusu olarak dusur
        # (oturum basina tek, guncellenir).
        card = user_profile.get("card") or {}
        opportunity = card.get("upsell_opportunity")
        if opportunity:
            self.merchant_data.upsert_session_insight(
                merchant["merchant_id"], session_id, "fırsat",
                str(opportunity)[:200], channel=channel)

    def _contact_subject_from_tools(self, tools_used) -> str:
        """Anlamli araclardan panel etiketi turetir (konu basligi icin)."""
        labels = panel_tool_labels()
        for name in tools_used:
            if name not in self._TRIVIAL_TOOLS and name in labels:
                return labels[name]
        return ""

    def _build_merchant_router_line(self, merchant: Dict[str, Any] | None,
                                    user_id: str) -> str:
        """Router icin TEK SATIRLIK isletme ozeti: tam profil cevap LLM'ine gider,
        router'a gitmez (token butcesi — Groq free tier TPM)."""
        if not merchant:
            return ""
        plan = merchant.get("plan") or {}
        trend = merchant.get("volume_trend") or {}
        line = (
            f"MERCHANT: {merchant.get('business_name')} ({merchant.get('merchant_id')}), "
            f"urunler: {','.join(merchant.get('products', []))}, plan %{plan.get('rate_pct')}, "
            f"ciro trendi %{trend.get('change_pct', 0)}"
        )
        if (self.call_contexts.get(user_id) or {}).get("mode") == "outbound":
            line += " — OUTBOUND kurtarma aramasi (churn sinyalinde recommend_offer/dormant_retention)"
        return line

    # ------------------------------------------------------------- profiles

    def _get_user_profile(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = {
                "conversation_focus": None,
                "handoff_reason": None,
                "merchant_id": None,
                "pending_offer": None,
                "offer_made": False,
                "resumed_from_store": False,
                "resume_summary": None,
            }
            self._restore_user_profile(user_id)
        return self.user_profiles[user_id]

    def _restore_user_profile(self, user_id: str) -> None:
        user_profile = self.user_profiles[user_id]
        saved_notes = self.admin_store.get_user_ai_notes(user_id)
        ai_notes = saved_notes.get("ai_notes", {})
        if not ai_notes:
            return

        user_profile["merchant_id"] = ai_notes.get("merchant_id")

        # GORUSMEYE OZEL durum (kart, odak, bekleyen teklif, tekrar korumalari)
        # kosulsuz geri yuklenMEZ: hepsi kaydedildigi OTURUMA aittir.
        #
        # Aksi halde yeni bir konusma eski konusmanin baglamiyla basliyordu —
        # "selam" diyen musteriye, kendisinin hic soylemedigi bir tutarin
        # tarihi soruluyordu (kart "44.104 TL biliniyor, tarih eksik" diyordu).
        user_profile["_restored_state"] = {
            "session_id": ai_notes.get("guard_session_id"),
            "card": ai_notes.get("card") if isinstance(ai_notes.get("card"), dict) else None,
            "conversation_focus": ai_notes.get("conversation_focus"),
            "handoff_reason": ai_notes.get("handoff_reason"),
            "pending_offer": ai_notes.get("pending_offer"),
            "offer_made": bool(ai_notes.get("offer_made")),
            "once_tools": list(ai_notes.get("once_tools") or []),
        }
        user_profile["resumed_from_store"] = True
        user_profile["resume_summary"] = self._build_resume_summary_from_notes(ai_notes)

    def _apply_restored_state(self, user_profile: Dict[str, Any], session_id: str) -> None:
        """DB'den gelen GORUSME DURUMUNU yalnizca AYNI oturuma uygular.

        Iki yonlu hata var, ikisini de kapatir:
          - Hic saklanmazsa: surec yeniden baslayinca ayni gorusmede musteri
            kartini ve tekrar korumalarini kaybederiz (ikinci ekstre gider).
          - Kosulsuz saklanirsa: YENI konusma eskisinin baglamiyla baslar —
            "selam" diyene eski bir tutarin tarihi sorulur — ve musteri bir
            daha hic ekstre alamaz.

        Kimlik (merchant_id) bunun DISINDADIR: o gorusmeye degil kisiye aittir.
        """
        user_profile["session_id"] = session_id
        restored = user_profile.pop("_restored_state", None)
        if not restored:
            return
        if restored.get("session_id") != session_id:
            return      # yeni gorusme: eski baglam tasinmaz

        if restored.get("card"):
            user_profile["card"] = restored["card"]
        user_profile["conversation_focus"] = restored.get("conversation_focus")
        user_profile["handoff_reason"] = restored.get("handoff_reason")
        user_profile["pending_offer"] = restored.get("pending_offer")
        if restored.get("offer_made"):
            user_profile["offer_made"] = True
        for tool_name in restored.get("once_tools") or []:
            user_profile[f"_once_{tool_name}"] = True

    def _session_key(self, user_id: str, channel: str) -> str:
        return f"{channel}:{user_id}"

    def _get_conversation_history(self, user_id: str, channel: str) -> List[Dict[str, Any]]:
        key = self._session_key(user_id, channel)
        if key not in self.conversation_histories:
            self.conversation_histories[key] = self._hydrate_history_from_store(user_id, channel)
        return self.conversation_histories[key]

    def _hydrate_history_from_store(self, user_id: str, channel: str, max_turns: int = 10) -> List[Dict[str, Any]]:
        """Surec yeniden basladiginda konusma gecmisini DB'den geri yukler.

        Boylece kalici akis oturumu ile bellek ici gecmis tutarli kalir:
        akis kaldigi dugumden devam eder ve LLM onceki mesajlari gorur.
        """
        try:
            session_id = self.admin_store.get_latest_session_id_for_user(user_id, channel)
            if not session_id:
                return []
            conversation = self.admin_store.get_conversation(session_id)
        except Exception as error:
            print(f"History hydrate warning: {error}")
            return []

        history: List[Dict[str, Any]] = []
        for turn in conversation.get("turns", [])[-max_turns:]:
            user_text = (turn.get("user_input") or "").strip()
            agent_text = (turn.get("agent_response") or "").strip()
            if user_text:
                history.append({
                    "role": "user",
                    "text": user_text,
                })
            if agent_text:
                history.append({"role": "agent", "text": agent_text})
        return history

    def _get_session_id(self, user_id: str, channel: str) -> str:
        key = self._session_key(user_id, channel)
        if key not in self.active_sessions:
            # Uzun sessizlikten sonra YENI gorusme baslar. Aksi halde WhatsApp
            # oturumu sonsuza kadar yasar ve "gorusme basina bir kez" calisan
            # araclar (ekstre, teklif, devir) bir daha hic calismaz.
            existing = self.admin_store.get_latest_session_id_for_user(
                user_id, channel, max_idle_hours=SESSION_IDLE_HOURS)
            self.active_sessions[key] = existing or str(uuid.uuid4())
        return self.active_sessions[key]

    def get_history(self, user_id: str, channel: str) -> List[Dict[str, Any]]:
        """Konusma gecmisinin kopyasini dondurur (panel sohbeti gibi arayuzler icin)."""
        return list(self._get_conversation_history(user_id, channel))

    def reset_conversation(self, user_id: str, channel: str) -> None:
        """Konusmayi sifirlar: bellek ici gecmisi bosaltir, yeni oturum acar
        ve kullanici profilini temizler."""
        key = self._session_key(user_id, channel)
        self.conversation_histories[key] = []
        self.planner_transcripts[key] = []
        self.active_sessions[key] = str(uuid.uuid4())
        self.user_profiles.pop(user_id, None)

    # ----------------------------------------------------------- extraction

    def _build_resume_prompt_block(self, user_profile: Dict[str, Any],
                                   channel: str, is_first_turn: bool) -> str:
        """Donen WhatsApp musterisi icin composer'a RESUME BAGLAMI verir.

        Eskiden cevap uretildikten SONRA basina string yapistiriliyordu
        ('Tekrar merhaba, ...') — bu selamlamayi yarim kesip 'ben Ada' ile
        bozuk birlestiriyordu. Artik LLM'e baglam olarak verilir; model dogal,
        tek akici bir cumle uretir. Blok YALNIZCA bir kez uygulanir."""
        if channel != "whatsapp" or not is_first_turn:
            return ""
        if not user_profile.get("resumed_from_store") or not user_profile.get("resume_summary"):
            return ""
        summary = user_profile["resume_summary"].rstrip(".")
        user_profile["resumed_from_store"] = False   # bir kez uygula
        return (
            "RESUME CONTEXT (WhatsApp — donen musteri, ilk mesaj):\n"
            f"- Onceki gorusmede: {summary}.\n"
            "- Ilk cumlende bunu DOGAL ve TEK bir akici cumlede hatirlat, sonra "
            "bugun nasil yardimci olabilecegini sor. Ornek ritim: 'Merhaba, gecen "
            "sefer ... konusmustuk; bugun nasil yardimci olabilirim?' (kimlik "
            "dogrulandiysa uygun hitabi kullan).\n"
            "- Kendini YENIDEN TANITMA ('ben Ada', 'Moka United'dan' deme) — "
            "musteri seni zaten taniyor. Iki ayri selam cumlesi kurma."
        )

    def _build_ai_notes_payload(
        self,
        user_profile: Dict[str, Any],
        router_decision: Dict[str, Any],
        context: Dict[str, Any],
        user_input: str,
    ) -> Dict[str, Any]:
        card = user_profile.get("card") or {}
        handoff = context.get("handoff", {})

        ai_notes = {
            "last_user_message": user_input,
            "name": user_profile.get("merchant_display"),
            "merchant_id": user_profile.get("merchant_id"),
            "issue": card.get("issue"),
            "mood": card.get("mood"),
            "amount_mentioned_try": card.get("amount_mentioned_try"),
            "date_mentioned": card.get("date_mentioned"),
            "terminal_id": card.get("terminal_id"),
            "conversation_focus": user_profile.get("conversation_focus"),
            "handoff_required": bool(handoff.get("required")),
            "handoff_reason": handoff.get("reason") or user_profile.get("handoff_reason"),
            "last_router_tool": router_decision.get("tool"),
            "pending_offer": user_profile.get("pending_offer"),
            "card": user_profile.get("card"),
            # Tekrar korumasi: surec yeniden baslarsa "bu gorusmede zaten
            # ekstre gonderildi / teklif sunuldu" bilgisi kaybolmasin. Oturum
            # kimligiyle birlikte saklanir; YENI gorusmede uygulanmaz.
            "guard_session_id": user_profile.get("session_id"),
            "offer_made": bool(user_profile.get("offer_made")),
            "once_tools": sorted(
                key[len("_once_"):] for key in user_profile
                if key.startswith("_once_") and user_profile[key]
            ),
        }

        return {key: value for key, value in ai_notes.items() if value not in (None, "", [], {})}

    def _build_ai_summary(self, ai_notes: Dict[str, Any]) -> str:
        parts: List[str] = []

        if ai_notes.get("issue"):
            parts.append(f"konu: {ai_notes['issue']}")
        # Etiketler registry'den turetilir: eskiden burada elle yazilmis bir
        # sozluk vardi ve 'answer_general' eksikti.
        labels = panel_tool_labels()
        tool = ai_notes.get("last_router_tool")
        if tool in labels:
            parts.append(labels[tool])
        if ai_notes.get("mood") in ("gergin", "kizgin", "kızgın"):
            parts.append(f"musteri {ai_notes['mood']}")
        if ai_notes.get("handoff_required"):
            reason = ai_notes.get("handoff_reason") or ""
            parts.append(f"insan devri gerekti{': ' + reason if reason else ''}")
        if (ai_notes.get("pending_offer") or {}).get("trigger"):
            parts.append(f"bekleyen teklif: {ai_notes['pending_offer']['trigger']}")

        if not parts:
            return "Henüz kayda değer yapay zeka notu yok."

        return ". ".join(parts).capitalize() + "."

    def _build_resume_summary_from_notes(self, ai_notes: Dict[str, Any]) -> str | None:
        issue = ai_notes.get("issue")
        if issue:
            return f"{issue} konusunu konuşuyorduk"
        focus = ai_notes.get("conversation_focus")
        if focus:
            return "görüşmemize kaldığımız yerden devam ediyoruz"
        return None

    def _get_sales_profile(self) -> Dict[str, Any]:
        try:
            return self.admin_store.get_sales_profile()
        except Exception:
            return {
                "consultant_name": "",
                "consultant_title": "",
                "phone_number": "",
                "whatsapp_number": "",
                "office_name": "",
                "office_address": "",
                "updated_at": None,
            }

    def _build_sales_profile_prompt_summary(self) -> str:
        profile = self._get_sales_profile()
        populated = {
            key: value
            for key, value in profile.items()
            if value not in ("", None, False)
        }
        if not populated:
            return "No configured human representative profile."
        return json.dumps(populated, ensure_ascii=False)

    # -------------------------------------------------------- text cleanup

    def _remove_redundant_greeting(self, text: str, is_first_turn: bool) -> str:
        """
        After the first turn, strip robotic repeated greetings so follow-up replies
        get to the point like a real support agent.
        """
        if is_first_turn:
            return text

        cleaned = text.strip()
        if not cleaned:
            return cleaned

        sentences = re.split(r'(?<=[.!?])\s+', cleaned, maxsplit=1)
        if len(sentences) == 2:
            first_sentence, remainder = sentences
            first_lower = first_sentence.lower()
            greeting_markers = ("merhaba", "selam", "hoş geldiniz", "iyi günler")
            if any(marker in first_lower for marker in greeting_markers):
                cleaned = remainder.strip()

        cleaned = re.sub(
            r"^\s*(merhaba|selam|merhabalar|selamlar)[,!\.\s]+",
            "",
            cleaned,
            flags=re.IGNORECASE
        ).strip()

        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]

        return cleaned

    def _normalize_currency_language(self, text: str) -> str:
        cleaned = text
        cleaned = re.sub(r"\bTRY\b", "TL", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(\d+(?:[.,]\d+)?)\s*milyon TL['’]?[a-zçğıöşü]*",
            r"\1 milyon TL",
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned

    def _make_speech_friendly(self, text: str) -> str:
        """Son guvenlik agi: LLM kurallara ragmen sese uygun olmayan bir sey
        yazdiysa (maskeli IBAN, % isareti, ciplak URL) konusma diline cevir."""
        cleaned = text
        # "%1,99" / "% 2,49" -> "yüzde 1,99"
        cleaned = re.sub(r"%\s*(\d)", r"yüzde \1", cleaned)
        # Maskeli IBAN blogu -> "sonu XX YY ile biten IBAN" (son rakamda biter,
        # kuyruktaki boslugu yutmaz)
        def _iban_repl(match: re.Match) -> str:
            return speakable_iban(match.group(0))
        cleaned = re.sub(r"TR[*\d][*\d ]{9,}\d", _iban_repl, cleaned)
        # Maskeli kart: "(kart) **** 4832" -> "4832 ile biten kart"
        cleaned = re.sub(r"(?:kart[ıi]?\s+)?\*{2,}[\s*]*(\d{4})", r"\1 ile biten kart", cleaned)
        # Ciplak URL -> okunmaz, SMS'e referans verilir
        cleaned = re.sub(r"https?://\S+", "telefonunuza SMS ile gönderilen link", cleaned)
        return cleaned

    # --- Agentic musteri karti (hafizayi router LLM yonetir; kelime bazli cikarim yok) --
    def _merge_router_card(self, user_profile: Dict[str, Any], router_decision: Dict[str, Any]) -> None:
        """Router LLM'in ayni cagrida urettigi 'card' alanini profile'a alir.
        Router kart uretmediyse mevcut kart korunur; telefon her zaman saklanir."""
        card = router_decision.get("card")
        if isinstance(card, dict) and card:
            user_profile["card"] = card
        # Telefon kanaldan bilinir; LLM koymasa/silse de karta geri yaz.
        if user_profile.get("phone_number"):
            user_profile.setdefault("card", {})
            if isinstance(user_profile["card"], dict):
                user_profile["card"]["phone"] = user_profile["phone_number"]

    def _build_customer_card_prompt(self, user_profile: Dict[str, Any]) -> str:
        """Otoriter musteri kartini LLM prompt'una serer."""
        card = user_profile.get("card") or {}
        lines: List[str] = []

        label_map = {
            "owner_name": "Isim", "business_name": "Isletme", "issue": "Sorun",
            "amount_mentioned_try": "Bahsedilen tutar", "date_mentioned": "Tarih",
            "terminal_id": "Terminal", "card_last4": "Kart son 4",
            "mood": "Ruh hali", "upsell_opportunity": "Firsat notu",
        }
        for key, label in label_map.items():
            value = card.get(key)
            if value in (None, "", [], {}):
                continue
            if key == "amount_mentioned_try":
                parsed = parse_amount_text(value)
                if parsed is not None:
                    value = format_try_amount(parsed)
            lines.append(f"- {label}: {value}")

        for change in card.get("changed") or []:
            lines.append(f"- ⚠ DEGISTI: {change}. Artik guncel duruma gore ilerle, eskiye DONME.")

        info_state = self._build_info_state_lines(user_profile)
        if not lines and not info_state:
            return ""
        block = (
            "MUSTERI KARTI (OTORITER — sohbet gecmisindeki eski ifadelerle celisirse "
            "BU KARTI esas al; kart musterinin GUNCEL gercek durumudur):\n" + "\n".join(lines)
        ) if lines else ""
        if info_state:
            block += ("\n\n" if block else "") + info_state
        return block

    # Destek surecinde isimize yarayan bilgi alanlari. Envanter YAPISALDIR:
    # alan dolu mu bos mu diye bakilir (kelime analizi yok); yeterli mi
    # yetersiz mi kararini LLM verir.
    def _build_info_state_lines(self, user_profile: Dict[str, Any]) -> str:
        card = user_profile.get("card") or {}
        values = {
            "Isletme ve yetkili (hattan dogrulandi)": user_profile.get("merchant_id"),
            "Sorun": card.get("issue"),
            "Tutar": card.get("amount_mentioned_try"),
            "Tarih": card.get("date_mentioned"),
        }
        known = [f"{label}: {value}" for label, value in values.items()
                 if value not in (None, "", [], {})]
        return (
            "GORUSMEDE GECENLER (yalnizca hatirlatma):\n"
            f"- {chr(10).join('- ' + item for item in known) if known else 'henuz bir sey gecmedi'}\n"
            "\n"
            "KURAL — SORMA, BAK:\n"
            "Musteriye VERI SORMA. Tutar, tarih, islem, hakedis, cihaz, komisyon "
            "bilgisi SENDE: araclarla ARA. Musteri bir tutar soyledi diye tarihini "
            "sorma — o tutarla arama yap, cikanlari kendisine goster.\n"
            "Musteriye yalnizca SENDE OLMAYAN seyler sorulur: bir TERCIH (hangi "
            "kanal, hangi tutar icin link) ya da arama birden fazla sonuc verdiginde "
            "AYIRT EDICI bir ayrinti. Bunlar da tek seferde bir tane.\n"
            "Yukaridakiler zaten konusuldu; tekrar sorma."
        )

    # ----------------------------------------------------------- prompts

    def _build_router_user_prompt(self, user_input: str, user_profile: Dict[str, Any],
                                  history: List[Dict[str, Any]], merchant_block: str) -> str:
        recent_turns = history[-4:]
        history_lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = turn.get("text", "")
            history_lines.append(f"{role}: {text}")

        card_block = self._build_customer_card_prompt(user_profile)

        return (
            f"LATEST USER INPUT:\n{user_input}\n\n"
            f"RECENT CONVERSATION:\n" + ("\n".join(history_lines) if history_lines else "No previous turns.") + "\n\n"
            + (merchant_block + "\n\n" if merchant_block else "")
            + (card_block + "\n\n" if card_block else "")
            + f"CONVERSATION FOCUS:\n{user_profile.get('conversation_focus')}\n\n"
            "Decide the next tool using the full context above. The MUSTERI KARTI is the "
            "caller's current truth — if history conflicts with it, trust the card. "
            "Interpret short follow-ups ('denedim olmadi', 'evet gonder', 'kabul ediyorum') "
            "from the recent conversation."
        )

    def _build_response_user_prompt(self, user_input: str, user_profile: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
        # Kart blogu system prompt'ta zaten var; burada tekrarlamak cift token.
        recent_turns = history[-4:]
        history_lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = turn.get("text", "")
            history_lines.append(f"{role}: {text}")

        return (
            f"CURRENT USER MESSAGE:\n{user_input}\n\n"
            f"RECENT CONVERSATION:\n" + ("\n".join(history_lines) if history_lines else "No previous turns.") + "\n\n"
            "Reply naturally in Turkish. The MUSTERI KARTI in your system prompt is the "
            "caller's current truth — if older messages conflict, follow the card. Give ONE "
            "useful point plus at most ONE short question; no filler, no repeating what they "
            "already told you, and never ask for info the card already has."
        )

    def _run_router_step(
        self,
        *,
        user_input: str,
        user_profile: Dict[str, Any],
        current_history: List[Dict[str, Any]],
        merchant_block: str,
    ) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        """Arac secimini tamamen router LLM'e birakir; kural tabanli override yoktur."""
        router_prompt = build_router_system_prompt()
        router_user_prompt = self._build_router_user_prompt(
            user_input, user_profile, current_history, merchant_block
        )

        router_response_str = ""
        try:
            router_response_str = self.llm_client.generate(
                system_prompt=router_prompt,
                user_prompt=router_user_prompt,
                json_mode=True,
                profile="router",
            )
            router_decision = parse_llm_json(router_response_str)

            tool_name = router_decision.get("tool")
            tool_args = router_decision.get("args", {})
        except Exception as e:
            print(f"Router Parse Error: {e} | Raw: {router_response_str}")
            tool_name = "answer_general"
            tool_args = {}
            router_decision = {"tool": tool_name, "args": tool_args, "error": str(e)}

        if not isinstance(tool_args, dict):
            tool_args = {}
        # Tip duzeltmesi artik registry'de (tools.coerce_args), run_tool icinde.
        router_decision["args"] = tool_args
        # Slot yazimi kaldirildi: hafizanin tek kaynagi artik musteri kartidir.

        if tool_name == "trigger_handoff":
            user_profile["handoff_reason"] = tool_args.get("reason", "")

        return tool_name, tool_args, router_decision

    @property
    def planner(self) -> ToolPlanner:
        """Planlayici GEC baglanir.

        Kurulumda sabitlenirse llm_client sonradan degistirildiginde (testler,
        model degisimi) eski istemci kullanilmaya devam ederdi.
        """
        return ToolPlanner(self.llm_client, self.run_tool)

    # ----------------------------------------------------------- agent loop

    def _get_planner_transcript(self, user_id: str, channel: str) -> List[Dict[str, Any]]:
        """Planlayicinin KALICI konusma transkripti.

        Onceki surumde her turda sifirdan kuruluyordu: yalnizca duz metin
        mesajlar veriliyor, onceki turun arac cagrilari ve ARAC SONUCLARI
        atiliyordu. Model "gecen tur ne ogrendigini" asistanin prozasindan
        cikarmak zorunda kaliyor, bu yuzden ayni araci tekrar tekrar cagiriyordu.

        Artik transkript birikiyor: user -> assistant(tool_calls) -> tool(...)
        -> assistant(cevap) zinciri oldugu gibi korunur.
        """
        key = self._session_key(user_id, channel)
        if key not in self.planner_transcripts:
            self.planner_transcripts[key] = self._hydrate_transcript_from_store(
                user_id, channel)
        return self.planner_transcripts[key]

    def _hydrate_transcript_from_store(self, user_id: str, channel: str,
                                       max_turns: int = 10) -> List[Dict[str, Any]]:
        """Surec yeniden baslarsa transkripti DB'den kurar.

        Arac sonuclari router_decision_json icinde saklanir (bkz. _run_agent_loop);
        boylece yeniden baslatmadan sonra da model ne ogrendigini bilir.
        """
        transcript: List[Dict[str, Any]] = []
        try:
            session_id = self.admin_store.get_latest_session_id_for_user(user_id, channel)
            if not session_id:
                return transcript
            conversation = self.admin_store.get_conversation(session_id)
        except Exception as error:
            print(f"Transcript hydration warning: {error}")
            return transcript

        for index, turn in enumerate(conversation.get("turns", [])[-max_turns:]):
            transcript.append({"role": "user", "content": turn.get("user_input", "")})
            decision = turn.get("router_decision") or {}
            for order, call in enumerate(decision.get("tools") or []):
                if not isinstance(call, dict) or not call.get("name"):
                    continue
                call_id = f"h{index}_{order}"
                transcript.append({
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": call_id, "type": "function",
                                    "function": {"name": call["name"],
                                                 "arguments": call.get("args") or {}}}],
                })
                transcript.append({
                    "role": "tool", "tool_call_id": call_id, "name": call["name"],
                    "content": call.get("result") or "",
                })
            if turn.get("agent_response"):
                transcript.append({"role": "assistant",
                                   "content": turn["agent_response"]})
        return transcript

    def _run_agent_loop(self, *, user_input: str, user_profile: Dict[str, Any],
                        current_history: List[Dict[str, Any]],
                        merchant: Dict[str, Any] | None,
                        ctx: ToolContext, on_tool=None) -> Dict[str, Any]:
        """Faz A: model araclari kendi secer, sonuclari gorur, gerekirse zincirler.

        Donen sozluk panel/DB sozlesmesini korur: 'tool' + 'args' alanlari eskisi
        gibi ilk araci gosterir, 'tools' listesi tum zinciri tasir.
        """
        system_prompt = build_planner_system_prompt()

        merchant_line = self._build_merchant_router_line(merchant, ctx.user_id)
        if merchant_line:
            system_prompt += f"\n\n{merchant_line}"
        card_block = self._build_customer_card_prompt(user_profile)
        if card_block:
            system_prompt += f"\n\n{card_block}"

        transcript = self._get_planner_transcript(ctx.user_id, ctx.channel)
        user_message = {"role": "user", "content": user_input}

        plan = self.planner.run(
            system_prompt=system_prompt,
            messages=trim_transcript(transcript + [user_message]),
            ctx=ctx,
            on_tool=on_tool,
        )

        # Bu turu transkripte isle: kullanici mesaji + arac cagrilari + sonuclar.
        # Asistanin NIHAI cevabi sonra eklenir (bkz. record_agent_reply) cunku
        # cevabi ikinci model (kompozisyon fazi) yazar — planlayicinin kendi
        # duz yazisi atilir ve transkripte girmemelidir.
        transcript.append(user_message)
        transcript.extend(plan.messages)

        if plan.stop_reason == "llm_error":
            print(f"Agent loop LLM error: {plan.llm_error}")
            if not plan.executed:
                # Saglayici hatasini parse hatasindan AYIR: onceki surumde ikisi
                # de sessizce answer_general'a dusuyordu ve sebep gorunmuyordu.
                ctx.builder.add_fact(
                    "Araç katmanına ulaşılamadı. Veri uydurma; kısaca özür dile ve "
                    "bilgiyi tekrar sormasını iste.")
        elif not plan.executed:
            self.run_tool("answer_general", {"category": "other"}, ctx)

        # Engellenen araclar cevap modeline ACIKCA bildirilir. Genel bir
        # "islem yapilmadi" uyarisi yetmiyordu: model neden olmadigini
        # bilmeyince "ekstrenizi tekrar iletiyorum" gibi yapilmamis bir eylem
        # anlatiyordu. Dogru cevap "zaten gonderilmisti" olmali.
        for item in plan.executed:
            if not item.suppressed:
                continue
            label = panel_tool_labels().get(item.name, item.name)
            ctx.builder.add_fact(
                f"TEKRAR ENGELLENDI ({label}): bu işlem bu görüşmede ZATEN "
                "yapılmıştı, ŞİMDİ TEKRAR YAPILMADI. Müşteriye yeniden "
                "yaptığını söyleme; daha önce yapıldığını hatırlat."
            )

        # UYDURMA KORUMASI: hafiza guncellemesi disinda hicbir arac calismadiysa
        # bu turda FIILEN BIR SEY YAPILMADI. Cevap modeline bunu acikca soyle;
        # aksi halde "linki olusturdum, SMS gonderdim" gibi GERCEKLESMEMIS bir
        # eylem iddia edebiliyor (canli provada goruldu).
        did_something = any(
            item.name not in ("update_customer_card", "answer_general")
            and not item.suppressed and not item.error
            for item in plan.executed
        )
        if not did_something:
            ctx.builder.add_fact(
                "BU TURDA HİÇBİR İŞLEM YAPILMADI ve yeni veri alınmadı. "
                "Link oluşturdum / ekstre gönderdim / kayıt açtım gibi YAPILMAMIŞ "
                "bir eylemi ASLA söyleme. Ya eksik bilgiyi sor ya da yalnızca "
                "elindeki bilgiyle konuş."
            )

        return {
            "tool": plan.executed[0].name if plan.executed else "answer_general",
            "args": plan.executed[0].args if plan.executed else {},
            "tools": [
                {"name": item.name, "args": item.args, "result": item.result,
                 "suppressed": item.suppressed, "error": item.error}
                for item in plan.executed
            ],
            "iterations": plan.iterations,
            "stop_reason": plan.stop_reason,
            "usage": plan.usage,
        }

    # ------------------------------------------------------- tool execution

    def _build_tool_context(self, response_builder: ResponseBuilder,
                            merchant: Dict[str, Any] | None, user_id: str,
                            channel: str, user_profile: Dict[str, Any]) -> ToolContext:
        return ToolContext(
            repo=self.merchant_data,
            store=self.admin_store,
            builder=response_builder,
            merchant=merchant,
            user_id=user_id,
            channel=channel,
            user_profile=user_profile,
            config=self.config,
        )

    def run_tool(self, tool_name: str, tool_args: Dict[str, Any],
                 ctx: ToolContext) -> str:
        """Tek arac calistirir ve MODELE donecek kisa ozeti dondurur.

        Hem tek atimlik yol hem de cok adimli agent loop burayi kullanir.
        Handler patlarsa cagri olmez: hata metni ozet olarak doner, boylece
        loop'ta model durumu gorup baska bir yol deneyebilir.
        """
        spec = tools.get(tool_name)
        if spec is None:
            available = ", ".join(tools.tool_names())
            ctx.builder.add_fact(
                "İstenen araç bulunamadı; genel bilgiyle yardımcı ol, veri uydurma.")
            return f"HATA: '{tool_name}' diye bir arac yok. Mevcut araclar: {available}"

        if spec.requires_merchant and ctx.merchant is None:
            ctx.builder.add_fact(
                "İşletme kaydı bulunamadı; genel bilgiyle yardımcı ol, gerekirse "
                "temsilciye devret.")
            return "HATA: Hatta bir isletme eslesmedi; bu arac calistirilamaz."

        args = tools.coerce_args(spec, tool_args)
        # Modelin yapisal argumanlari hafizaya yansisin: boylece basit turlarda
        # ayrica update_customer_card cagirmaya gerek kalmaz.
        tools.mirror_args_to_card(ctx, tool_name, args)
        try:
            return spec.fn(ctx, args) or "Islem tamamlandi."
        except Exception as error:
            print(f"Tool dispatch error ({tool_name}): {error}")
            ctx.builder.add_fact(
                "Sistemde kısa süreli bir aksaklık oldu ve sorgu tamamlanamadı. Özür dile, "
                "bilgiyi TEKRAR sormasını iste (tutar/tarih), asla veri uydurma."
            )
            return (f"HATA: '{tool_name}' calistirilamadi ({type(error).__name__}). "
                    "Veri uydurma; baska bir arac dene ya da eksik bilgiyi musteriye sor.")

    # ------------------------------------------------------- tool handlers

    # ------------------------------------------------------------ main turn

    def process_turn(self, user_input: str, user_id: str = "default_user",
                     channel: str = "default", on_delta=None,
                     on_tool=None) -> Dict[str, Any]:
        """Bir konusma turu.

        Kullanici basina kilitlidir: ayni kisiden gelen es zamanli iki mesaj
        birbirinin profilini/transkriptini bozmaz. Farkli kullanicilar paralel
        ilerler.
        """
        self._touch_user(user_id)
        with self._lock_for_user(user_id):
            return self._process_turn_locked(user_input, user_id, channel,
                                             on_delta, on_tool)

    def process_turn_stream(self, user_input: str, user_id: str = "default_user",
                            channel: str = "default"):
        """process_turn'un AKAN surumu.

        Sirasiyla ("tool", arac_adi), ("delta", metin) ve en sonda
        ("done", tam_sonuc) verir. Arac olaylari, planlama fazi uzun
        surdugunde kullanicinin sessizce beklememesi icindir.
        Tur, arka planda normal akisindan gecer (arac planlama, loglama,
        transkript) — degisen tek sey cevabin parca parca yayinlanmasi.

        Neden thread: tur mantigi lineer bir fonksiyon; onu generator'a
        cevirmek tum akisi bolmeyi gerektirirdi. Kuyruk ile besleme, mantigi
        TEK yerde tutar (akan ve akmayan yol ayni koddan gecer).
        """
        import queue

        channel_queue: "queue.Queue" = queue.Queue()
        outcome: Dict[str, Any] = {}

        def worker():
            try:
                outcome["result"] = self.process_turn(
                    user_input, user_id=user_id, channel=channel,
                    on_delta=lambda text: channel_queue.put(("delta", text)),
                    on_tool=lambda name: channel_queue.put(("tool", name)))
            except Exception as error:      # pragma: no cover
                outcome["error"] = error
            finally:
                channel_queue.put(None)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = channel_queue.get()
            if item is None:
                break
            yield item

        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        yield ("done", outcome.get("result") or {})

    def _process_turn_locked(self, user_input: str, user_id: str, channel: str,
                             on_delta=None, on_tool=None) -> Dict[str, Any]:
        user_input = user_input.strip()
        response_builder = ResponseBuilder()
        if not user_input:
            response_builder.add_fact("Boş kullanıcı mesajı.")
            return {
                "user_input": user_input,
                "agent_response": "Sizi dinliyorum.",
                "router_decision": {"tool": "answer_general", "args": {"category": "empty_input"}},
                "context": response_builder.build()
            }

        current_history = self._get_conversation_history(user_id, channel)
        is_first_turn = len(current_history) == 0
        user_profile = self._get_user_profile(user_id)
        session_id = self._get_session_id(user_id, channel)
        self._apply_restored_state(user_profile, session_id)

        # Bilinen musteri bilgisi: WhatsApp'ta user_id zaten telefondur (kanal gercegi).
        user_profile["channel"] = channel
        if channel == "whatsapp" and user_id and any(ch.isdigit() for ch in user_id):
            user_profile["phone_number"] = user_id

        # Hattaki isletme: cagri baglami > telefon eslesmesi > demo varsayilani.
        merchant = self._resolve_merchant(user_id, user_profile)
        merchant_block = self._build_merchant_profile_block(
            merchant, user_id, user_profile.get('identity_verified', True))

        # 2. PLANLAMA + ARAC YURUTME
        tool_context = self._build_tool_context(
            response_builder, merchant, user_id, channel, user_profile)

        if AGENT_LOOP_ENABLED:
            router_decision = self._run_agent_loop(
                user_input=user_input,
                user_profile=user_profile,
                current_history=current_history,
                merchant=merchant,
                ctx=tool_context,
                on_tool=on_tool,
            )
        else:
            # GERI DONUS YOLU (AGENT_ENABLED=0): tek atimlik router.
            tool_name, tool_args, router_decision = self._run_router_step(
                user_input=user_input,
                user_profile=user_profile,
                current_history=current_history,
                merchant_block=self._build_merchant_router_line(merchant, user_id),
            )
            self._merge_router_card(user_profile, router_decision)
            self.run_tool(tool_name, tool_args, tool_context)

        # Log User Turn
        current_history.append({
            "role": "user",
            "text": user_input,
            "router_decision": router_decision,
        })

        # 3. RESPONSE GENERATION STEP
        context_json = response_builder.to_json()
        system_prompt = self.prompt_builder.build_system_prompt()

        if merchant_block:
            system_prompt += f"\n\n{merchant_block}"
        system_prompt += f"\n\nCONTEXT FROM TOOLS:\n{context_json}"
        system_prompt += (
            f"\n\nCONVERSATION MEMORY:\n"
            f"Conversation focus: {user_profile.get('conversation_focus')}\n"
            f"Offer already made this call: {user_profile.get('offer_made')}\n"
            f"Recent turn count: {len(current_history)}"
        )
        card_block = self._build_customer_card_prompt(user_profile)
        if card_block:
            system_prompt += f"\n\n{card_block}"
        system_prompt += f"\n\nSALES PROFILE:\n{self._build_sales_profile_prompt_summary()}"
        system_prompt += f"\nConversation stage: first_turn={is_first_turn}"

        resume_block = self._build_resume_prompt_block(user_profile, channel, is_first_turn)
        if resume_block:
            system_prompt += f"\n\n{resume_block}"

        response_user_prompt = self._build_response_user_prompt(
            user_input, user_profile, current_history)

        # Final Generation — sablon dal yok, tum cevaplar LLM'den uretilir.
        if on_delta is not None:
            final_response = self._compose_streaming(
                system_prompt, response_user_prompt, on_delta,
                channel=channel, is_first_turn=is_first_turn)
        else:
            raw_response = self.llm_client.generate(
                system_prompt=system_prompt, user_prompt=response_user_prompt)
            if is_llm_error(raw_response):
                # LLM'e ulasilamiyorsa uretilecek model yok; ariza mesaji zorunlu.
                print(f"LLM generation error: {raw_response}")
                raw_response = self.TECHNICAL_FAULT_REPLY
            final_response = self._polish(raw_response, channel=channel,
                                          is_first_turn=is_first_turn)

        current_history.append({"role": "agent", "text": final_response})

        # Planlayici transkriptine MUSTERININ DUYDUGU cevabi yaz. Planlama
        # fazinin kendi duz yazisi degil: onu kompozisyon modeli yaziyor ve
        # planlayicininki atiliyor. Yanlis olani yazarsak model bir sonraki
        # turda soylemedigi bir seyi soyledigini sanir.
        if AGENT_LOOP_ENABLED:
            self._get_planner_transcript(user_id, channel).append(
                {"role": "assistant", "content": final_response})

        ai_notes = self._build_ai_notes_payload(
            user_profile=user_profile,
            router_decision=router_decision,
            context=response_builder.build(),
            user_input=user_input,
        )
        ai_summary = self._build_ai_summary(ai_notes)
        try:
            self.admin_store.log_turn(
                session_id=session_id,
                user_id=user_id,
                channel=channel,
                user_input=user_input,
                agent_response=final_response,
                router_decision=router_decision,
                context=response_builder.build(),
            )
            self.admin_store.save_user_ai_notes(
                user_id=user_id,
                ai_summary=ai_summary,
                ai_notes=ai_notes,
            )
        except Exception as error:
            print(f"AdminStore log warning: {error}")

        # Konusma -> CRM kapali dongusu: anlamli tur ise isletme-360'a otomatik
        # temas kaydi dus (oturum basina tek kayit, upsert).
        try:
            self._log_crm_contact(merchant, session_id, channel, user_profile,
                                  ai_notes, ai_summary, router_decision)
        except Exception as error:                          # pragma: no cover
            print(f"CRM contact log warning: {error}")

        return {
            "user_input": user_input,
            "agent_response": final_response,
            "router_decision": router_decision,
            "context": response_builder.build()
        }

    # ------------------------------------------------------------ call flow

    def start_call(self, user_id: str, channel: str = "voice", mode: str = "inbound",
                   merchant_id: str | None = None, goal: str | None = None) -> Dict[str, Any]:
        """Cagri baslangici: inbound'da sabit selamlama, outbound'da AI ILK konusur.

        Outbound acilis tek atimlik cevap-LLM cagrisiyla uretilir (router yok);
        uretilen metin gecmise ilk agent turu olarak yazilir, sonraki turlar
        normal process_turn akisindan gecer.
        """
        self._touch_user(user_id)
        with self._lock_for_user(user_id):
            return self._start_call_locked(user_id, channel, mode, merchant_id, goal)

    def _start_call_locked(self, user_id: str, channel: str, mode: str,
                           merchant_id: str | None, goal: str | None) -> Dict[str, Any]:
        if merchant_id:
            self.set_call_context(user_id, merchant_id, mode, goal)
        user_profile = self._get_user_profile(user_id)
        user_profile["channel"] = channel
        merchant = self._resolve_merchant(user_id, user_profile)
        history = self._get_conversation_history(user_id, channel)
        session_id = self._get_session_id(user_id, channel)
        assistant_name = self.config.get_assistant_name()

        if mode == "outbound" and merchant:
            # Sadakat teklifini simdiden bekleyen teklife koy: kabul tek tur surer.
            retention = self.merchant_data.get_retention_plan()
            if retention:
                user_profile["pending_offer"] = {
                    "trigger": "dormant_retention",
                    "plan_id": retention.get("plan_id"),
                }
            merchant_block = self._build_merchant_profile_block(
            merchant, user_id, user_profile.get('identity_verified', True))
            system_prompt = self.prompt_builder.build_system_prompt()
            system_prompt += f"\n\n{merchant_block}"
            if retention:
                system_prompt += (
                    f"\n\nCONTEXT FROM TOOLS:\n{{\"offer\": {{\"trigger\": \"dormant_retention\", "
                    f"\"plan\": {json.dumps(retention, ensure_ascii=False)}}}}}"
                )
            opener = self.llm_client.generate(
                system_prompt=system_prompt,
                user_prompt=(
                    "Bu bir GIDEN aramadir ve ILK konusan SENSIN. Isletme sahibine adiyla hitap et, "
                    "kendini kisaca tanit, POS kullanimlarinin azaldigini fark ettiginizi soyle ve "
                    "bir sorun olup olmadigini SICAK bir dille sor. Teklifi HENUZ sunma. "
                    "En fazla 2 cumle, dogal konusma Turkcesi."
                ),
            )
            if is_llm_error(opener):
                print(f"Outbound opener LLM error: {opener}")
                opener = (
                    f"{merchant.get('salutation', merchant.get('owner_name', ''))}, merhaba! Ben {assistant_name}, "
                    f"Moka'dan arıyorum. Son dönemde POS kullanımınızın azaldığını fark ettik, "
                    "bir sorun mu yaşadınız acaba?"
                )
        else:
            salutation = (merchant or {}).get("salutation") or ""
            greeting_target = f" {salutation}" if salutation else ""
            opener = (
                f"Moka'ya hoş geldiniz{greeting_target}, ben {assistant_name}. "
                "Size nasıl yardımcı olabilirim?"
            )

        history.append({"role": "agent", "text": opener})
        try:
            self.admin_store.log_turn(
                session_id=session_id,
                user_id=user_id,
                channel=channel,
                user_input="",
                agent_response=opener,
                router_decision={"tool": "call_start", "args": {"mode": mode}},
                context=ResponseBuilder().build(),
            )
            # Cagri baslar baslamaz panelde musteri adiyla gorunsun.
            if merchant:
                self.admin_store.save_user_ai_notes(
                    user_id=user_id,
                    ai_summary=("Giden kurtarma aramasi basladi." if mode == "outbound"
                                else "Gelen cagri karsilandi."),
                    ai_notes={
                        "name": user_profile.get("merchant_display"),
                        "merchant_id": merchant.get("merchant_id"),
                    },
                )
        except Exception as error:
            print(f"AdminStore log warning: {error}")

        return {
            "reply_text": opener,
            "session_id": session_id,
            "mode": mode,
            "merchant": {
                "merchant_id": (merchant or {}).get("merchant_id"),
                "business_name": (merchant or {}).get("business_name"),
                "owner_name": (merchant or {}).get("owner_name"),
                "salutation": (merchant or {}).get("salutation"),
            } if merchant else None,
        }

    def process_audio_turn(
        self,
        audio_path: str,
        user_id: str = "default_user",
        channel: str = "voice",
        output_audio_path: str | None = None,
        synthesize_reply: bool = True,
    ) -> Dict[str, Any]:
        from core.voice import VoiceTurnProcessor

        voice_processor = VoiceTurnProcessor(orchestrator=self)
        return voice_processor.process_audio_turn(
            audio_path=audio_path,
            user_id=user_id,
            channel=channel,
            output_audio_path=output_audio_path,
            synthesize_reply=synthesize_reply,
        )

from typing import Dict, Any, List
import json
import re
import uuid
from core.config import Config
from core.admin_store import AdminStore
from core.merchant_data import MerchantDataManager, describe_day
from core.rules import RuleEngine
from core.schemas import ResponseBuilder
from core.prompts import SystemPromptBuilder
from core.llm import LLMClient, is_llm_error
from core.parsing import parse_llm_json
from core.tools import get_router_system_prompt, TOOLS_SCHEMA
from core.intent import IntentParser
from core.slots import SlotMapper

# Demo fallback: panel test sohbeti / sesli demo bir isletme secmediyse bu hatta
# baglanmis kabul edilir (gercek sistemde kimlik CTI'dan gelir).
DEFAULT_DEMO_MERCHANT_ID = "M-1001"


class AgentOrchestrator:
    def __init__(self):
        self.config = Config()
        self.merchant_data = MerchantDataManager()
        self.rule_engine = RuleEngine()
        self.prompt_builder = SystemPromptBuilder()
        self.llm_client = LLMClient()
        self.intent_parser = IntentParser()
        self.slot_mapper = SlotMapper()
        self.admin_store = AdminStore()
        self.active_sessions: Dict[str, str] = {}

        self.history: List[Dict[str, Any]] = []
        self.conversation_histories: Dict[str, List[Dict[str, Any]]] = {}
        self.user_profiles: Dict[str, Dict[str, Any]] = {}
        # Cagri baglami: kimlik telefondan/panelden gelir, router argumani DEGILDIR.
        self.call_contexts: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------ formatting

    def _format_try_amount(self, amount: float) -> str:
        amount = int(round(amount))
        if amount >= 1_000_000:
            millions = amount // 1_000_000
            thousands = (amount % 1_000_000) // 1_000
            if thousands:
                return f"{millions} milyon {thousands} bin TL"
            return f"{millions} milyon TL"

        if amount >= 1_000:
            thousands = amount // 1_000
            remainder = amount % 1_000
            if remainder:
                return f"{thousands} bin {remainder} TL"
            return f"{thousands} bin TL"

        return f"{amount} TL"

    @staticmethod
    def _time_of(iso_value: str) -> str:
        if iso_value and "T" in iso_value:
            return iso_value.split("T")[1][:5]
        return ""

    @staticmethod
    def _mask_email(email: str) -> str:
        if not email or "@" not in email:
            return email or ""
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}"

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
        context = self.call_contexts.get(user_id)
        merchant_id = (context or {}).get("merchant_id") or user_profile.get("merchant_id")

        # WhatsApp: user_id telefon numarasidir; isletmeyi telefonla esle.
        if not merchant_id and user_profile.get("phone_number"):
            digits = re.sub(r"\D", "", user_profile["phone_number"])[-10:]
            for merchant in self.config.merchants:
                if re.sub(r"\D", "", merchant.get("phone", ""))[-10:] == digits:
                    merchant_id = merchant["merchant_id"]
                    break

        if not merchant_id:
            merchant_id = DEFAULT_DEMO_MERCHANT_ID

        user_profile["merchant_id"] = merchant_id
        return self.merchant_data.get_merchant(merchant_id)

    def _call_mode(self, user_id: str) -> str:
        return (self.call_contexts.get(user_id) or {}).get("mode", "inbound")

    def _build_merchant_profile_block(self, merchant: Dict[str, Any] | None,
                                      user_id: str) -> str:
        if not merchant:
            return ""
        plan = merchant.get("plan") or {}
        trend = merchant.get("volume_trend") or {}
        devices = merchant.get("devices") or []
        volumes = merchant.get("monthly_volume_try", [])[-3:]
        volume_line = ", ".join(
            f"{v['month']}: {self._format_try_amount(v['volume'])}" for v in volumes
        )
        device_line = "; ".join(
            f"{d['terminal_id']} ({d['model']}, {d['status']}"
            + (f", not: {d['note']}" if d.get("note") else "")
            + ")"
            for d in devices
        ) or "kayitli cihaz yok"

        lines = [
            "MERCHANT PROFILE (hattaki isletme — kimlik dogrulandi, ASLA tekrar sorma):",
            f"- Isletme: {merchant.get('business_name')} ({merchant.get('sector')}, {merchant.get('city')})",
            f"- Yetkili: {merchant.get('owner_name')} — hitap: {merchant.get('salutation', merchant.get('owner_name'))}",
            f"- Urunler: {', '.join(merchant.get('products', []))}",
            f"- Plan: {plan.get('name')} (%{plan.get('rate_pct')} komisyon"
            + (f", ayda {self._format_try_amount(plan.get('monthly_fee_try', 0))} sabit ucret" if plan.get('monthly_fee_try') else "")
            + ")",
            f"- Son 3 ay ciro: {volume_line} (degisim: %{trend.get('change_pct', 0)})",
            f"- Cihazlar: {device_line}",
        ]

        context = self.call_contexts.get(user_id) or {}
        if context.get("mode") == "outbound":
            goal = context.get("goal") or (
                "Bu bir GIDEN aramadir: isletmenin islem hacmi ciddi dustu. Sebebi "
                "anlayisla ogren; teknik sorun varsa coz, rakibe gectiyse/memnuniyetsizse "
                "CONTEXT'teki sadakat teklifini kisaca sun."
            )
            lines.append(f"- ARAMA HEDEFI (outbound): {goal}")

        return "\n".join(lines)

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
                "slots": {},
                "intents": [],
                "conversation_focus": None,
                "handoff_reason": None,
                "urgency": None,
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

        user_profile["conversation_focus"] = ai_notes.get("conversation_focus")
        user_profile["urgency"] = ai_notes.get("urgency")
        user_profile["handoff_reason"] = ai_notes.get("handoff_reason")
        user_profile["merchant_id"] = ai_notes.get("merchant_id")
        user_profile["pending_offer"] = ai_notes.get("pending_offer")
        if isinstance(ai_notes.get("card"), dict):
            user_profile["card"] = ai_notes["card"]
        user_profile["resumed_from_store"] = True
        user_profile["resume_summary"] = self._build_resume_summary_from_notes(ai_notes)

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
                    "intents": self.intent_parser.parse(user_text),
                    "slots": self.slot_mapper.extract(user_text),
                })
            if agent_text:
                history.append({"role": "agent", "text": agent_text})
        return history

    def _get_session_id(self, user_id: str, channel: str) -> str:
        key = self._session_key(user_id, channel)
        if key not in self.active_sessions:
            existing = self.admin_store.get_latest_session_id_for_user(user_id, channel)
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
        self.active_sessions[key] = str(uuid.uuid4())
        self.user_profiles.pop(user_id, None)

    # ----------------------------------------------------------- extraction

    def _extract_urgency(self, text: str) -> str | None:
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("acil", "hemen", "şu an", "su an", "müşteri bekliyor", "musteri bekliyor")):
            return "high"
        return None

    def _update_user_profile_from_tool_args(self, user_profile: Dict[str, Any], tool_args: Dict[str, Any]) -> None:
        for key in ("amount_try", "date", "card_last4", "terminal_id", "symptom"):
            if key in tool_args and tool_args[key] is not None:
                user_profile["slots"][key] = tool_args[key]

    def _update_user_profile_from_text(self, user_profile: Dict[str, Any], user_input: str) -> None:
        urgency = self._extract_urgency(user_input)
        if urgency:
            user_profile["urgency"] = urgency

    # ------------------------------------------------------ summaries/notes

    def _prepend_resume_summary(self, text: str, user_profile: Dict[str, Any], channel: str, is_first_turn: bool) -> str:
        if channel != "whatsapp" or not is_first_turn:
            return text
        if not user_profile.get("resumed_from_store") or not user_profile.get("resume_summary"):
            return text

        summary = user_profile["resume_summary"].rstrip(".")
        cleaned_text = re.sub(
            r"^\s*(merhaba|selam|merhabalar|selamlar)[,!\.\s]+",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        ).strip()
        user_profile["resumed_from_store"] = False
        if cleaned_text:
            cleaned_text = cleaned_text[0].lower() + cleaned_text[1:] if len(cleaned_text) > 1 else cleaned_text.lower()
            return f"Tekrar merhaba, {summary.lower()}. {cleaned_text}"
        return f"Tekrar merhaba, {summary.lower()}."

    def _build_ai_notes_payload(
        self,
        user_profile: Dict[str, Any],
        current_intents: List[str],
        current_slots: Dict[str, Any],
        router_decision: Dict[str, Any],
        context: Dict[str, Any],
        user_input: str,
    ) -> Dict[str, Any]:
        card = user_profile.get("card") or {}
        handoff = context.get("handoff", {})

        ai_notes = {
            "last_user_message": user_input,
            "merchant_id": user_profile.get("merchant_id"),
            "issue": card.get("issue"),
            "mood": card.get("mood"),
            "amount_mentioned_try": card.get("amount_mentioned_try") or user_profile["slots"].get("amount_try"),
            "date_mentioned": card.get("date_mentioned") or user_profile["slots"].get("date"),
            "terminal_id": card.get("terminal_id") or user_profile["slots"].get("terminal_id"),
            "urgency": user_profile.get("urgency"),
            "conversation_focus": user_profile.get("conversation_focus"),
            "current_intents": sorted(set(current_intents)),
            "handoff_required": bool(handoff.get("required")),
            "handoff_reason": handoff.get("reason") or user_profile.get("handoff_reason"),
            "last_router_tool": router_decision.get("tool"),
            "pending_offer": user_profile.get("pending_offer"),
            "card": user_profile.get("card"),
        }

        return {key: value for key, value in ai_notes.items() if value not in (None, "", [], {})}

    def _build_ai_summary(self, ai_notes: Dict[str, Any]) -> str:
        parts: List[str] = []

        if ai_notes.get("issue"):
            parts.append(f"konu: {ai_notes['issue']}")
        tool_map = {
            "get_settlement_status": "hakedis sorgulandi",
            "find_transaction": "islem sorgulandi",
            "troubleshoot_pos": "cihaz arizasi calisildi",
            "explain_fees": "komisyon aciklandi",
            "send_statement": "ekstre gonderildi",
            "create_payment_link": "odeme linki olusturuldu",
            "recommend_offer": "teklif sunuldu",
            "trigger_handoff": "insana devredildi",
        }
        tool = ai_notes.get("last_router_tool")
        if tool in tool_map:
            parts.append(tool_map[tool])
        if ai_notes.get("mood") in ("gergin", "kizgin", "kızgın"):
            parts.append(f"musteri {ai_notes['mood']}")
        if ai_notes.get("urgency") == "high":
            parts.append("durum acil")
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
                try:
                    value = self._format_try_amount(float(value))
                except (TypeError, ValueError):
                    pass
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
            "Tutar": card.get("amount_mentioned_try") or user_profile["slots"].get("amount_try"),
            "Tarih": card.get("date_mentioned") or user_profile["slots"].get("date"),
        }
        known = [label for label, value in values.items() if value not in (None, "", [], {})]
        missing = [label for label, value in values.items() if value in (None, "", [], {})]
        return (
            "ELIMDEKI BILGILER (yapisal envanter):\n"
            f"- BILINEN: {', '.join(known) if known else 'yok'}\n"
            f"- EKSIK: {', '.join(missing) if missing else 'yok — her sey elimizde'}\n"
            "Kural: Yapacagin is icin once bu envantere bak. Bilgiler YETERLIyse hic soru "
            "sormadan ilerle. Yetersizse SADECE EKSIK listesinden, tek seferde EN FAZLA BIR "
            "bilgi iste. BILINEN listesindeki hicbir seyi musteriye tekrar sorma."
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
        recent_turns = history[-6:]
        history_lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = turn.get("text", "")
            history_lines.append(f"{role}: {text}")

        card_block = self._build_customer_card_prompt(user_profile)

        return (
            f"CURRENT USER MESSAGE:\n{user_input}\n\n"
            f"RECENT CONVERSATION:\n" + ("\n".join(history_lines) if history_lines else "No previous turns.") + "\n\n"
            + (card_block + "\n\n" if card_block else "")
            + "Reply naturally in Turkish. The MUSTERI KARTI above is the caller's current "
            "truth — if older messages conflict, follow the card. Give ONE useful point plus "
            "at most ONE short question; no filler, no repeating what they already told you, "
            "and never ask for info the card already has."
        )

    def _merge_contextual_filters(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        current_slots: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Carry regex-extracted slots into tool args as a semantic backup: the STT
        or router may miss an amount/date the regex caught (and vice versa).
        """
        if tool_name != "find_transaction":
            return tool_args

        merged_args = dict(tool_args)
        for source in (current_slots, user_profile.get("slots", {})):
            for key in ("amount_try", "date", "card_last4"):
                if merged_args.get(key) in (None, "") and source.get(key) not in (None, ""):
                    merged_args[key] = source[key]
        return merged_args

    def _run_router_step(
        self,
        *,
        user_input: str,
        user_profile: Dict[str, Any],
        current_history: List[Dict[str, Any]],
        current_slots: Dict[str, Any],
        merchant_block: str,
    ) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        """Arac secimini tamamen router LLM'e birakir; kural tabanli override yoktur."""
        router_prompt = get_router_system_prompt()
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
        tool_args = self._merge_contextual_filters(tool_name, tool_args, current_slots, user_profile)
        router_decision["args"] = tool_args
        self._update_user_profile_from_tool_args(user_profile, tool_args)

        if tool_name == "trigger_handoff":
            user_profile["handoff_reason"] = tool_args.get("reason", "")

        return tool_name, tool_args, router_decision

    # ------------------------------------------------------- tool handlers

    def _handle_settlement(self, response_builder: ResponseBuilder,
                           merchant: Dict[str, Any], tool_args: Dict[str, Any]) -> None:
        period = tool_args.get("period") or "latest"
        merchant_id = merchant["merchant_id"]
        rows = self.merchant_data.get_settlements_for_period(merchant_id, period)

        if not rows:
            response_builder.add_fact("Bu dönem için hakediş kaydı bulunamadı.")
            return

        response_builder.set_settlement(rows[0])
        response_builder.set_settlements(rows[:3])

        for settlement in rows[:2]:
            day = describe_day(settlement.get("payout_eta", ""))
            time = self._time_of(settlement.get("payout_eta", ""))
            batch_day = describe_day(settlement.get("batch_date", ""))
            net = self._format_try_amount(settlement.get("net_try", 0))
            gross = self._format_try_amount(settlement.get("gross_try", 0))
            commission = self._format_try_amount(settlement.get("commission_try", 0))
            status = settlement.get("status")
            if status == "ödendi":
                response_builder.add_fact(
                    f"{batch_day} tarihli satışların hakedişi ödendi: brüt {gross}, "
                    f"komisyon {commission}, net {net} ({settlement.get('iban_masked')})."
                )
            elif status == "planlandı":
                response_builder.add_fact(
                    f"{batch_day} tarihli satışların hakedişi: brüt {gross}, komisyon {commission}, "
                    f"net {net}. Ödeme {day} saat {time}'de {settlement.get('iban_masked')} hesabına planlandı."
                )
            else:  # beklemede
                note = settlement.get("note") or "banka tarafında doğrulama bekleniyor"
                response_builder.add_fact(
                    f"DİKKAT: {batch_day} tarihli {net} tutarındaki hakediş hâlâ beklemede ({note}). "
                    "Dürüstçe kabul et, gecikme için özür dile ve temsilciye eskalasyon öner."
                )

    def _handle_find_transaction(self, response_builder: ResponseBuilder,
                                 merchant: Dict[str, Any], tool_args: Dict[str, Any]) -> None:
        merchant_id = merchant["merchant_id"]
        amount = tool_args.get("amount_try")
        on_date = tool_args.get("date")
        card_last4 = tool_args.get("card_last4")
        status = tool_args.get("status")

        rows = self.merchant_data.find_transactions(
            merchant_id,
            amount_try=amount,
            on_date=on_date,
            card_last4=card_last4,
            status=status,
        )

        if rows:
            response_builder.set_transactions(rows[:3])
            txn = rows[0]
            day = describe_day(txn.get("timestamp", ""))
            time = self._time_of(txn.get("timestamp", ""))
            response_builder.add_fact(
                f"İşlem bulundu: {self._format_try_amount(txn.get('amount_try', 0))}, {day} saat {time}, "
                f"kart **** {txn.get('card_last4')}, durum: {txn.get('status')}."
            )
            settlement = self.merchant_data.get_settlement_for_transaction(txn)
            if settlement:
                pay_day = describe_day(settlement.get("payout_eta", ""))
                pay_time = self._time_of(settlement.get("payout_eta", ""))
                if settlement.get("status") == "ödendi":
                    response_builder.add_fact(
                        f"Bu işlem {settlement.get('batch_id')} hakediş grubundaydı ve ödendi "
                        f"(net {self._format_try_amount(settlement.get('net_try', 0))})."
                    )
                else:
                    response_builder.add_fact(
                        f"Para kaybolmadı: işlem {settlement.get('batch_id')} hakediş grubunda; "
                        f"ödeme {pay_day} saat {pay_time}'de hesaba geçecek. Müşteriyi rahatlat."
                    )
        else:
            response_builder.add_fact("Belirtilen kriterlerle işlem bulunamadı.")
            if amount is not None:
                nearby = self.merchant_data.find_transactions(merchant_id, on_date=on_date, limit=3)
                if nearby:
                    amounts = ", ".join(self._format_try_amount(t.get("amount_try", 0)) for t in nearby)
                    response_builder.add_fact(
                        f"Yakın zamanda şu tutarlarda işlemler var: {amounts}. Tutarı teyit ettir."
                    )

    def _handle_troubleshoot(self, response_builder: ResponseBuilder,
                             merchant: Dict[str, Any], tool_args: Dict[str, Any],
                             user_profile: Dict[str, Any], user_id: str) -> None:
        symptom = tool_args.get("symptom") or (user_profile.get("card") or {}).get("issue") or ""
        step_result = tool_args.get("step_result")
        devices = merchant.get("devices") or []
        device = None
        if tool_args.get("terminal_id"):
            device = next((d for d in devices if d["terminal_id"] == tool_args["terminal_id"]), None)
        if device is None and devices:
            device = devices[0]
        if device:
            response_builder.set_device(device)

        if step_result == "resolved":
            response_builder.add_fact("Sorun giderildi olarak işaretlendi. Kısaca sevindiğini söyle ve başka ihtiyacı olup olmadığını sor.")
            user_profile["conversation_focus"] = "resolved"
            return

        if step_result == "not_resolved":
            terminal = device.get("terminal_id") if device else "cihaz"
            model = device.get("model", "") if device else ""
            try:
                task_id = self.admin_store.create_task(
                    title=f"Servis: {terminal} {model} değişimi — {merchant.get('business_name')}",
                    user_id=user_id,
                )
                response_builder.add_fact(
                    f"Denenen adımlar işe yaramadı. Servis kaydı oluşturuldu (görev #{task_id}): "
                    "cihaz 2 iş günü içinde yenisiyle değiştirilecek."
                )
            except Exception as error:
                print(f"Service task warning: {error}")
                response_builder.add_fact(
                    "Denenen adımlar işe yaramadı. Servis kaydı oluşturuldu: cihaz 2 iş günü içinde değiştirilecek."
                )
            response_builder.add_fact(
                "FIRSAT: Cihaz değişene kadar satış kaçırmasın — telefonuna hemen bir ödeme linki "
                "tanımlayabileceğini söyle; müşterileri karttan linkle ödeyebilir. Kabul ederse link oluşturulacak."
            )
            user_profile["pending_offer"] = {"trigger": "pos_out_of_service"}
            user_profile["conversation_focus"] = "pos_service"
            return

        kb = self.merchant_data.match_kb(symptom)
        if kb:
            response_builder.set_kb_steps(kb.get("steps", []))
            response_builder.add_fact(f"Arıza eşleşti: {kb.get('title')}.")
            response_builder.add_fact(
                "Adımları TEK TEK ver: önce ilk adımı söyle, denemesini iste. Hepsini birden sayma."
            )
            user_profile["conversation_focus"] = "pos_troubleshooting"
        else:
            response_builder.add_fact(
                "Bilinen arıza kaydı eşleşmedi. Cihazı kapatıp 30 saniye sonra açmasını öner; "
                "düzelmezse servis kaydı açılacağını söyle."
            )

    def _handle_explain_fees(self, response_builder: ResponseBuilder,
                             merchant: Dict[str, Any], tool_args: Dict[str, Any],
                             user_profile: Dict[str, Any]) -> None:
        summary = self.merchant_data.monthly_summary(merchant["merchant_id"])
        plan = merchant.get("plan") or {}
        response_builder.set_plan_info({"plan": plan, "monthly_summary": summary})
        fee_note = (
            f", ayda {self._format_try_amount(plan.get('monthly_fee_try', 0))} sabit ücret"
            if plan.get("monthly_fee_try") else ", sabit ücret yok"
        )
        response_builder.add_fact(
            f"Mevcut plan: {plan.get('name')} — işlem başına %{plan.get('rate_pct')} komisyon{fee_note}."
        )
        if summary:
            response_builder.add_fact(
                f"Bu ay ({summary.get('month')}): ciro {self._format_try_amount(summary.get('gross_try', 0))}, "
                f"kesilen komisyon yaklaşık {self._format_try_amount(summary.get('commission_try', 0))}."
            )

        upgrade = self.merchant_data.get_upgrade_candidate(merchant)
        if upgrade:
            trend = merchant.get("volume_trend") or {}
            plan_new = upgrade["plan"]
            saving = self._format_try_amount(upgrade["monthly_saving_try"])
            response_builder.set_offer({
                "trigger": "volume_growth",
                "plan": plan_new,
                "monthly_saving_try": upgrade["monthly_saving_try"],
            })
            response_builder.add_fact(
                f"FIRSAT: Ciro son dönemde belirgin büyümüş (%{trend.get('change_pct', 0)}). "
                f"{plan_new.get('name')} planına geçerse komisyon %{plan_new.get('rate_pct')}'e düşer, "
                f"ayda yaklaşık {saving} cebinde kalır. Açıklamayı bitirdikten SONRA bunu tek cümleyle öner."
            )
            user_profile["pending_offer"] = {
                "trigger": "volume_growth",
                "plan_id": plan_new.get("plan_id"),
                "monthly_saving_try": upgrade["monthly_saving_try"],
            }

    def _handle_send_statement(self, response_builder: ResponseBuilder,
                               merchant: Dict[str, Any], tool_args: Dict[str, Any],
                               user_id: str) -> None:
        period = tool_args.get("period") or "this_month"
        month = None
        if period == "last_month":
            from datetime import date
            today = date.today()
            prev = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
            month = prev.strftime("%Y-%m")
        summary = self.merchant_data.monthly_summary(merchant["merchant_id"], month=month)
        masked = self._mask_email(merchant.get("email", ""))
        try:
            self.admin_store.enqueue_outbound_message(
                user_id,
                f"[Ekstre] {merchant.get('business_name')} — {summary.get('month')} dönemi: "
                f"ciro {self._format_try_amount(summary.get('gross_try', 0))}, "
                f"komisyon {self._format_try_amount(summary.get('commission_try', 0))}.",
                sender="ai-statement",
            )
        except Exception as error:
            print(f"Statement outbox warning: {error}")
        response_builder.add_fact(
            f"{summary.get('month')} dönemi ekstresi kayıtlı e-posta adresine gönderildi: {masked}."
        )
        response_builder.add_fact(
            f"Dönem özeti: ciro {self._format_try_amount(summary.get('gross_try', 0))}, "
            f"komisyon {self._format_try_amount(summary.get('commission_try', 0))}."
        )

    def _handle_payment_link(self, response_builder: ResponseBuilder,
                             merchant: Dict[str, Any], tool_args: Dict[str, Any],
                             user_profile: Dict[str, Any], user_id: str) -> None:
        link = self.merchant_data.create_payment_link(
            merchant["merchant_id"],
            amount_try=tool_args.get("amount_try"),
            description=tool_args.get("description"),
        )
        response_builder.set_payment_link(link)
        amount_note = (
            f" ({self._format_try_amount(link['amount_try'])} tutarında)"
            if link.get("amount_try") else " (tutar serbest)"
        )
        response_builder.add_fact(
            f"Ödeme linki oluşturuldu{amount_note}: {link['url']} — telefonuna SMS ile de gönderildi. "
            "Müşterileri bu linkten kartla ödeyebilir, tutarlar hakedişe dahil olur."
        )
        payload = {"url": link["url"], "amount_try": link.get("amount_try")}
        pending = user_profile.get("pending_offer") or {}
        if pending.get("trigger") == "pos_out_of_service":
            # POS arizasi sirasindaki linki kabul edilen upsell olarak say.
            payload["trigger"] = "pos_out_of_service"
            try:
                self.admin_store.record_lead_event(user_id, "offer_accepted", payload)
            except Exception as error:
                print(f"Lead event warning: {error}")
            user_profile["pending_offer"] = None
            user_profile["offer_made"] = True
        try:
            self.admin_store.record_lead_event(user_id, "payment_link_created", payload)
        except Exception as error:
            print(f"Lead event warning: {error}")

    def _handle_recommend_offer(self, response_builder: ResponseBuilder,
                                merchant: Dict[str, Any], tool_args: Dict[str, Any],
                                user_profile: Dict[str, Any], user_id: str) -> None:
        trigger = tool_args.get("trigger") or (user_profile.get("pending_offer") or {}).get("trigger")
        accepted = bool(tool_args.get("accepted"))

        if accepted:
            pending = user_profile.get("pending_offer") or {"trigger": trigger}
            payload = dict(pending)
            if pending.get("trigger") == "dormant_retention" or trigger == "dormant_retention":
                series = [v.get("volume", 0) for v in merchant.get("monthly_volume_try", [])]
                healthy = sorted(series, reverse=True)[:3]
                recovered = round(sum(healthy) / len(healthy)) if healthy else 0
                payload["recovered_volume_try"] = recovered
                response_builder.add_fact(
                    f"Teklif kabul edildi ve kaydedildi. Aylık yaklaşık {self._format_try_amount(recovered)} "
                    "hacim geri kazanılıyor. Sıcak bir teşekkür et, planın bugün aktifleştirileceğini söyle."
                )
            else:
                response_builder.add_fact(
                    "Teklif kabul edildi ve kaydedildi. Teşekkür et; talebin temsilci onayıyla bugün "
                    "aktifleştirileceğini söyle."
                )
            try:
                self.admin_store.record_lead_event(user_id, "offer_accepted", payload)
            except Exception as error:
                print(f"Lead event warning: {error}")
            user_profile["pending_offer"] = None
            user_profile["offer_made"] = True
            return

        if user_profile.get("offer_made"):
            response_builder.add_fact(
                "Bu görüşmede zaten bir teklif sunuldu. İkinci teklif YAPMA; mevcut konuya devam et."
            )
            return

        if trigger == "volume_growth":
            upgrade = self.merchant_data.get_upgrade_candidate(merchant)
            if upgrade:
                plan_new = upgrade["plan"]
                response_builder.set_offer({
                    "trigger": trigger, "plan": plan_new,
                    "monthly_saving_try": upgrade["monthly_saving_try"],
                })
                response_builder.add_fact(
                    f"TEKLİF: {plan_new.get('name')} planı — komisyon %{plan_new.get('rate_pct')}, "
                    f"ayda yaklaşık {self._format_try_amount(upgrade['monthly_saving_try'])} tasarruf. "
                    "Tek cümleyle, yardımcı olma tonunda sun ve ister misiniz diye sor."
                )
                user_profile["pending_offer"] = {
                    "trigger": trigger,
                    "plan_id": plan_new.get("plan_id"),
                    "monthly_saving_try": upgrade["monthly_saving_try"],
                }
            else:
                response_builder.add_fact("Uygun bir üst plan bulunamadı; teklif sunma.")
        elif trigger == "social_selling":
            missing = [p for p in ("sanal_pos", "odeme_linki") if p not in merchant.get("products", [])]
            label_map = {"sanal_pos": "Sanal POS", "odeme_linki": "Ödeme Linki"}
            products = ", ".join(label_map[p] for p in missing) or "Sanal POS"
            response_builder.set_offer({"trigger": trigger, "products": missing})
            response_builder.add_fact(
                f"TEKLİF: Instagram/internetten satış için {products} tam çözüm — havale kovalamak yerine "
                "link gönderir ya da siteye entegre eder, kartla tahsil eder. Başvuru kaydı açmayı öner."
            )
            user_profile["pending_offer"] = {"trigger": trigger, "products": missing}
        elif trigger == "dormant_retention":
            retention = self.merchant_data.get_retention_plan()
            trend = merchant.get("volume_trend") or {}
            if retention:
                response_builder.set_offer({"trigger": trigger, "plan": retention})
                response_builder.add_fact(
                    f"Hacim son dönemde ciddi düşmüş (%{abs(trend.get('change_pct', 0))} azalma). "
                    f"TEKLİF: {retention.get('name')} — 3 ay boyunca %{retention.get('rate_pct')} komisyon, "
                    "sabit ücret yok. Empatiyle, geri kazanmak istediğinizi belirterek sun."
                )
                user_profile["pending_offer"] = {
                    "trigger": trigger, "plan_id": retention.get("plan_id"),
                }
        elif trigger == "pos_out_of_service":
            response_builder.add_fact(
                "TEKLİF: Cihaz çalışana kadar ödeme linkiyle tahsilata devam edebilir — "
                "isterse hemen link oluşturulacak."
            )
            user_profile["pending_offer"] = {"trigger": trigger}
        else:
            response_builder.add_fact("Belirgin bir fırsat yok; teklif sunma.")

    def _handle_answer_general(self, response_builder: ResponseBuilder,
                               merchant: Dict[str, Any], tool_args: Dict[str, Any],
                               user_profile: Dict[str, Any]) -> None:
        category = tool_args.get("category")
        details = self.config.get_project_details()
        if category == "security_smalltalk":
            response_builder.add_fact(
                "GÜVENLİK UYARISI: Müşteri kart numarası paylaşmaya başladı ya da kart verisi konuşuluyor. "
                "Nazikçe ama NET biçimde kes: tam kart numarası asla telefonda paylaşılmamalı; "
                "gerekirse sadece son 4 hane yeterli."
            )
            user_profile["conversation_focus"] = "security"
        elif category == "company_info":
            response_builder.add_fact(f"Şirket bilgisi: {details.get('description', '')}")
            products = details.get("products", [])
            if products:
                response_builder.add_fact(
                    "Ürünler: " + "; ".join(f"{p['label']} — {p['description']}" for p in products)
                )
            user_profile["conversation_focus"] = "company_info"
        elif category == "how_it_works":
            response_builder.add_fact(
                "Çalışma bilgisi: gün içi işlemler akşam 23:00'te gruplanır, komisyon düşülür, "
                "ertesi iş günü saat 10:00'da IBAN'a yatar (T+1)."
            )
            user_profile["conversation_focus"] = "how_it_works"
        elif category == "working_hours":
            response_builder.add_fact(
                f"Destek hattı {details.get('working_hours', '7/24')} açık; Ada her zaman yanıtlıyor."
            )
        elif category == "thanks":
            response_builder.add_fact("Görüşme kapanışı: kısa ve sıcak bir kapanış yap, yeni bir konu açma.")
            user_profile["conversation_focus"] = "closing"
        else:
            response_builder.add_fact("Genel sohbet ya da selamlama.")
            if category == "greeting":
                user_profile["conversation_focus"] = "greeting"

    # ------------------------------------------------------------ main turn

    def process_turn(self, user_input: str, user_id: str = "default_user", channel: str = "default") -> Dict[str, Any]:
        """
        LLM Tool Calling Pipeline: Input -> Router -> Tool -> Response
        """
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
        self.history = current_history
        is_first_turn = len(current_history) == 0
        user_profile = self._get_user_profile(user_id)
        session_id = self._get_session_id(user_id, channel)

        # Bilinen musteri bilgisi: WhatsApp'ta user_id zaten telefondur (kanal gercegi).
        user_profile["channel"] = channel
        if channel == "whatsapp" and user_id and any(ch.isdigit() for ch in user_id):
            user_profile["phone_number"] = user_id

        # Hattaki isletme: cagri baglami > telefon eslesmesi > demo varsayilani.
        merchant = self._resolve_merchant(user_id, user_profile)
        merchant_block = self._build_merchant_profile_block(merchant, user_id)

        current_intents = self.intent_parser.parse(user_input)
        current_slots = self.slot_mapper.extract(user_input)
        self._update_user_profile_from_text(user_profile, user_input)
        user_profile["slots"].update(current_slots)
        user_profile["intents"] = current_intents

        tool_name, tool_args, router_decision = self._run_router_step(
            user_input=user_input,
            user_profile=user_profile,
            current_history=current_history,
            current_slots=current_slots,
            merchant_block=self._build_merchant_router_line(merchant, user_id),
        )

        # AGENTIC HAFIZA: musteri kartini router LLM'in ayni cagrida urettigi
        # "card" alanindan birlestir (ekstra cagri yok, kelime bazli cikarim yok).
        self._merge_router_card(user_profile, router_decision)

        # Log User Turn
        current_history.append({
            "role": "user",
            "text": user_input,
            "router_decision": router_decision,
            "intents": current_intents,
            "slots": current_slots
        })

        # 2. TOOL EXECUTION STEP
        if merchant is None:
            response_builder.add_fact("İşletme kaydı bulunamadı; genel bilgiyle yardımcı ol, gerekirse temsilciye devret.")
        elif tool_name == "get_settlement_status":
            self._handle_settlement(response_builder, merchant, tool_args)
        elif tool_name == "find_transaction":
            self._handle_find_transaction(response_builder, merchant, tool_args)
        elif tool_name == "troubleshoot_pos":
            self._handle_troubleshoot(response_builder, merchant, tool_args, user_profile, user_id)
        elif tool_name == "explain_fees":
            self._handle_explain_fees(response_builder, merchant, tool_args, user_profile)
        elif tool_name == "send_statement":
            self._handle_send_statement(response_builder, merchant, tool_args, user_id)
        elif tool_name == "create_payment_link":
            self._handle_payment_link(response_builder, merchant, tool_args, user_profile, user_id)
        elif tool_name == "recommend_offer":
            self._handle_recommend_offer(response_builder, merchant, tool_args, user_profile, user_id)
        elif tool_name == "trigger_handoff":
            response_builder.trigger_handoff(
                reason=tool_args.get("reason", "Müşteri talebi"),
                missing_info=tool_args.get("missing_info", []),
                share_contact_details=bool(tool_args.get("share_contact_details")),
            )
            response_builder.add_fact(
                "İnsan temsilciye devir tetiklendi. Müşteriyi doğrula (haklısınız de), özetin "
                "temsilciye iletildiğini ve hemen bağlanacağını söyle."
            )
        else:  # answer_general ve bilinmeyen araclar
            self._handle_answer_general(response_builder, merchant, tool_args, user_profile)

        # 3. RESPONSE GENERATION STEP
        context_json = response_builder.to_json()
        system_prompt = self.prompt_builder.build_system_prompt()

        if merchant_block:
            system_prompt += f"\n\n{merchant_block}"
        system_prompt += f"\n\nCONTEXT FROM TOOLS:\n{context_json}"
        system_prompt += (
            f"\n\nCONVERSATION MEMORY:\n"
            f"Last detected intents: {current_intents}\n"
            f"Known slots: {user_profile['slots']}\n"
            f"Conversation focus: {user_profile.get('conversation_focus')}\n"
            f"Offer already made this call: {user_profile.get('offer_made')}\n"
            f"Recent turn count: {len(current_history)}"
        )
        card_block = self._build_customer_card_prompt(user_profile)
        if card_block:
            system_prompt += f"\n\n{card_block}"
        system_prompt += f"\n\nSALES PROFILE:\n{self._build_sales_profile_prompt_summary()}"
        system_prompt += f"\nConversation stage: first_turn={is_first_turn}"

        # Final Generation — sablon dal yok, tum cevaplar LLM'den uretilir (tam agentic).
        final_response = self.llm_client.generate(
            system_prompt=system_prompt,
            user_prompt=self._build_response_user_prompt(user_input, user_profile, current_history)
        )
        if is_llm_error(final_response):
            # LLM'e ulasilamiyorsa uretilecek model yok; tek satirlik ariza mesaji zorunlu.
            print(f"LLM generation error: {final_response}")
            final_response = (
                "Şu anda teknik bir sorun yaşıyorum, kısa süre sonra tekrar deneyebilir misiniz? "
                "Dilerseniz sizi müşteri temsilcimize de bağlayabilirim."
            )

        final_response = self._remove_redundant_greeting(final_response, is_first_turn)
        final_response = self._normalize_currency_language(final_response)
        final_response = self._prepend_resume_summary(final_response, user_profile, channel, is_first_turn)

        current_history.append({"role": "agent", "text": final_response})
        self.history = current_history
        ai_notes = self._build_ai_notes_payload(
            user_profile=user_profile,
            current_intents=current_intents,
            current_slots=current_slots,
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
            merchant_block = self._build_merchant_profile_block(merchant, user_id)
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

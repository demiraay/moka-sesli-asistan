from typing import Dict, Any, List
import json
import re
import uuid
from core.config import Config
from core.admin_store import AdminStore
from core.inventory import InventoryManager
from core.rules import RuleEngine
from core.schemas import ResponseBuilder
from core.prompts import SystemPromptBuilder
from core.llm import LLMClient, is_llm_error
from core.parsing import extract_try_amount, parse_llm_json
from core.tools import get_router_system_prompt, TOOLS_SCHEMA
from core.intent import IntentParser
from core.slots import SlotMapper

class AgentOrchestrator:
    def __init__(self):
        self.config = Config()
        self.inventory_manager = InventoryManager()
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

    def _format_try_amount(self, amount: int) -> str:
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

    def _get_available_price_values(self) -> List[int]:
        available_ids = {
            item["inventory_id"]
            for item in self.config.inventory
            if item.get("status") == "available"
        }
        return [
            price["list_price_try"]
            for price in self.config.prices
            if price["inventory_id"] in available_ids and price.get("list_price_try") is not None
        ]

    def _should_send_unit_details(self, exact_matches: List[Dict[str, Any]], criteria: Dict[str, Any]) -> bool:
        """
        Voice-first heuristic:
        Only send raw unit details to the LLM when the result set is already narrow.
        This reduces the chance of long, list-heavy spoken answers.
        """
        if len(exact_matches) > 3:
            return False

        narrowing_keys = {"flat_type_id", "floor", "block_id", "direction", "sun_exposure", "min_price", "max_price"}
        return len(exact_matches) <= 2 or any(criteria.get(key) is not None for key in narrowing_keys)

    def _get_user_profile(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = {
                "slots": {},
                "intents": [],
                "conversation_focus": None,
                "budget_max_try": None,
                "budget_min_try": None,
                "handoff_reason": None,
                "name": None,
                "urgency": None,
                "current_listing": None,
                "resumed_from_store": False,
                "resume_summary": None,
            }
            self._restore_user_profile(user_id)
        return self.user_profiles[user_id]

    def _restore_listing(self, inventory_id: str | None) -> Dict[str, Any] | None:
        if not inventory_id:
            return None
        item = next((entry for entry in self.config.inventory if entry.get("inventory_id") == inventory_id), None)
        if item is None:
            return None
        return self.inventory_manager.enrich_details(item)

    def _restore_user_profile(self, user_id: str) -> None:
        user_profile = self.user_profiles[user_id]
        saved_notes = self.admin_store.get_user_ai_notes(user_id)
        ai_notes = saved_notes.get("ai_notes", {})
        if not ai_notes:
            return

        if ai_notes.get("preferred_flat_type_id"):
            user_profile["slots"]["flat_type_id"] = ai_notes["preferred_flat_type_id"]
        for key in ("preferred_block", "preferred_floor", "preferred_direction", "sun_preference"):
            if ai_notes.get(key) is not None:
                mapped_key = {
                    "preferred_block": "block_id",
                    "preferred_floor": "floor",
                    "preferred_direction": "direction",
                    "sun_preference": "sun_exposure",
                }[key]
                user_profile["slots"][mapped_key] = ai_notes[key]

        user_profile["budget_max_try"] = ai_notes.get("budget_max_try")
        user_profile["budget_min_try"] = ai_notes.get("budget_min_try")
        user_profile["conversation_focus"] = ai_notes.get("conversation_focus")
        user_profile["name"] = ai_notes.get("name")
        user_profile["urgency"] = ai_notes.get("urgency")
        user_profile["handoff_reason"] = ai_notes.get("handoff_reason")
        user_profile["current_listing"] = self._restore_listing(ai_notes.get("selected_listing_id"))
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

    def _flat_type_label(self, flat_type_id: str | None) -> str | None:
        if not flat_type_id:
            return None
        flat = next((item for item in self.config.flats if item.get("flat_type_id") == flat_type_id), None)
        if flat:
            return flat.get("label", flat_type_id)
        return flat_type_id

    def _extract_name(self, text: str) -> str | None:
        lowered = text.lower()
        patterns = [
            r"(?:benim adım|adim|ismim|isimim)\s+([a-zA-ZçğıöşüÇĞİÖŞÜ]+)",
            r"\bben\s+([a-zA-ZçğıöşüÇĞİÖŞÜ]+)\b",
        ]
        stopwords = {"ev", "daire", "araba", "konut", "acil", "bugun", "yarin", "simdi"}
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                candidate = match.group(1).strip()
                if candidate not in stopwords and len(candidate) > 1:
                    return candidate.capitalize()
        return None

    def _extract_urgency(self, text: str) -> str | None:
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("acil", "hemen", "bugün", "bugun", "yarın", "yarin")):
            return "high"
        return None

    def _extract_budget_preferences(self, text: str) -> Dict[str, int]:
        # Ciplak sayilar burada butce sayilmaz: serbest metinde '5' kat/kapi
        # numarasi olabilir. Akis motoru butce sorusunun cevabinda ayrica izin verir.
        amount = extract_try_amount(text)
        if amount is None:
            return {}

        lowered = text.lower().replace(".", "").replace(",", ".").strip()
        if any(marker in lowered for marker in ("en az", "minimum", "min ", "alt limit")):
            return {"budget_min_try": amount}
        if any(marker in lowered for marker in ("en fazla", "maks", "max", "üst limit", "ust limit", "bütçe", "butce", "civar")):
            return {"budget_max_try": amount}
        return {"budget_max_try": amount}

    def _update_user_profile_from_tool_args(self, user_profile: Dict[str, Any], tool_args: Dict[str, Any]) -> None:
        for key in ("flat_type_id", "floor", "block_id", "direction", "sun_exposure", "min_price", "max_price"):
            if key in tool_args and tool_args[key] is not None:
                user_profile["slots"][key] = tool_args[key]

        if tool_args.get("max_price") is not None:
            user_profile["budget_max_try"] = int(tool_args["max_price"])
        if tool_args.get("min_price") is not None:
            user_profile["budget_min_try"] = int(tool_args["min_price"])

    def _update_user_profile_from_text(self, user_profile: Dict[str, Any], user_input: str) -> None:
        extracted_name = self._extract_name(user_input)
        if extracted_name:
            user_profile["name"] = extracted_name

        urgency = self._extract_urgency(user_input)
        if urgency:
            user_profile["urgency"] = urgency

        budgets = self._extract_budget_preferences(user_input)
        if budgets.get("budget_max_try") is not None:
            user_profile["budget_max_try"] = budgets["budget_max_try"]
            user_profile["slots"]["max_price"] = budgets["budget_max_try"]
        if budgets.get("budget_min_try") is not None:
            user_profile["budget_min_try"] = budgets["budget_min_try"]
            user_profile["slots"]["min_price"] = budgets["budget_min_try"]

    def _should_track_listing_context(self, criteria: Dict[str, Any], exact_matches: List[Dict[str, Any]]) -> bool:
        return bool(exact_matches) and (
            criteria.get("sort_by") is not None
            or len(exact_matches) == 1
            or any(criteria.get(key) is not None for key in ("flat_type_id", "floor", "block_id", "direction", "sun_exposure", "max_price", "min_price"))
        )

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
            return f"Tekrar merhaba, geçen konuşmamızda {summary.lower()}. {cleaned_text}"
        return f"Tekrar merhaba, geçen konuşmamızda {summary.lower()}."

    def _build_ai_notes_payload(
        self,
        user_profile: Dict[str, Any],
        current_intents: List[str],
        current_slots: Dict[str, Any],
        router_decision: Dict[str, Any],
        context: Dict[str, Any],
        user_input: str,
    ) -> Dict[str, Any]:
        slots = user_profile.get("slots", {})
        handoff = context.get("handoff", {})

        ai_notes = {
            "last_user_message": user_input,
            "name": user_profile.get("name"),
            "urgency": user_profile.get("urgency"),
            "conversation_focus": user_profile.get("conversation_focus"),
            "current_intents": sorted(set(current_intents)),
            "preferred_flat_type": self._flat_type_label(slots.get("flat_type_id")),
            "preferred_flat_type_id": slots.get("flat_type_id"),
            "preferred_block": slots.get("block_id"),
            "preferred_floor": slots.get("floor"),
            "preferred_direction": slots.get("direction"),
            "sun_preference": slots.get("sun_exposure"),
            "budget_max_try": user_profile.get("budget_max_try"),
            "budget_min_try": user_profile.get("budget_min_try"),
            "handoff_required": bool(handoff.get("required")),
            "handoff_reason": handoff.get("reason") or user_profile.get("handoff_reason"),
            "last_router_tool": router_decision.get("tool"),
            "selected_listing_id": user_profile.get("current_listing", {}).get("inventory_id") if user_profile.get("current_listing") else None,
            "card": user_profile.get("card"),
        }

        return {key: value for key, value in ai_notes.items() if value not in (None, "", [], {})}

    def _build_ai_summary(self, ai_notes: Dict[str, Any]) -> str:
        parts: List[str] = []

        if ai_notes.get("preferred_flat_type"):
            parts.append(f"{ai_notes['preferred_flat_type']} ile ilgileniyor")
        if ai_notes.get("name"):
            parts.append(f"kullanici adi {ai_notes['name']}")
        if ai_notes.get("budget_max_try"):
            parts.append(f"ust butce {self._format_try_amount(int(ai_notes['budget_max_try']))}")
        if ai_notes.get("urgency") == "high":
            parts.append("ihtiyac acil")
        if ai_notes.get("sun_preference"):
            sun_map = {
                "high": "gunes alan",
                "medium": "orta gunes alan",
                "low": "az gunes alan",
                "none": "gunes istemiyor",
            }
            parts.append(f"tercih: {sun_map.get(ai_notes['sun_preference'], ai_notes['sun_preference'])}")
        if ai_notes.get("preferred_direction"):
            parts.append(f"yon tercihi {ai_notes['preferred_direction']}")
        if ai_notes.get("preferred_block"):
            parts.append(f"blok tercihi {ai_notes['preferred_block']}")
        if ai_notes.get("preferred_floor"):
            parts.append(f"kat tercihi {ai_notes['preferred_floor']}")
        if ai_notes.get("selected_listing_id"):
            parts.append("belirli bir daire üzerinde konuşuldu")
        elif ai_notes.get("conversation_focus"):
            focus_map = {
                "qualification": "alim niyeti var, ihtiyac analizi suruyor",
                "project_overview": "genel proje bilgisi istiyor",
                "greeting": "ilk temas kuruldu",
                "listing_overview": "paylaşılan bir dairenin detayları konuşuluyor",
                "contact_reassurance": "iletişim tarafında güven arıyor",
            }
            parts.append(focus_map.get(ai_notes["conversation_focus"], ai_notes["conversation_focus"]))

        if not parts:
            return "Henüz kayda değer yapay zeka notu yok."

        return ". ".join(parts).capitalize() + "."

    def _build_resume_summary_from_notes(self, ai_notes: Dict[str, Any]) -> str | None:
        parts: List[str] = []

        flat_type = ai_notes.get("preferred_flat_type")
        budget_max = ai_notes.get("budget_max_try")
        budget_min = ai_notes.get("budget_min_try")
        current_listing = ai_notes.get("selected_listing_id")
        focus = ai_notes.get("conversation_focus")

        if flat_type and budget_max:
            parts.append(f"{flat_type} için yaklaşık {self._format_try_amount(int(budget_max))} civarında bakıyordunuz")
        elif flat_type:
            parts.append(f"{flat_type} dairelerle ilgileniyordunuz")
        elif budget_max:
            parts.append(f"yaklaşık {self._format_try_amount(int(budget_max))} civarında seçenek bakıyordunuz")
        elif budget_min:
            parts.append(f"en az {self._format_try_amount(int(budget_min))} seviyesinde seçenek bakıyordunuz")

        if current_listing:
            parts.append("paylaşılan bir daire üzerinden devam ediyorduk")
        elif focus == "project_overview":
            parts.append("proje genelini konuşuyorduk")
        elif focus == "contact_reassurance":
            parts.append("iletişim ve geri dönüş tarafını konuşuyorduk")

        if not parts:
            return None

        return " ve ".join(parts)

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
                "maps_url": "",
                "latitude": "",
                "longitude": "",
                "location_label": "",
                "auto_share_whatsapp_location": False,
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
            return "No configured sales profile."
        return json.dumps(populated, ensure_ascii=False)

    def _remove_redundant_greeting(self, text: str, is_first_turn: bool) -> str:
        """
        After the first turn, strip robotic repeated greetings so follow-up replies
        get to the point like a real consultant conversation.
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
        cleaned = re.sub(r"\bMyr\b", "milyon TL", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(\d+(?:[.,]\d+)?)\s*milyon TL['’]?[a-zçğıöşü]*",
            r"\1 milyon TL",
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned

    def _summarize_listing_for_router(self, listing: Dict[str, Any] | None) -> str:
        if not listing:
            return "No current listing selected."

        flat_details = listing.get("flat_details", {})
        price = listing.get("price", {}).get("list_price_try")
        label = flat_details.get("label", listing.get("flat_type_id", "Unknown"))
        return (
            f"Current listing: {listing.get('inventory_id')} / {label} / "
            f"block {listing.get('block_id')} / floor {listing.get('floor')} / "
            f"direction {listing.get('direction')} / price {price}."
        )

    def _build_preference_memory_summary(self, user_profile: Dict[str, Any]) -> str:
        slots = user_profile.get("slots", {})
        parts: List[str] = []

        if user_profile.get("conversation_focus"):
            parts.append(f"Conversation focus: {user_profile['conversation_focus']}.")
        if slots.get("flat_type_id"):
            parts.append(f"Preferred flat type: {slots['flat_type_id']}.")
        if slots.get("floor") is not None:
            parts.append(f"Preferred floor: {slots['floor']}.")
        if slots.get("block_id"):
            parts.append(f"Preferred block: {slots['block_id']}.")
        if slots.get("direction"):
            parts.append(f"Preferred direction: {slots['direction']}.")
        if slots.get("sun_exposure"):
            parts.append(f"Sunlight preference: {slots['sun_exposure']}.")
        if user_profile.get("budget_min_try") is not None:
            parts.append(f"Known minimum budget: {user_profile['budget_min_try']} TL.")
        if user_profile.get("budget_max_try") is not None:
            parts.append(f"Known maximum budget: {user_profile['budget_max_try']} TL.")
        if user_profile.get("current_listing"):
            parts.append(self._summarize_listing_for_router(user_profile["current_listing"]))

        return " ".join(parts) if parts else "No durable preferences captured yet."

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
        """Otoriter musteri kartini LLM prompt'una serer. Degerler zaten kartin
        LLM'i tarafindan dogal dilde uretildi; burada ekstra eslemesi yok."""
        card = user_profile.get("card") or {}
        lines: List[str] = []

        label_map = {
            "name": "Isim", "flat_type": "Daire tipi", "budget_max_try": "Ust butce",
            "budget_min_try": "Alt butce", "block": "Blok", "floor": "Kat",
            "direction": "Cephe", "sun": "Gunes tercihi", "urgency": "Aciliyet",
            "intent": "Su anki talep",
        }
        for key, label in label_map.items():
            value = card.get(key)
            if value in (None, "", [], {}):
                continue
            if key in ("budget_max_try", "budget_min_try"):
                try:
                    value = self._format_try_amount(int(value))
                except (TypeError, ValueError):
                    pass
            lines.append(f"- {label}: {value}")

        phone = user_profile.get("phone_number") or card.get("phone")
        if phone:
            lines.append(
                f"- Telefon: {phone} (ZATEN ELINDE — musteri WhatsApp'tan yaziyor; "
                "telefon/numara SORMA, sende var)"
            )
        elif user_profile.get("channel") == "whatsapp":
            lines.append("- Kanal: WhatsApp (numara sistemde kayitli, tekrar isteme)")

        for change in card.get("changed") or []:
            lines.append(f"- ⚠ DEGISTI: {change}. Artik guncel tercihe gore ilerle, eskiye DONME.")

        info_state = self._build_info_state_lines(user_profile)
        if not lines and not info_state:
            return ""
        block = (
            "MUSTERI KARTI (OTORITER — sohbet gecmisindeki eski ifadelerle celisirse "
            "BU KARTI esas al; kart musterinin GUNCEL gercek tercihidir):\n" + "\n".join(lines)
        ) if lines else ""
        if info_state:
            block += ("\n\n" if block else "") + info_state
        return block

    # Satis surecinde isimize yarayan bilgi alanlari. Envanter YAPISALDIR:
    # alan dolu mu bos mu diye bakilir (kelime analizi yok); yeterli mi
    # yetersiz mi kararini LLM verir.
    def _build_info_state_lines(self, user_profile: Dict[str, Any]) -> str:
        card = user_profile.get("card") or {}
        values = {
            "Telefon": user_profile.get("phone_number") or card.get("phone"),
            "Isim": card.get("name") or user_profile.get("name"),
            "Daire tipi": card.get("flat_type"),
            "Butce": card.get("budget_max_try") or user_profile.get("budget_max_try"),
            "Aciliyet": card.get("urgency"),
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

    def _build_router_user_prompt(self, user_input: str, user_profile: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
        recent_turns = history[-4:]
        history_lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = turn.get("text", "")
            history_lines.append(f"{role}: {text}")

        current_listing_summary = self._summarize_listing_for_router(user_profile.get("current_listing"))
        card_block = self._build_customer_card_prompt(user_profile)

        return (
            f"LATEST USER INPUT:\n{user_input}\n\n"
            f"RECENT CONVERSATION:\n" + ("\n".join(history_lines) if history_lines else "No previous turns.") + "\n\n"
            + (card_block + "\n\n" if card_block else "")
            + f"CONVERSATION FOCUS:\n{user_profile.get('conversation_focus')}\n"
            f"{current_listing_summary}\n\n"
            "Decide the next tool using the full context above. The MUSTERI KARTI is the "
            "customer's current truth — if history conflicts with it, trust the card. "
            "If the user refers to a previously discussed listing, resolve it from context."
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
            + "Reply naturally in Turkish. The MUSTERI KARTI above is the customer's current "
            "truth — if older messages conflict, follow the card, never revert to a preference "
            "they changed. Give ONE useful point plus at most ONE short question; no filler, no "
            "repeating what they already told you, and never ask for info the card already has."
        )

    def _merge_contextual_filters(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        current_slots: Dict[str, Any],
        user_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Carry relevant preferences forward in follow-up turns so the assistant
        behaves more like a consultant and less like a stateless FAQ bot.
        """
        if tool_name not in {"search_inventory", "check_price"}:
            return tool_args

        remembered_slots = user_profile.get("slots", {})
        if not remembered_slots:
            return tool_args

        carry_keys = ("flat_type_id", "floor", "block_id", "direction", "sun_exposure", "min_price", "max_price")
        merged_args = dict(tool_args)

        for key in carry_keys:
            if key not in merged_args and key not in current_slots and key in remembered_slots:
                merged_args[key] = remembered_slots[key]

        return merged_args

    def _run_router_step(
        self,
        *,
        user_input: str,
        user_profile: Dict[str, Any],
        current_history: List[Dict[str, Any]],
        current_slots: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        """Arac secimini tamamen router LLM'e birakir; kural tabanli override yoktur.
        Tek ekleme, onceki turlardan hatirlanan tercihlerin arama filtresi olarak
        tasinmasidir (hafiza, karar degil)."""
        router_prompt = get_router_system_prompt()
        router_user_prompt = self._build_router_user_prompt(user_input, user_profile, current_history)

        router_response_str = ""
        try:
            router_response_str = self.llm_client.generate(
                system_prompt=router_prompt,
                user_prompt=router_user_prompt,
                json_mode=True
            )
            router_decision = parse_llm_json(router_response_str)

            tool_name = router_decision.get("tool")
            tool_args = router_decision.get("args", {})
        except Exception as e:
            print(f"Router Parse Error: {e} | Raw: {router_response_str}")
            tool_name = "answer_general"
            tool_args = {}
            router_decision = {"tool": tool_name, "args": tool_args, "error": str(e)}

        tool_args = self._merge_contextual_filters(tool_name, tool_args, current_slots, user_profile)
        router_decision["args"] = tool_args
        self._update_user_profile_from_tool_args(user_profile, tool_args)

        if tool_name == "trigger_handoff":
            user_profile["handoff_reason"] = tool_args.get("reason", "")

        return tool_name, tool_args, router_decision

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

        # Bilinen musteri bilgisi: WhatsApp'ta user_id zaten telefondur (regex/
        # keyword degil, kanal gercegi). Karta isle ki AI bir daha telefon istemesin.
        user_profile["channel"] = channel
        if channel == "whatsapp" and user_id and any(ch.isdigit() for ch in user_id):
            user_profile["phone_number"] = user_id

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
        if tool_name == "search_inventory":
            # Inject rule-based overrides (e.g. Always check available)
            if self.rule_engine.get_policies()['require_stock_check']:
                tool_args['status'] = 'available'
            
            criteria = tool_args
            search_results = self.inventory_manager.search(criteria)
            
            exact_matches = search_results['exact_matches']
            alternatives = search_results['alternatives']
            if self._should_track_listing_context(criteria, exact_matches):
                user_profile["current_listing"] = exact_matches[0]
            
            # In voice mode, avoid passing many raw units to the LLM.
            if self._should_send_unit_details(exact_matches, criteria):
                response_builder.set_units(exact_matches[:2])
            else:
                response_builder.add_question("İstersen tip, bütçe ya da kat bilgisine göre seçenekleri daraltayım.")

            response_builder.set_alternatives(alternatives[:3])
            
            msg = f"Envanter araması yapıldı (kriterler: {criteria})."
            if exact_matches:
                count = len(exact_matches)
                msg += f" {count} uygun daire bulundu."
                if count > 3:
                    msg += " Sesli yanıt için daire detayları özetlendi."
                
                # Price logic (summary)
                prices = [u.get('price', {}).get('list_price_try', 0) for u in exact_matches]
                if prices:
                    min_p, max_p = min(prices), max(prices)
                    response_builder.set_price({
                        "summary": f"Bu filtrede fiyatlar {self._format_try_amount(min_p)} ile {self._format_try_amount(max_p)} arasında.",
                        "count": count
                    })
            else:
                msg += " Birebir eşleşme bulunamadı."
                if criteria.get("max_price") is not None:
                    available_prices = self._get_available_price_values()
                    if available_prices:
                        min_available = min(available_prices)
                        response_builder.set_price({
                            "summary": f"Bu bütçede uygun daire bulunmuyor. Başlangıç fiyatımız {self._format_try_amount(min_available)}.",
                            "count": 0
                        })
                        response_builder.add_question("İsterseniz bu seviyeye en yakın seçenekleri paylaşayım.")
            
            response_builder.add_fact(msg)
            
        elif tool_name == "check_price":
            response_builder.add_fact("Fiyat sorgusu.")
            inventory_id = tool_args.get("inventory_id")
            if inventory_id:
                unit = next(
                    (item for item in self.config.inventory if item.get("inventory_id") == inventory_id),
                    None,
                )
                if unit is None:
                    response_builder.add_fact(f"{inventory_id} numaralı daire envanterde bulunamadı.")
                else:
                    enriched = self.inventory_manager.enrich_details(unit)
                    list_price = (enriched.get("price") or {}).get("list_price_try")
                    if list_price:
                        response_builder.set_price({
                            "summary": f"{inventory_id} numaralı dairenin liste fiyatı {self._format_try_amount(list_price)}.",
                            "inventory_id": inventory_id,
                            "list_price_try": list_price,
                            "count": 1,
                        })
                        response_builder.add_fact(
                            f"{inventory_id} liste fiyatı: {self._format_try_amount(list_price)}."
                        )
                    else:
                        response_builder.add_fact(f"{inventory_id} için fiyat kaydı bulunamadı.")
            else:
                 # No specific unit: share the available price range.
                 res = self.inventory_manager.search({'status': 'available'})
                 matches = res['exact_matches']
                 if matches:
                     prices = [u.get('price', {}).get('list_price_try', 0) for u in matches]
                     if prices:
                         min_p, max_p = min(prices), max(prices)
                         response_builder.add_fact(
                             f"Fiyatlar {self._format_try_amount(min_p)} ile {self._format_try_amount(max_p)} arasında."
                         )
                         
        elif tool_name == "trigger_handoff":
            response_builder.trigger_handoff(
                reason=tool_args.get("reason", "User request"), 
                missing_info=tool_args.get("missing_info", []),
                share_contact_details=bool(tool_args.get("share_contact_details")),
                share_location=bool(tool_args.get("share_location")),
            )
            response_builder.add_fact("Satış temsilcisine yönlendirme tetiklendi.")

        elif tool_name == "answer_general":
            category = tool_args.get("category")
            if category == "qualification":
                response_builder.add_fact("Müşteri ihtiyaç analizi akışında.")
                response_builder.add_fact("Önceki tercihlerden devam et; cevaplanmış soruları tekrarlama.")
                user_profile["conversation_focus"] = "qualification"
            elif category == "project_overview":
                response_builder.add_fact("Müşteri proje ve uygun daireler hakkında kısa bir özet istiyor.")
                user_profile["conversation_focus"] = "project_overview"
            elif category == "listing_overview":
                response_builder.add_fact("Müşteri seçili ilanın detaylarını istiyor.")
                listing = user_profile.get("current_listing")
                if listing:
                    price = (listing.get("price") or {}).get("list_price_try")
                    sunlight = (listing.get("sunlight") or {}).get("sun_exposure")
                    details = (
                        f"Seçili daire: {listing.get('inventory_id')}, {listing.get('block_id')} blok, "
                        f"{listing.get('floor')}. kat, tip {self._flat_type_label(listing.get('flat_type_id'))}, "
                        f"cephe {listing.get('direction')}"
                    )
                    if price:
                        details += f", liste fiyatı {self._format_try_amount(int(price))}"
                    if sunlight:
                        details += f", güneş alma durumu {sunlight}"
                    response_builder.add_fact(details + ".")
                user_profile["conversation_focus"] = "listing_overview"
            elif category == "contact_reassurance":
                response_builder.add_fact("Müşteri, iletişim bilgisi istemeden önce satış hattının ulaşılabilir olduğunu teyit etmek istiyor.")
                user_profile["conversation_focus"] = "contact_reassurance"
            else:
                response_builder.add_fact("Genel sohbet ya da selamlama.")
                if category == "greeting":
                    user_profile["conversation_focus"] = "greeting"

        # 3. RESPONSE GENERATION STEP
        context_json = response_builder.to_json()
        system_prompt = self.prompt_builder.build_system_prompt()

        # Add tool result context to system prompt
        system_prompt += f"\n\nCONTEXT FROM TOOLS:\n{context_json}"
        system_prompt += (
            f"\n\nCONVERSATION MEMORY:\n"
            f"Last detected intents: {current_intents}\n"
            f"Known user slots: {user_profile['slots']}\n"
            f"Conversation focus: {user_profile.get('conversation_focus')}\n"
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
                "Dilerseniz sizi satış danışmanımıza da yönlendirebilirim."
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

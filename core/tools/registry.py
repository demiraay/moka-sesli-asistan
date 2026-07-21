"""Arac kayit defteri — araclarin TEK kaynagi.

Onceki hal: ayni 9 arac DORT ayri yerde tanimliydi — TOOLS_SCHEMA (olu kod),
COMPACT_TOOL_GUIDE (LLM'e giden tek kopya), tool_map (panel etiketleri) ve
if/elif dispatch zinciri. Aralarinda tutarsizlik birikmisti; ornegin
trigger_handoff'un share_contact_details parametresi semada YOKTU ama kod ve
panel kuyrugu onu kullaniyordu.

Artik her arac tek bir @tool bildirimidir; sema, dispatch ve panel etiketi
hepsi ondan turetilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.formatting import parse_amount_text

# Arac sinifi — agent loop'un yan etki guvenligi bunlara dayanir.
PURE = "pure"                # tekrar cagrilabilir, yan etkisiz
SIDE_EFFECT = "side_effect"  # dis dunyayi degistirir (kayit, mesaj, lead)
TERMINAL = "terminal"        # calistiginda dongu biter (handoff)

# Sema token butcesi: aciklamalar KISA olmali. tools=[...] her iterasyonda
# gider; eski tam sema ~2782 token tutuyordu ve Groq free tier TPM limitini
# tek turda yiyordu. Bu tavan bir testle kilitli (test_tool_registry).
MAX_DESCRIPTION_CHARS = 200


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., str]
    kind: str = PURE
    panel_label: str = ""
    once_per_turn: bool = True
    once_per_call: bool = False
    requires_merchant: bool = True


REGISTRY: Dict[str, ToolSpec] = {}


def tool(*, name: str, description: str, parameters: Dict[str, Any],
         kind: str = PURE, panel_label: str = "", once_per_turn: bool = True,
         once_per_call: bool = False, requires_merchant: bool = True):
    """Bir fonksiyonu arac olarak kaydeder.

    Handler imzasi: fn(ctx: ToolContext, args: dict) -> str
    Donen string MODELE beslenir (kisa tutulmali); yapisal veri ctx.builder'a
    yazilir ve panel/DB sozlesmesini besler.
    """
    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        if name in REGISTRY:
            raise ValueError(f"Arac zaten kayitli: {name}")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(
                f"'{name}' aciklamasi cok uzun ({len(description)} > "
                f"{MAX_DESCRIPTION_CHARS}). Token butcesi: bkz. registry.py")
        REGISTRY[name] = ToolSpec(
            name=name, description=description, parameters=parameters, fn=fn,
            kind=kind, panel_label=panel_label, once_per_turn=once_per_turn,
            once_per_call=once_per_call, requires_merchant=requires_merchant)
        return fn
    return decorator


def get(name: str) -> Optional[ToolSpec]:
    return REGISTRY.get(name)


def tool_names() -> List[str]:
    return list(REGISTRY)


def openai_tools_schema() -> List[Dict[str, Any]]:
    """OpenAI/Groq uyumlu tools=[...] yuku.

    Eski TOOLS_SCHEMA duz {name, description, parameters} bicimindeydi; modern
    API {"type": "function", "function": {...}} sarmali bekler.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in REGISTRY.values()
    ]


def panel_tool_labels() -> Dict[str, str]:
    """Panel ozeti icin arac -> Turkce etiket.

    Eskiden orchestrator icinde elle yazilmis bir sozluktu ve 'answer_general'
    eksikti; artik registry'den turetilir.
    """
    return {name: spec.panel_label for name, spec in REGISTRY.items() if spec.panel_label}


def tool_guide() -> str:
    """Arac rehberini REGISTRY'den TURETIR.

    Eskiden COMPACT_TOOL_GUIDE adiyla elle yazilmis ve semadan bagimsiz
    surukleniyordu; ikisi arasinda tutarsizlik birikmisti.
    """
    lines = []
    for index, spec in enumerate(REGISTRY.values(), start=1):
        properties = (spec.parameters or {}).get("properties", {})
        required = set((spec.parameters or {}).get("required", []))
        params = []
        for key, schema in properties.items():
            marker = "" if key in required else "?"
            enum = schema.get("enum")
            params.append(f"{key}{marker}" + (f": {'|'.join(map(str, enum))}" if enum else ""))
        signature = f"{spec.name}{{{', '.join(params)}}}" if params else spec.name
        lines.append(f"{index}. {signature} — {spec.description}")
    return "\n".join(lines)


def build_planner_system_prompt() -> str:
    """Agent loop'un Faz A (planlama) sistem prompt'u.

    Araclarin KENDISI tools=[...] ile gider; burada yalnizca KARAR SEZGILERI
    durur. Persona/ses kurallari buraya GIRMEZ — onlara sadece Faz B'de
    (cevap uretimi) ihtiyac var, token butcesi bunu gerektiriyor.
    """
    return """Sen Moka United'in Turkce sesli destek asistaninin arac planlayicisisin.
Arayan bir UYE ISYERI sahibi; kimligi hattan biliniyor, ASLA kim oldugunu sorma.

GOREVIN: dogru araclari cagirmak. Duz yazi cevap URETME — cevabi baska bir model yazacak.

EN ONEMLI AYRIM — SORU MU, TALEP MI?
Musteri bir sey SORUYORSA (nasil, ne zaman, hangi, nereye, mumkun mu, ne kadar
surer, ne olur) bu BILGI ISTEGIDIR. Cevabi anlatilir; EYLEM YAPILMAZ.
  - "Ekstreyi nasil yollayacaksin?"  -> answer_general  (ASLA send_statement!)
  - "Link gonderebiliyor musun?"     -> answer_general  (ASLA create_payment_link!)
  - "Cihazi degistirebiliyor musunuz?" -> answer_general
Eylem araci ancak ACIK TALEP ya da ONAY varsa calisir:
  "gonder", "olustur", "evet", "olur", "yap", "tamam gonder".
Supheye dusersen EYLEM YAPMA — bilgi ver ve teklif et. Yapilmamis bir eylemi
geri almak, yapilmamis olmasindan cok daha kotudur.

SORMA, BAK:
Musteriye VERI SORMA — veri SENDE. Musteri bir tutar/tarih/kart soylediyse
ARAMA yap, sorma.
  - "44 bin 104 ne?"          -> find_transaction{amount_try:44104}
                                 ya da get_settlement_status. TARIHINI SORMA.
  - "dun bir islem vardi"     -> find_transaction{date:"dun"}
  - "param nerede"            -> get_settlement_status
Arama bos donerse ya da birden fazla sonuc cikarsa O ZAMAN ayirt edici tek bir
ayrinti sorulur. Once ARA, sonra gerekiyorsa sor.

KURALLAR:
1. Once musterinin ASIL sorununu cozen araci cagir. Cihaz arizasi > para > genel.
2. Aracin sonucunu OKU. Eksik bilgi varsa (ornegin islem bulunamadi) baska bir
   filtreyle TEKRAR dene ya da araci birak — musteriye sorulacagini varsay.
3. TESHIS ONCE: musteri bir sikayet ediyorsa ("param yatmadi", "kesinti yuksek")
   once DURUMU OKUYAN araci cagir. Ayni turda cozum/teklif/eylem araci EKLEME —
   once ne oldugunu ogren, musteriye anlat, sonraki turda devam et.
4. Yeni bilgi ciktikca update_customer_card cagir (sorun, tutar, tarih, ruh hali).
5. GELIR: teklif araclarini yalnizca asil sorun ele alindiktan SONRA cagir.
   Gorusme basina tek teklif.
6. DEVIR: ofke ("yeter artik", "sikayet edecegim"), dolandiricilik, chargeback,
   hukuki tehdit, hesap kapatma, iki kez cozulemeyen ariza veya acik temsilci
   talebinde trigger_handoff. Sakin bir soru DEVIR DEGILDIR.
7. Argumanlara ASLA veri uydurma — yalnizca musterinin soyledigi degerler.
8. TUR BASINA EN AZ ARAC: bir sonraki adimi atmadan once musterinin cevabini
   bekle. Gerekli araclari calistirdiktan sonra DUR (bos cevap don).
"""


def build_router_system_prompt() -> str:
    """Tek atimlik router icin sistem prompt'u (agent loop oncesi yol)."""
    return f"""You are a smart orchestrator for the AI support agent of Moka United, a Turkish payment company. The caller is a MERCHANT (isletme sahibi) whose identity is already known from the phone line. Your job is to analyze the User Input and select the correct TOOL to execute.

AVAILABLE TOOLS (name{{args}} — when to use):
{tool_guide()}

INSTRUCTIONS:
1. Output MUST be valid JSON only. No markdown. Format: {{ "tool": "...", "args": {{...}}, "card": {{...}} }}
2. "card" = caller memory. Update the "MUSTERI KARTI" from the new message: replace changed values (never keep both), keep the rest, never invent, null when unknown. Fields (all optional): owner_name, business_name, issue (short Turkish phrase), amount_mentioned_try (number), date_mentioned, terminal_id, card_last4, mood ("sakin"/"gergin"/"kizgin"), upsell_opportunity, changed (what changed THIS message; [] if nothing).
3. Multiple asks -> pick the most critical (device down > money > general).
4. ANGER: mood "kizgin" + unresolved/repeated complaint -> trigger_handoff. A calm question is NOT a handoff.
5. REVENUE: offer only AFTER the actual issue is addressed.
6. FOLLOW-UPS: interpret short replies from recent conversation.
7. INFO SUFFICIENCY: "ELIMDEKI BILGILER" lists BILINEN/EKSIK. missing_info may only contain EKSIK items. Merchant identity/business/phone are ALWAYS known from the line — never ask, never list as missing.
8. NEVER invent amounts, dates or details in args — only what the caller said. Resolve pronouns ("o islem") from context.
"""


def coerce_args(spec: ToolSpec, args: Dict[str, Any]) -> Dict[str, Any]:
    """Model argumanlarini semaya gore duzeltir.

    Model sayiyi metin olarak dondurebilir ("1.250"); bunlar tutara cevrilir.
    Cevrilemeyen sayisal alanlar DUSURULUR — sozlukte None olarak birakilirsa
    handler "deger verildi ama bos" sanir.
    """
    if not isinstance(args, dict):
        return {}
    properties = (spec.parameters or {}).get("properties", {})
    coerced: Dict[str, Any] = {}

    for key, value in args.items():
        schema = properties.get(key)
        if schema is None:
            coerced[key] = value
            continue

        expected = schema.get("type")
        if expected == "number" or expected == "integer":
            parsed = parse_amount_text(value)
            if parsed is None:
                continue                      # anahtari tamamen at
            coerced[key] = int(parsed) if expected == "integer" else parsed
        elif expected == "boolean":
            if isinstance(value, str):
                coerced[key] = value.strip().lower() in ("true", "1", "evet", "yes")
            else:
                coerced[key] = bool(value)
        elif expected == "string":
            coerced[key] = value if isinstance(value, str) else str(value)
        elif expected == "array":
            coerced[key] = value if isinstance(value, list) else [value]
        else:
            coerced[key] = value

    return coerced

"""AI gunluk brifing uretimi.

Panel verilerinden (KPI, sicak leadler, bekleyen handoff'lar, takip listesi,
stok) LLM ile danisman icin kisa bir Turkce sabah brifingi uretir ve kaydeder.
"""

import json
from typing import Any, Dict

from core.llm import is_llm_error

BRIEFING_SYSTEM_PROMPT = """Sen bir konut projesi satış ofisinin operasyon asistanısın.
Danışman güne başlarken okuyacağı KISA bir Türkçe brifing yazacaksın.

KURALLAR:
- En fazla 8 kısa madde; her madde tek satır, başında "- " olsun.
- Önce acil işler (bekleyen müşteriler, dolmak üzere opsiyonlar), sonra fırsatlar, sonra genel durum.
- İsimleri, telefonları ve sayıları verilerden AYNEN kullan; asla veri uydurma.
- Veri yoksa o konuyu hiç yazma; boş kategoriler için madde üretme.
- Samimi ama profesyonel bir dil kullan; emoji kullanma.
- Sonuna tek cümlelik motive edici bir kapanış ekleyebilirsin."""


def generate_briefing(store, llm_client) -> str:
    """Brifing uretir, kaydeder ve metni dondurur. LLM'e ulasilamazsa ValueError."""
    context = store.get_briefing_context()
    user_prompt = (
        "GÜNÜN VERİLERİ (JSON):\n"
        + json.dumps(context, ensure_ascii=False, indent=1)
        + "\n\nBu verilerle danışman için sabah brifingini yaz."
    )

    text = llm_client.generate(system_prompt=BRIEFING_SYSTEM_PROMPT, user_prompt=user_prompt)
    if is_llm_error(text) or not str(text).strip():
        raise ValueError("Brifing üretilemedi: LLM'e ulaşılamıyor.")

    content = str(text).strip()
    store.save_briefing(content)
    return content

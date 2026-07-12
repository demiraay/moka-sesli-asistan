"""AI gunluk brifing uretimi.

Panel verilerinden (KPI, gelir olaylari, bekleyen handoff'lar, uyuyan
isletmeler, takip listesi) LLM ile destek ekibi icin kisa bir Turkce sabah
brifingi uretir ve kaydeder.
"""

import json
from typing import Any, Dict

from core.llm import is_llm_error

BRIEFING_SYSTEM_PROMPT = """Sen Moka United'ın sesli destek operasyonunun asistanısın.
Destek ekibi güne başlarken okuyacağı KISA bir Türkçe brifing yazacaksın.

KURALLAR:
- En fazla 8 kısa madde; her madde tek satır, başında "- " olsun.
- Önce acil işler (insan bekleyen çağrılar, geciken hakedişler), sonra gelir fırsatları
  (uyuyan işletmeler, bekleyen teklifler), sonra genel durum (çağrı hacmi, çözüm oranı).
- İsimleri, işletmeleri ve sayıları verilerden AYNEN kullan; asla veri uydurma.
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

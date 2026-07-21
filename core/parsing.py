"""LLM JSON ciktisi ayristirma.

Not: native tool calling'de argumanlar zaten temiz JSON gelir; bu yardimci
yalnizca AGENT_ENABLED=0 geri donus yolundaki tek atimlik router icin kullanilir.
"""

import ast
import json
from typing import Any, Dict, Optional


def parse_llm_json(raw: str) -> Dict[str, Any]:
    """LLM ciktisindaki JSON'u ayristirir.

    Kod blogu isaretlerini temizler; model tek tirnak kullanmissa
    ast.literal_eval ile toparlar. Sozluk disinda bir sey donerse ValueError.
    """
    cleaned = str(raw).replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output is not a dict.")
    return parsed

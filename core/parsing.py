"""Ortak metin ayristirma yardimcilari: LLM JSON ciktisi ve Turkce tutar ifadeleri."""

import ast
import json
import re
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


def extract_try_amount(text: str, *, assume_bare_number_is_budget: bool = False) -> Optional[int]:
    """'8 milyon', '750 bin', '8500000 tl' gibi ifadelerden TL tutari cikarir.

    Ciplak sayilar ('50') dogal olarak belirsizdir: kat, kapi numarasi ya da adet
    olabilir. Bu yuzden yalnizca assume_bare_number_is_budget=True iken (ornegin
    akis motoru butce sorusunun cevabini islerken) milyon TL olarak yorumlanir.
    """
    lowered = text.lower().replace(".", "").replace(",", ".")

    million_match = re.search(r"(\d+(?:\.\d+)?)\s*milyon", lowered)
    if million_match:
        return int(float(million_match.group(1)) * 1_000_000)

    thousand_match = re.search(r"(\d+(?:\.\d+)?)\s*bin", lowered)
    if thousand_match:
        return int(float(thousand_match.group(1)) * 1_000)

    tl_match = re.search(r"(\d{5,})\s*tl", lowered)
    if tl_match:
        return int(tl_match.group(1))

    if assume_bare_number_is_budget:
        bare_number = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", lowered)
        if bare_number:
            numeric = float(bare_number.group(1))
            if numeric <= 100:
                return int(numeric * 1_000_000)
            return int(numeric)

    return None

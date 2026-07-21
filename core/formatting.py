"""Sesli okumaya uygun Turkce bicimlendirme yardimcilari.

Bunlar keyword/niyet cikarimi DEGIL, sunum katmanidir: tutarlar, IBAN'lar ve
tarihler TTS'te dogru duyulsun diye. Agentic donusumde korunurlar.

Eskiden AgentOrchestrator'in metotlariydi; arac handler'lari orchestrator'a
bagimli kalmasin diye modul seviyesine tasindi.
"""

from __future__ import annotations

import re
from typing import Any, Optional


def parse_amount_text(value: Any) -> Optional[float]:
    """"1.250", "1,250.50", "1250" gibi metinleri tutara cevirir.

    Kural: hem nokta hem virgul varsa SONUNCUSU ondaliktir; tek tur ayrac
    tam 3 hanelik grup(lar) ayiriyorsa binliktir ("1.250" -> 1250),
    1-2 hane ayiriyorsa ondaliktir ("500,5" -> 500.5).
    """
    if value is None or isinstance(value, bool):
        # bool, int'in alt sinifidir: True'nun 1.0 TL olmasini engelle.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace("TL", "").replace("₺", "")
    if not text:
        return None
    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")   # 1.250,50
        else:
            text = text.replace(",", "")                      # 1,250.50
    elif "." in text or "," in text:
        separator = "." if "." in text else ","
        head, *groups = text.split(separator)
        if groups and all(len(group) == 3 for group in groups):
            text = head + "".join(groups)                     # binlik: 1.250
        else:
            text = text.replace(",", ".")                     # ondalik: 500,5
    try:
        return float(text)
    except ValueError:
        return None


def format_try_amount(amount: Any) -> str:
    """Tutari dogal Turkce soyleyise cevirir: 1250 -> "bin 250 TL"."""
    parsed = parse_amount_text(amount)
    if parsed is None:
        return f"{amount} TL"

    negative = parsed < 0
    value = int(round(abs(parsed)))
    text = _spoken_amount(value)
    return f"eksi {text}" if negative else text


def _spoken_amount(value: int) -> str:
    if value >= 1_000_000:
        millions = value // 1_000_000
        remainder = value % 1_000_000
        thousands = remainder // 1_000
        units = remainder % 1_000
        parts = [f"{millions} milyon"]
        if thousands:
            parts.append(f"{thousands} bin")
        # Onceki surum bu son parcayi ATIYORDU: 1_000_500 -> "1 milyon TL"
        # (500 TL sessizce kayboluyordu).
        if units:
            parts.append(str(units))
        return " ".join(parts) + " TL"

    if value >= 1_000:
        thousands = value // 1_000
        remainder = value % 1_000
        # "1 bin 250" kulaga yanlis gelir; Turkcede "bin 250" denir.
        prefix = "bin" if thousands == 1 else f"{thousands} bin"
        return f"{prefix} {remainder} TL" if remainder else f"{prefix} TL"

    return f"{value} TL"


def time_of(iso_value: str) -> str:
    if iso_value and "T" in iso_value:
        return iso_value.split("T")[1][:5]
    return ""


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}"


def speakable_iban(masked_iban: str) -> str:
    """Maskeli IBAN'i sesli okumaya uygun hale getirir.

    "TR** **** **** **** **44 17" gibi metinler TTS'te felaket okunur;
    bunun yerine "sonu 44 17 ile biten IBAN" denir.
    """
    digits = re.sub(r"\D", "", masked_iban or "")
    if len(digits) < 2:
        return "kayıtlı IBAN"
    tail = digits[-4:] if len(digits) >= 4 else digits
    spaced = " ".join(tail[index:index + 2] for index in range(0, len(tail), 2))
    return f"sonu {spaced} ile biten IBAN"

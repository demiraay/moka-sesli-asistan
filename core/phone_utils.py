from __future__ import annotations

import re


def normalize_phone_number(raw_phone: str, default: str = "unknown_phone") -> str:
    cleaned = (raw_phone or "").strip()
    if not cleaned:
        return default

    cleaned = re.sub(r"^whatsapp:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@c\.us$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@(lid|s\.whatsapp\.net)$", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return default

    # If this is not a phone-like value, leave the caller's identifier untouched.
    if not re.fullmatch(r"[+\d\s().-]+", cleaned):
        return cleaned

    had_plus_prefix = cleaned.lstrip().startswith("+")
    digits_only = re.sub(r"\D", "", cleaned)
    if not digits_only:
        return default

    # Turkish-friendly normalization so demo and WhatsApp users land on the same ID.
    # Examples:
    # 05401112233   -> +905401112233
    # 5401112233    -> +905401112233
    # +90 540...    -> +90540...
    # 0540 / 540    -> +90540
    if digits_only.startswith("90"):
        return f"+{digits_only}"

    if digits_only.startswith("0"):
        return f"+90{digits_only[1:]}"

    if 3 <= len(digits_only) <= 10:
        return f"+90{digits_only}"

    if had_plus_prefix or len(digits_only) >= 11:
        return f"+{digits_only}"

    return digits_only

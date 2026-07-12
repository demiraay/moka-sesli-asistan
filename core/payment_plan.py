"""Odeme plani hesaplayicisi.

Turk konut projesi satisindaki vadeli odeme kalibi: pesinat + esit araliklarla
yerlestirilen ara (balon) odemeler + aylik taksitler. Istege bagli yillik vade
farki orani, kalan bakiyeye basit oranti ile eklenir.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

TR_TZ = timezone(timedelta(hours=3))

TURKISH_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def _month_label(base: datetime, offset_months: int) -> str:
    month_index = base.month - 1 + offset_months
    year = base.year + month_index // 12
    return f"{TURKISH_MONTHS[month_index % 12]} {year}"


def build_payment_plan(
    *,
    price: int,
    down_payment_pct: float,
    months: int,
    balloon_count: int = 0,
    balloon_amount: int = 0,
    annual_rate_pct: float = 0.0,
    start: Optional[datetime] = None,
) -> Dict[str, Any]:
    if price <= 0:
        raise ValueError("Fiyat pozitif olmali.")
    if not 0 <= down_payment_pct <= 100:
        raise ValueError("Pesinat yuzdesi 0-100 araliginda olmali.")
    if not 1 <= months <= 240:
        raise ValueError("Vade 1-240 ay araliginda olmali.")
    if balloon_count < 0 or balloon_amount < 0:
        raise ValueError("Ara odeme degerleri negatif olamaz.")
    if annual_rate_pct < 0 or annual_rate_pct > 200:
        raise ValueError("Vade farki orani 0-200 araliginda olmali.")

    start = start or datetime.now(TR_TZ)

    down_payment = round(price * down_payment_pct / 100)
    balloon_total = balloon_count * balloon_amount
    remainder = price - down_payment - balloon_total
    if remainder < 0:
        raise ValueError("Pesinat ve ara odemeler toplami fiyati asiyor.")

    interest = round(remainder * (annual_rate_pct / 100) * (months / 12)) if annual_rate_pct else 0
    financed = remainder + interest

    monthly = financed // months
    last_monthly = financed - monthly * (months - 1)

    # Ara odemeleri vade icine esit araliklarla yerlestir (ör. 3 ara odeme,
    # 24 ay vade -> 6., 12., 18. aylar).
    balloon_months = {
        round(k * months / (balloon_count + 1)) or 1
        for k in range(1, balloon_count + 1)
    } if balloon_count else set()

    schedule: List[Dict[str, Any]] = [{
        "label": "Peşinat",
        "month_label": _month_label(start, 0),
        "amount": down_payment,
    }]
    for month in range(1, months + 1):
        amount = last_monthly if month == months else monthly
        schedule.append({
            "label": f"{month}. taksit",
            "month_label": _month_label(start, month),
            "amount": amount,
        })
        if month in balloon_months:
            schedule.append({
                "label": "Ara ödeme",
                "month_label": _month_label(start, month),
                "amount": balloon_amount,
            })

    total = down_payment + balloon_total + financed

    return {
        "inputs": {
            "price": price,
            "down_payment_pct": down_payment_pct,
            "months": months,
            "balloon_count": balloon_count,
            "balloon_amount": balloon_amount,
            "annual_rate_pct": annual_rate_pct,
        },
        "down_payment": down_payment,
        "balloon_total": balloon_total,
        "interest": interest,
        "financed": financed,
        "monthly": monthly,
        "last_monthly": last_monthly,
        "total": total,
        "schedule": schedule,
    }

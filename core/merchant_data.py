"""Is verisi erisimi — GERIYE UYUMLULUK KABUGU.

Gercek uygulama core/repository.MerchantRepository icinde ve SQLite uzerinde
calisir. Bu modul yalnizca eski isimleri (MerchantDataManager, resolve_date_token,
describe_day) ayakta tutar; yeni kod dogrudan MerchantRepository kullanmali.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from core.config import Config
from core.repository import (  # noqa: F401  (yeniden ihrac)
    MerchantRepository,
    normalize_day,
    resolve_date_token,
    tr_lower,
)

_TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]

_TR_WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def describe_day(iso_value: str, today: Optional[date] = None) -> str:
    """ISO tarih/zaman damgasini Turkce etikete cevirir (bugün/dün/yarın/tarih)."""
    if not iso_value:
        return ""
    base = today or date.today()
    try:
        day = datetime.fromisoformat(iso_value).date()
    except ValueError:
        return iso_value
    delta = (day - base).days
    if delta == 0:
        return "bugün"
    if delta == -1:
        return "dün"
    if delta == 1:
        return "yarın"
    label = f"{day.day} {_TR_MONTHS[day.month - 1]}"
    if abs(delta) <= 6:
        label += f" {_TR_WEEKDAYS[day.weekday()]}"
    return label


class MerchantDataManager(MerchantRepository):
    """MerchantRepository'nin eski adi.

    Eski cagiranlar `manager.config` uzerinden veri listelerine eristigi icin
    Config referansi korunur (bkz. Config'in tembel veri ozellikleri).
    """

    def __init__(self, config: Optional[Config] = None, **kwargs: Any):
        self.config = config or Config()
        super().__init__(db_path=self.config.business_db_path, **kwargs)

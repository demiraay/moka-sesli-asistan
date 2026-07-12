"""Mock Moka backend data access layer.

Replaces the old real-estate InventoryManager. Same pattern: reads the JSON
data loaded by the Config singleton and answers tool-level queries with
in-memory joins.

Date freshness: the JSON files use relative day tokens ("D0", "D-1",
"D+1T10:00:00") so the demo data never goes stale. Tokens are resolved to
real ISO timestamps once at load time, relative to today.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config

_DATE_TOKEN_RE = re.compile(r"^D(0|[+-]?\d+)(?:T(\d{2}:\d{2}(?::\d{2})?))?$")

# Fields that may contain relative day tokens, per dataset.
_TOKEN_FIELDS = {
    "transactions": ["timestamp"],
    "settlements": ["batch_date", "payout_eta"],
    "pos_devices": ["last_seen_at"],
}

_TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]

_TR_WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def resolve_date_token(value: Any, today: Optional[date] = None) -> Any:
    """Converts a "D-1T16:40:00"-style token to an ISO timestamp string.

    Non-token values pass through unchanged.
    """
    if not isinstance(value, str):
        return value
    match = _DATE_TOKEN_RE.match(value.strip())
    if not match:
        return value
    base = today or date.today()
    day = base + timedelta(days=int(match.group(1)))
    time_part = match.group(2)
    if time_part:
        if len(time_part) == 5:
            time_part += ":00"
        return f"{day.isoformat()}T{time_part}"
    return day.isoformat()


def describe_day(iso_value: str, today: Optional[date] = None) -> str:
    """Turns an ISO date/timestamp into a human Turkish label (bugün/dün/yarın/tarih)."""
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


class MerchantDataManager:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._resolve_all_tokens()

    # ------------------------------------------------------------------ setup

    def _resolve_all_tokens(self) -> None:
        if getattr(self.config, "_date_tokens_resolved", False):
            return
        datasets = {
            "transactions": self.config.transactions,
            "settlements": self.config.settlements,
            "pos_devices": self.config.pos_devices,
        }
        for name, rows in datasets.items():
            for row in rows:
                for field in _TOKEN_FIELDS[name]:
                    if field in row:
                        row[field] = resolve_date_token(row[field])
        self.config._date_tokens_resolved = True

    # -------------------------------------------------------------- merchants

    def get_merchant(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        """Returns the merchant enriched with plan, devices and volume trend."""
        merchant = next(
            (m for m in self.config.merchants if m.get("merchant_id") == merchant_id),
            None,
        )
        if not merchant:
            return None
        enriched = dict(merchant)
        enriched["plan"] = self.get_plan(merchant.get("commission_plan_id"))
        enriched["devices"] = self.get_devices(merchant_id)
        enriched["volume_trend"] = self._volume_trend(merchant)
        return enriched

    def _volume_series(self, merchant: Dict[str, Any]) -> List[int]:
        return [entry.get("volume", 0) for entry in merchant.get("monthly_volume_try", [])]

    def _volume_trend(self, merchant: Dict[str, Any]) -> Dict[str, Any]:
        series = self._volume_series(merchant)
        if not series:
            return {"last_month": 0, "prev_3m_avg": 0, "change_pct": 0}
        last = series[-1]
        prev = series[-4:-1] or series[:-1] or [last]
        prev_avg = sum(prev) / len(prev)
        change_pct = 0.0
        if prev_avg:
            change_pct = round((last - prev_avg) / prev_avg * 100, 1)
        return {
            "last_month": last,
            "prev_3m_avg": round(prev_avg),
            "change_pct": change_pct,
        }

    def list_dormant_merchants(self) -> List[Dict[str, Any]]:
        """Merchants whose last-month volume dropped below 30% of the prior 3-month average."""
        dormant = []
        for merchant in self.config.merchants:
            series = self._volume_series(merchant)
            if len(series) < 4:
                continue
            last = series[-1]
            prev_avg = sum(series[-4:-1]) / 3
            if prev_avg <= 0 or last >= 0.3 * prev_avg:
                continue
            drop_pct = round((1 - last / prev_avg) * 100, 1)
            entry = self.get_merchant(merchant["merchant_id"]) or dict(merchant)
            entry["drop_pct"] = drop_pct
            entry["lost_volume_try"] = round(prev_avg - last)
            dormant.append(entry)
        dormant.sort(key=lambda m: m["lost_volume_try"], reverse=True)
        return dormant

    # ------------------------------------------------------------ settlements

    def list_settlements(self, merchant_id: str, limit: int = 5,
                         status: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = [s for s in self.config.settlements if s.get("merchant_id") == merchant_id]
        if status:
            rows = [s for s in rows if s.get("status") == status]
        rows.sort(key=lambda s: s.get("batch_date", ""), reverse=True)
        return rows[:limit]

    def get_latest_settlement(self, merchant_id: str,
                              status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = self.list_settlements(merchant_id, limit=1, status=status)
        return rows[0] if rows else None

    def get_settlements_for_period(self, merchant_id: str, period: str) -> List[Dict[str, Any]]:
        """period: latest | pending | last_week"""
        if period == "pending":
            rows = self.list_settlements(merchant_id, limit=10)
            return [s for s in rows if s.get("status") in ("planlandı", "beklemede")]
        if period == "last_week":
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            rows = self.list_settlements(merchant_id, limit=20)
            return [s for s in rows if s.get("batch_date", "") >= cutoff]
        latest = self.get_latest_settlement(merchant_id)
        return [latest] if latest else []

    # ----------------------------------------------------------- transactions

    def find_transactions(self, merchant_id: str,
                          amount_try: Optional[float] = None,
                          on_date: Optional[str] = None,
                          card_last4: Optional[str] = None,
                          status: Optional[str] = None,
                          limit: int = 5) -> List[Dict[str, Any]]:
        """Filters the merchant's transactions.

        amount_try is fuzzy (±1 TL, STT may mishear kuruş); on_date accepts
        "bugün"/"dün"/"D-2" tokens or ISO dates and matches the whole day.
        """
        rows = [t for t in self.config.transactions if t.get("merchant_id") == merchant_id]
        if amount_try is not None:
            rows = [t for t in rows if abs(t.get("amount_try", 0) - amount_try) <= 1.0]
        if on_date:
            day = self._normalize_day(on_date)
            if day:
                rows = [t for t in rows if t.get("timestamp", "").startswith(day)]
        if card_last4:
            rows = [t for t in rows if t.get("card_last4") == str(card_last4)]
        if status:
            rows = [t for t in rows if t.get("status") == status]
        rows.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
        return rows[:limit]

    def get_settlement_for_transaction(self, txn: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        batch_id = txn.get("settlement_batch_id")
        if not batch_id:
            return None
        return next(
            (s for s in self.config.settlements if s.get("batch_id") == batch_id),
            None,
        )

    @staticmethod
    def _normalize_day(value: str) -> Optional[str]:
        value = (value or "").strip().lower()
        today = date.today()
        if value in ("bugün", "bugun", "today", "d0"):
            return today.isoformat()
        if value in ("dün", "dun", "yesterday", "d-1"):
            return (today - timedelta(days=1)).isoformat()
        if value in ("evvelsi gün", "önceki gün", "d-2"):
            return (today - timedelta(days=2)).isoformat()
        token = resolve_date_token(value.upper())
        if token != value.upper():
            return token[:10]
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return None

    # ---------------------------------------------------------------- devices

    def get_devices(self, merchant_id: str) -> List[Dict[str, Any]]:
        return [d for d in self.config.pos_devices if d.get("merchant_id") == merchant_id]

    # --------------------------------------------------------------------- kb

    def match_kb(self, symptom_text: str) -> Optional[Dict[str, Any]]:
        """Keyword-overlap match over the support knowledge base."""
        text = (symptom_text or "").lower()
        if not text:
            return None
        best, best_score = None, 0
        for entry in self.config.support_kb:
            score = sum(1 for s in entry.get("symptoms", []) if s.lower() in text)
            if score > best_score:
                best, best_score = entry, score
        return best

    # ------------------------------------------------------------------ plans

    def get_plan(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not plan_id:
            return None
        return next(
            (p for p in self.config.commission_plans if p.get("plan_id") == plan_id),
            None,
        )

    def get_retention_plan(self) -> Optional[Dict[str, Any]]:
        return next(
            (p for p in self.config.commission_plans if p.get("retention_only")),
            None,
        )

    def get_upgrade_candidate(self, merchant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Best cheaper plan the merchant already qualifies for, with monthly saving."""
        current = self.get_plan(merchant.get("commission_plan_id"))
        if not current:
            return None
        series = self._volume_series(merchant)
        if len(series) < 2:
            return None
        avg_volume = sum(series[-2:]) / 2
        candidates = [
            p for p in self.config.commission_plans
            if not p.get("retention_only")
            and p.get("rate_pct", 99) < current.get("rate_pct", 0)
            and avg_volume >= p.get("min_monthly_volume_try", 0)
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda p: p.get("rate_pct", 99))
        saving = avg_volume * (current["rate_pct"] - best["rate_pct"]) / 100
        saving -= best.get("monthly_fee_try", 0) - current.get("monthly_fee_try", 0)
        if saving <= 0:
            return None
        return {"plan": best, "current_plan": current, "monthly_saving_try": round(saving)}

    # ---------------------------------------------------------- payment links

    def create_payment_link(self, merchant_id: str,
                            amount_try: Optional[float] = None,
                            description: Optional[str] = None) -> Dict[str, Any]:
        merchant = self.get_merchant(merchant_id) or {}
        name = merchant.get("business_name", "isletme")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()
                      .replace("ı", "i").replace("ş", "s").replace("ç", "c")
                      .replace("ö", "o").replace("ü", "u").replace("ğ", "g")).strip("-")
        slug = "-".join(slug.split("-")[:2]) or "isletme"
        return {
            "url": f"https://moka.link/{slug}-{uuid.uuid4().hex[:4]}",
            "merchant_id": merchant_id,
            "amount_try": amount_try,
            "description": description,
            "expires": (date.today() + timedelta(days=7)).isoformat(),
        }

    # -------------------------------------------------------------- summaries

    def monthly_summary(self, merchant_id: str, month: Optional[str] = None) -> Dict[str, Any]:
        """Gross volume / estimated commission / txn count for a month ("YYYY-MM")."""
        merchant = self.get_merchant(merchant_id)
        if not merchant:
            return {}
        target = month or date.today().strftime("%Y-%m")
        volume = next(
            (e.get("volume", 0) for e in merchant.get("monthly_volume_try", [])
             if e.get("month") == target),
            0,
        )
        plan = merchant.get("plan") or {}
        rate = plan.get("rate_pct", 0)
        txns = [
            t for t in self.config.transactions
            if t.get("merchant_id") == merchant_id
            and t.get("timestamp", "").startswith(target)
        ]
        return {
            "month": target,
            "gross_try": volume,
            "commission_try": round(volume * rate / 100),
            "rate_pct": rate,
            "plan_name": plan.get("name"),
            "txn_count": len(txns),
        }

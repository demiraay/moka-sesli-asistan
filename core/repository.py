"""Is verisi erisim katmani (SQLite).

MerchantDataManager'in yerini alir ve AYNI public API'yi sunar: donen sozlukler
eski JSON satirlariyla ayni alan adlarini tasir (ornegin islemlerde 'timestamp',
hakedislerde 'batch_date'). Bu bilincli bir uyumluluk karari — orchestrator,
panel ve test_merchant_data.py'nin 14 testi bu bicime bagli.

Tarih tazeleme: goreli token'lar (D-1T16:40:00) seed aninda cozulur, ham hali
*_token sutununda saklanir. ensure_fresh() gun degistiginde yeniden cozer, boylece
demo verisi bayatlamaz.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from core import db
from core.migrations.business import BUSINESS_MIGRATIONS

_WEEKDAY_INDEX = {
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
    "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}

_DATE_TOKEN_RE = re.compile(r"^D(0|[+-]?\d+)(?:T(\d{2}:\d{2}(?::\d{2})?))?$")


def tr_lower(text: str) -> str:
    """Turkce-farkindalikli kucultme.

    'İ'.lower() Python'da 'i' + birlesik nokta (U+0307) uretir ve substring
    eslesmesini bozar. SQLite'in LOWER()'i bunu hic yapamaz — bu yuzden
    kb_symptoms.symptom_normalized seed'de bu fonksiyonla uretilir.
    """
    return (text or "").replace("İ", "i").replace("I", "ı").lower().replace("̇", "")


def resolve_date_token(value: Any, today: Optional[date] = None) -> Any:
    """"D-1T16:40:00" gibi bir token'i ISO zaman damgasina cevirir."""
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


def normalize_day(value: str, today: Optional[date] = None) -> Optional[str]:
    """"bugun"/"dun"/"sali"/"D-2"/ISO -> ISO tarih. Dogal dil kalir, SQL'e inmez."""
    value = (value or "").strip().lower()
    base = today or date.today()
    if value in ("bugün", "bugun", "today", "d0"):
        return base.isoformat()
    if value in ("dün", "dun", "yesterday", "d-1"):
        return (base - timedelta(days=1)).isoformat()
    if value in ("evvelsi gün", "önceki gün", "d-2"):
        return (base - timedelta(days=2)).isoformat()

    weekday_key = value.replace(" günü", "").replace(" gunu", "").strip()
    if weekday_key in _WEEKDAY_INDEX:
        target = _WEEKDAY_INDEX[weekday_key]
        delta = 0 if base.weekday() == target else (base.weekday() - target) % 7 or 7
        return (base - timedelta(days=delta)).isoformat()

    token = resolve_date_token(value.upper(), today=base)
    if token != value.upper():
        return str(token)[:10]
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


class MerchantRepository:
    """Moka is verisine SQL erisimi."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None,
                 auto_refresh: bool = True):
        base_dir = Path(__file__).resolve().parent.parent
        self.db_path = Path(db_path) if db_path else base_dir / "data" / "moka.sqlite3"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Is verisi veritabani bulunamadi: {self.db_path}\n"
                f"Once seed calistirin:  python3 scripts/seed_demo_data.py"
            )
        db.migrate(self.db_path, BUSINESS_MIGRATIONS)
        if auto_refresh:
            self.ensure_fresh()

    # ------------------------------------------------------------ connection

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, params)]
        finally:
            connection.close()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # --------------------------------------------------------------- refresh

    def ensure_fresh(self) -> bool:
        """Gun degistiyse tum goreli tarihleri yeniden cozer.

        Demo verisinin bayatlamamasi icin: JSON'daki token'lar (D-1, D0T08:12)
        her zaman "bugune gore" anlamli olmali. Gunde en fazla bir kez calisir.
        """
        today = date.today().isoformat()
        if self.get_config("resolved_on") == today:
            return False

        with db.session(self.db_path) as connection:
            for table, token_column, value_column in (
                ("transactions", "ts_token", "ts"),
                ("settlements", "batch_date_token", "batch_date"),
                ("settlements", "payout_eta_token", "payout_eta"),
                ("pos_devices", "last_seen_token", "last_seen_at"),
                ("merchant_contacts", "contacted_token", "contacted_at"),
            ):
                rows = connection.execute(
                    f"SELECT rowid AS rid, {token_column} AS token FROM {table} "
                    f"WHERE {token_column} != ''"
                ).fetchall()
                connection.executemany(
                    f"UPDATE {table} SET {value_column} = ? WHERE rowid = ?",
                    [(resolve_date_token(row["token"]), row["rid"]) for row in rows],
                )
            connection.execute("UPDATE transactions SET ts_day = substr(ts, 1, 10)")

            # Aylik ciro serisi: son eleman HER ZAMAN icinde bulunulan ay olmali.
            base = date.today()
            updates = []
            for row in connection.execute(
                "SELECT merchant_id, month_offset FROM merchant_monthly_volume"
            ).fetchall():
                offset = row["month_offset"]           # 0 = bu ay, -1 = onceki...
                year, month = base.year, base.month + offset
                while month <= 0:
                    month += 12
                    year -= 1
                while month > 12:
                    month -= 12
                    year += 1
                updates.append((f"{year:04d}-{month:02d}", row["merchant_id"], offset))
            connection.executemany(
                "UPDATE merchant_monthly_volume SET month = ? "
                "WHERE merchant_id = ? AND month_offset = ?",
                updates,
            )
            connection.execute(
                "INSERT INTO app_config (key, value_json) VALUES ('resolved_on', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (json.dumps(today),),
            )
        return True

    # ---------------------------------------------------------------- config

    def get_config(self, key: str, default: Any = None) -> Any:
        row = self._query_one("SELECT value_json FROM app_config WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except (ValueError, TypeError):
            return default

    def set_config(self, key: str, value: Any) -> None:
        with db.session(self.db_path) as connection:
            connection.execute(
                "INSERT INTO app_config (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ------------------------------------------------------------- merchants

    def _volume_rows(self, merchant_id: str) -> List[Dict[str, Any]]:
        return [
            {"month": row["month"], "volume": row["volume_try"]}
            for row in self._query(
                "SELECT month, volume_try FROM merchant_monthly_volume "
                "WHERE merchant_id = ? ORDER BY month_offset ASC",
                (merchant_id,),
            )
        ]

    def _merchant_base(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """SQL satirini eski JSON bicimine cevirir (alan adlari korunur)."""
        merchant = dict(row)
        merchant.pop("phone_e164", None)
        merchant["products"] = [
            item["product_key"] for item in self._query(
                "SELECT product_key FROM merchant_products WHERE merchant_id = ? "
                "ORDER BY sort_order, product_key",
                (row["merchant_id"],),
            )
        ]
        merchant["monthly_volume_try"] = self._volume_rows(row["merchant_id"])
        return merchant

    def get_merchant(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        """Isletme + plan + cihazlar + ciro trendi."""
        row = self._query_one(
            "SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,))
        if not row:
            return None
        merchant = self._merchant_base(row)
        merchant["plan"] = self.get_plan(row["commission_plan_id"])
        merchant["devices"] = self.get_devices(merchant_id)
        merchant["volume_trend"] = self._volume_trend(merchant)
        return merchant

    def list_merchants(self) -> List[Dict[str, Any]]:
        return [self._merchant_base(row) for row in
                self._query("SELECT * FROM merchants ORDER BY merchant_id")]

    # --- toplu listeleme (Config uyumluluk ozellikleri icin) ---------------

    def find_transactions_all(self) -> List[Dict[str, Any]]:
        return [self._transaction_row(row) for row in
                self._query("SELECT * FROM transactions ORDER BY ts DESC, txn_id DESC")]

    def list_all_settlements(self) -> List[Dict[str, Any]]:
        return [self._settlement_row(row) for row in
                self._query("SELECT * FROM settlements ORDER BY batch_date DESC, batch_id DESC")]

    def list_all_devices(self) -> List[Dict[str, Any]]:
        return [self._device_row(row) for row in
                self._query("SELECT * FROM pos_devices ORDER BY terminal_id")]

    def list_merchant_options(self) -> List[Dict[str, Any]]:
        """Acilir liste icin hafif isletme listesi (tek sorgu, alt sorgu yok)."""
        return self._query(
            "SELECT merchant_id, business_name, owner_name FROM merchants "
            "ORDER BY business_name")

    def list_plans(self) -> List[Dict[str, Any]]:
        plans = []
        for row in self._query("SELECT * FROM commission_plans ORDER BY plan_id"):
            plan = dict(row)
            plan["retention_only"] = bool(plan.get("retention_only"))
            plans.append(plan)
        return plans

    def list_kb_articles(self) -> List[Dict[str, Any]]:
        return [article for article in (
            self._kb_article(row["issue_id"]) for row in
            self._query("SELECT issue_id FROM kb_articles ORDER BY sort_order, issue_id")
        ) if article]

    def find_merchant_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Telefondan isletme bulur.

        Once normalize edilmis indeksli esitlik denenir; bulunamazsa son 10 hane
        ile geri duser (kayitli numara formati farkli olabilir).
        """
        from core.phone_utils import normalize_phone_number

        normalized = normalize_phone_number(phone)
        row = self._query_one(
            "SELECT * FROM merchants WHERE phone_e164 = ?", (normalized,))
        if row:
            return self.get_merchant(row["merchant_id"])

        digits = re.sub(r"\D", "", phone or "")[-10:]
        if len(digits) < 10:
            return None
        row = self._query_one(
            "SELECT * FROM merchants WHERE substr(replace(replace(phone,'+',''),' ',''), -10) = ?",
            (digits,))
        return self.get_merchant(row["merchant_id"]) if row else None

    def resolve_identity(self, identity: str) -> Optional[str]:
        """Arayan kimliginden merchant_id (kalici eslesme)."""
        row = self._query_one(
            "SELECT merchant_id FROM identities WHERE identity = ?", (identity,))
        return row["merchant_id"] if row and row["merchant_id"] else None

    def link_identity(self, identity: str, merchant_id: str, kind: str = "phone") -> None:
        with db.session(self.db_path) as connection:
            connection.execute(
                "INSERT INTO identities (identity, kind, merchant_id, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(identity) DO UPDATE SET merchant_id = excluded.merchant_id",
                (identity, kind, merchant_id, datetime.now().isoformat()),
            )

    def _volume_series(self, merchant: Dict[str, Any]) -> List[int]:
        return [entry.get("volume", 0) for entry in merchant.get("monthly_volume_try", [])]

    def _volume_trend(self, merchant: Dict[str, Any]) -> Dict[str, Any]:
        series = self._volume_series(merchant)
        if not series:
            return {"last_month": 0, "prev_3m_avg": 0, "change_pct": 0}
        last = series[-1]
        previous = series[-4:-1] or series[:-1] or [last]
        previous_avg = sum(previous) / len(previous)
        change_pct = round((last - previous_avg) / previous_avg * 100, 1) if previous_avg else 0.0
        return {
            "last_month": last,
            "prev_3m_avg": round(previous_avg),
            "change_pct": change_pct,
        }

    def list_dormant_merchants(self) -> List[Dict[str, Any]]:
        """Son ay cirosu onceki 3 ayin ortalamasinin %30'unun altina dusenler."""
        dormant: List[Dict[str, Any]] = []
        for row in self._query("SELECT merchant_id FROM merchants ORDER BY merchant_id"):
            merchant_id = row["merchant_id"]
            series = [item["volume"] for item in self._volume_rows(merchant_id)]
            if len(series) < 4:
                continue
            last = series[-1]
            previous_avg = sum(series[-4:-1]) / 3
            if previous_avg <= 0 or last >= 0.3 * previous_avg:
                continue
            entry = self.get_merchant(merchant_id)
            if not entry:
                continue
            entry["drop_pct"] = round((1 - last / previous_avg) * 100, 1)
            entry["lost_volume_try"] = round(previous_avg - last)
            dormant.append(entry)
        dormant.sort(key=lambda item: item["lost_volume_try"], reverse=True)
        return dormant

    # -------------------------------------------------- CRM / musteri-360

    def risk_profile(self, merchant: Dict[str, Any]) -> Dict[str, Any]:
        """Isletmenin risk skoru (0-100), kademe, yasam-dongusu segmenti + gerekce.

        SAKLANMAZ — merchant_monthly_volume'den anlik hesaplanir; saklansaydi
        ensure_fresh() ay etiketlerini kaydirdikca bayatlardi. Dormant kurali
        list_dormant_merchants ile birebir ayni tabana dayanir (son ay < onceki
        3 ayin %30'u).
        """
        trend = self._volume_trend(merchant)
        series = self._volume_series(merchant)
        last = series[-1] if series else 0
        change_pct = trend["change_pct"]

        if len(series) >= 4:
            prev_avg = sum(series[-4:-1]) / 3
        elif len(series) > 1:
            prev_avg = sum(series[:-1]) / len(series[:-1])
        else:
            prev_avg = last
        is_dormant = len(series) >= 4 and prev_avg > 0 and last < 0.3 * prev_avg

        reasons: List[str] = []
        score = 20  # taban: dusuk risk

        if last <= 0:
            score = 95
            reasons.append("Bu ay hic islem yok")
        elif is_dormant:
            score = 85
            reasons.append(f"Ciro %{round((1 - last / prev_avg) * 100)} dustu")
        elif change_pct <= -25:
            score = 65
            reasons.append(f"Ciro geriliyor (%{change_pct})")
        elif change_pct <= -10:
            score = 45
            reasons.append("Ciroda hafif dusus")
        elif change_pct >= 15:
            score = 15
            reasons.append(f"Ciro buyuyor (%{change_pct})")

        plan = merchant.get("plan") or {}
        if plan.get("retention_only"):
            score = min(100, score + 10)
            reasons.append("Tutundurma planinda")

        status = (merchant.get("status") or "").lower()
        if status in ("askıda", "askida", "pasif"):
            score = max(score, 80)
            reasons.append(f"Hesap durumu: {merchant.get('status')}")

        score = max(0, min(100, score))
        if score >= 80:
            tier = "kritik"
        elif score >= 55:
            tier = "yüksek"
        elif score >= 35:
            tier = "orta"
        else:
            tier = "düşük"

        if is_dormant or last <= 0:
            segment = "uyuyan"
        elif change_pct >= 15:
            segment = "büyüyor"
        elif change_pct <= -15:
            segment = "daralıyor"
        else:
            segment = "stabil"

        return {
            "risk_score": score,
            "risk_tier": tier,
            "segment": segment,
            "reasons": reasons,
        }

    def _last_contact_map(self) -> Dict[str, str]:
        return {
            row["merchant_id"]: row["last_contact"]
            for row in self._query(
                "SELECT merchant_id, MAX(contacted_at) AS last_contact "
                "FROM merchant_contacts GROUP BY merchant_id")
        }

    def list_customers(self) -> List[Dict[str, Any]]:
        """Isletme-360 listesi + rapor icin portfoy satirlari. M-TEST HARIC.

        Tek gecis: her merchant icin plan adi, son ay hacmi/degisimi, risk
        profili ve son temas tarihi. 18 satir icin Python'da risk hesabi ucuz.
        """
        from core.demo_profile import TEST_MERCHANT_ID

        last_contacts = self._last_contact_map()
        customers: List[Dict[str, Any]] = []
        for row in self._query(
                "SELECT * FROM merchants WHERE merchant_id != ? ORDER BY merchant_id",
                (TEST_MERCHANT_ID,)):
            merchant = self._merchant_base(row)
            merchant["plan"] = self.get_plan(row["commission_plan_id"])
            trend = self._volume_trend(merchant)
            risk = self.risk_profile(merchant)
            customers.append({
                "merchant_id": row["merchant_id"],
                "business_name": row["business_name"],
                "owner_name": row["owner_name"],
                "sector": row["sector"],
                "city": row["city"],
                "status": row["status"],
                "account_manager": row["account_manager"],
                "preferred_channel": row["preferred_channel"],
                "tier": row["tier"],
                "plan_id": row["commission_plan_id"],
                "plan_name": (merchant["plan"] or {}).get("name", ""),
                "last_month_try": trend["last_month"],
                "prev_3m_avg_try": trend["prev_3m_avg"],
                "change_pct": trend["change_pct"],
                "volume_series": self._volume_series(merchant),
                "risk_score": risk["risk_score"],
                "risk_tier": risk["risk_tier"],
                "segment": risk["segment"],
                "risk_reasons": risk["reasons"],
                "last_contact_at": last_contacts.get(row["merchant_id"], ""),
            })
        return customers

    def portfolio_summary(self, months: int = 6) -> Dict[str, Any]:
        """Portfoy-geneli agregat (eksik olan tek-sorguda ozet). M-TEST HARIC.

        months: rapor donemi (3 veya 6 ay). Grafikler son `months` aya kirpilir
        ve bir onceki ayni uzunluktaki donemle karsilastirilir (period_change_pct).
        """
        from collections import Counter
        from core.demo_profile import TEST_MERCHANT_ID

        months = 3 if int(months) <= 3 else 6
        customers = self.list_customers()

        # Aylik ciro + tahmini komisyon (month_offset bazinda, kronolojik)
        monthly = self._query(
            "SELECT mmv.month_offset AS mo, MAX(mmv.month) AS ym, "
            "       SUM(mmv.volume_try) AS vol, "
            "       SUM(mmv.volume_try * cp.rate_pct / 100.0) AS comm "
            "  FROM merchant_monthly_volume mmv "
            "  JOIN merchants m ON m.merchant_id = mmv.merchant_id "
            "  JOIN commission_plans cp ON cp.plan_id = m.commission_plan_id "
            " WHERE mmv.merchant_id != ? "
            " GROUP BY mmv.month_offset ORDER BY mmv.month_offset ASC",
            (TEST_MERCHANT_ID,))
        monthly_all = [
            {"month": r["ym"], "volume_try": round(r["vol"] or 0),
             "commission_try": round(r["comm"] or 0)}
            for r in monthly
        ]
        # Secilen doneme kirp + onceki ayni uzunluktaki donemle karsilastir.
        monthly_totals = monthly_all[-months:]
        this_period = sum(m["volume_try"] for m in monthly_totals)
        prev_slice = monthly_all[-2 * months:-months]
        prev_period = sum(m["volume_try"] for m in prev_slice)
        period_change_pct = (round((this_period - prev_period) / prev_period * 100, 1)
                             if prev_period else 0.0)

        # Aylik islem adedi (cozulmus ts uzerinden), ayni doneme kirpilmis
        txn_rows = self._query(
            "SELECT substr(ts, 1, 7) AS ym, COUNT(*) AS c FROM transactions "
            "WHERE merchant_id != ? AND ts != '' GROUP BY ym ORDER BY ym ASC",
            (TEST_MERCHANT_ID,))
        txn_volume_by_month = [{"month": r["ym"], "count": r["c"]} for r in txn_rows][-months:]

        def _counter(field: str) -> Dict[str, int]:
            return dict(Counter(c[field] for c in customers if c.get(field)))

        top_growing = sorted(
            (c for c in customers if c["change_pct"] > 0),
            key=lambda c: c["change_pct"], reverse=True)[:5]
        top_dormant = sorted(
            (c for c in customers if c["segment"] in ("uyuyan", "daralıyor")),
            key=lambda c: c["risk_score"], reverse=True)[:5]

        return {
            "merchant_count": len(customers),
            "period_months": months,
            "period_change_pct": period_change_pct,
            "total_last_month_try": sum(c["last_month_try"] for c in customers),
            "total_commission_try": (monthly_totals[-1]["commission_try"]
                                     if monthly_totals else 0),
            "monthly_totals": monthly_totals,
            "commission_by_month": [
                {"month": m["month"], "commission_try": m["commission_try"]}
                for m in monthly_totals],
            "txn_volume_by_month": txn_volume_by_month,
            "count_by_tier": _counter("tier"),
            "count_by_risk_tier": _counter("risk_tier"),
            "count_by_segment": _counter("segment"),
            "plan_distribution": _counter("plan_name"),
            "top_growing": top_growing,
            "top_dormant": top_dormant,
        }

    def list_followup_merchants(self) -> List[Dict[str, Any]]:
        """Otomatik takip gorevleri: uyuyan musteriler + acik/takip durumlu son
        konusmalar. Panel gorev listesine (admin handoff'lariyla birlikte) girer.
        """
        tasks: List[Dict[str, Any]] = []

        # 1) Uyuyan isletmeler — kurtarma aramasi
        for merchant in self.list_dormant_merchants():
            manager = merchant.get("account_manager") or "atanmamış"
            tasks.append({
                "kind": "dormant", "priority": "high",
                "merchant_id": merchant["merchant_id"],
                "title": f"{merchant['business_name']} — uyuyan, kurtarma araması",
                "detail": (f"Aylık ~{merchant.get('lost_volume_try', 0)} TL hacim kaybı"
                           f" · temsilci: {manager}"),
            })

        # 2) Cozulmemis / takip bekleyen son konusmalar (D1 outcome alani)
        seen = {task["merchant_id"] for task in tasks}
        for row in self._query(
                "SELECT mc.merchant_id, mc.subject, mc.outcome, "
                "       m.business_name, m.account_manager "
                "  FROM merchant_contacts mc "
                "  JOIN merchants m ON m.merchant_id = mc.merchant_id "
                " WHERE mc.outcome IN ('açık', 'takip') AND mc.source = 'ai' "
                " ORDER BY mc.contacted_at DESC"):
            if row["merchant_id"] in seen:
                continue
            seen.add(row["merchant_id"])
            manager = row.get("account_manager") or "atanmamış"
            tasks.append({
                "kind": "open", "priority": "high" if row["outcome"] == "açık" else "medium",
                "merchant_id": row["merchant_id"],
                "title": (f"{row['business_name']} — {row['outcome']} konu takibi: "
                          f"{row['subject']}"),
                "detail": f"Konuşma {row['outcome']} kaldı · temsilci: {manager}",
            })
        return tasks

    def get_customer_360(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        """Tek isletmenin is-tarafi 360 gorunumu (yalniz moka.sqlite3).

        Ops tarafi (konusma/gorev/lead) admin_store'dan route'ta birlestirilir;
        burasi saf kalir. Mevcut metodlari yeniden kullanir.
        """
        merchant = self.get_merchant(merchant_id)
        if not merchant:
            return None
        return {
            "merchant": merchant,
            "plan": merchant.get("plan"),
            "devices": merchant.get("devices", []),
            "volume_trend": merchant.get("volume_trend"),
            "risk": self.risk_profile(merchant),
            "settlements": self.list_settlements(merchant_id, limit=12),
            "transactions": self.find_transactions(merchant_id, limit=20),
            "upgrade": self.get_upgrade_candidate(merchant),
            "monthly": self.monthly_summary(merchant_id),
            "contacts": self.list_contacts(merchant_id),
            "insights": self.list_insights(merchant_id, limit=8),
        }

    # ----------------------------------------------------- temas gecmisi

    @staticmethod
    def _contact_row(row: Dict[str, Any]) -> Dict[str, Any]:
        contact = dict(row)
        contact.pop("contacted_token", None)
        return contact

    def list_contacts(self, merchant_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return [self._contact_row(row) for row in self._query(
            "SELECT * FROM merchant_contacts WHERE merchant_id = ? "
            "ORDER BY contacted_at DESC, id DESC LIMIT ?",
            (merchant_id, limit))]

    def add_contact(self, merchant_id: str, channel: str = "", note: str = "",
                    rep: str = "", direction: str = "outbound", subject: str = "",
                    when_token: Optional[str] = None) -> None:
        """Temas kaydi ekler. Tarih cift-kolon token deseninde yazilir."""
        token = when_token or f"D0T{datetime.now().strftime('%H:%M:%S')}"
        resolved = resolve_date_token(token)
        with db.session(self.db_path) as connection:
            connection.execute(
                "INSERT INTO merchant_contacts "
                "(merchant_id, channel, direction, subject, note, rep, "
                " contacted_token, contacted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (merchant_id, channel, direction, subject, note, rep, token, resolved))

    def list_identities_for_merchant(self, merchant_id: str) -> List[str]:
        """Bu isletmeye bagli arayan kimlikleri (cross-DB ops koprusu icin)."""
        return [row["identity"] for row in self._query(
            "SELECT identity FROM identities WHERE merchant_id = ? ORDER BY identity",
            (merchant_id,))]

    # -------------------------------------- konusma -> CRM (kapali dongu)

    def upsert_session_contact(self, merchant_id: str, session_id: str, *,
                               channel: str = "", subject: str = "", note: str = "",
                               rep: str = "AI", outcome: str = "", sentiment: str = "",
                               when_token: Optional[str] = None) -> None:
        """Bir OTURUMUN temas kaydini olusturur/gunceller (source='ai').

        Ayni session_id ile tekrar cagrilinca YENI satir acmaz, mevcut kaydi
        gunceller — boylece bir konusma = tek temas kaydi, icerigi konusma
        ilerledikce zenginlesir. outcome (çözüm durumu) ve sentiment (ruh hali)
        konusma ilerledikce guncellenir; 360/rapor "nasil sonuclandi"yi gosterir.
        """
        if not session_id:
            return
        token = when_token or f"D0T{datetime.now().strftime('%H:%M:%S')}"
        resolved = resolve_date_token(token)
        with db.session(self.db_path) as connection:
            existing = connection.execute(
                "SELECT id FROM merchant_contacts "
                "WHERE session_id = ? AND source = 'ai' LIMIT 1",
                (session_id,)).fetchone()
            if existing:
                connection.execute(
                    "UPDATE merchant_contacts SET channel = ?, subject = ?, note = ?, "
                    "outcome = ?, sentiment = ?, contacted_token = ?, contacted_at = ? "
                    "WHERE id = ?",
                    (channel, subject, note, outcome, sentiment, token, resolved,
                     existing["id"]))
            else:
                connection.execute(
                    "INSERT INTO merchant_contacts (merchant_id, channel, direction, "
                    "subject, note, rep, outcome, sentiment, contacted_token, "
                    "contacted_at, session_id, source) "
                    "VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?, ?, ?, 'ai')",
                    (merchant_id, channel, subject, note, rep, outcome, sentiment,
                     token, resolved, session_id))

    def update_preferred_channel(self, merchant_id: str, channel: str) -> None:
        """Musteri iletisim tercihi degisikligini kalici yazar (CRM guncel kalir)."""
        allowed = {"telefon", "whatsapp", "email", "sms"}
        if channel not in allowed:
            return
        with db.session(self.db_path) as connection:
            connection.execute(
                "UPDATE merchants SET preferred_channel = ? WHERE merchant_id = ?",
                (channel, merchant_id))

    def add_insight(self, merchant_id: str, category: str, note: str, *,
                    session_id: str = "", channel: str = "", rep: str = "AI",
                    when_token: Optional[str] = None) -> None:
        """Agent'in bilincli cikardigi KALICI CRM icgorusu (source='insight').

        Otomatik gunlukten farkli: her icgoru AYRI kayittir (bir konusmada
        birden fazla olabilir) ve 360'ta vurgulanir, sonraki konusmada agent'a
        geri beslenir."""
        token = when_token or f"D0T{datetime.now().strftime('%H:%M:%S')}"
        resolved = resolve_date_token(token)
        with db.session(self.db_path) as connection:
            connection.execute(
                "INSERT INTO merchant_contacts (merchant_id, channel, direction, "
                "subject, note, rep, contacted_token, contacted_at, session_id, source) "
                "VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?, 'insight')",
                (merchant_id, channel, category, note, rep, token, resolved, session_id))

    def upsert_session_insight(self, merchant_id: str, session_id: str, category: str,
                               note: str, *, channel: str = "", rep: str = "AI",
                               when_token: Optional[str] = None) -> None:
        """Oturum + kategori bazli tek icgoru (otomatik firsat/durum notu icin).

        add_insight'tan farki: ayni (session, kategori) tekrar yazilinca YENI
        satir acmaz, gunceller — konusma boyunca firsat notu bir kez birikir,
        her turda cogalmaz."""
        if not session_id:
            self.add_insight(merchant_id, category, note, channel=channel, rep=rep,
                             when_token=when_token)
            return
        token = when_token or f"D0T{datetime.now().strftime('%H:%M:%S')}"
        resolved = resolve_date_token(token)
        with db.session(self.db_path) as connection:
            existing = connection.execute(
                "SELECT id FROM merchant_contacts WHERE session_id = ? "
                "AND source = 'insight' AND subject = ? LIMIT 1",
                (session_id, category)).fetchone()
            if existing:
                connection.execute(
                    "UPDATE merchant_contacts SET note = ?, channel = ?, "
                    "contacted_token = ?, contacted_at = ? WHERE id = ?",
                    (note, channel, token, resolved, existing["id"]))
            else:
                connection.execute(
                    "INSERT INTO merchant_contacts (merchant_id, channel, direction, "
                    "subject, note, rep, contacted_token, contacted_at, session_id, source) "
                    "VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?, 'insight')",
                    (merchant_id, channel, category, note, rep, token, resolved, session_id))

    def list_insights(self, merchant_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Agent'in kaydettigi CRM icgoruleri (en yeni once)."""
        return [self._contact_row(row) for row in self._query(
            "SELECT * FROM merchant_contacts WHERE merchant_id = ? AND source = 'insight' "
            "ORDER BY contacted_at DESC, id DESC LIMIT ?",
            (merchant_id, limit))]

    # ----------------------------------------------------------- settlements

    @staticmethod
    def _settlement_row(row: Dict[str, Any]) -> Dict[str, Any]:
        settlement = dict(row)
        settlement.pop("batch_date_token", None)
        settlement.pop("payout_eta_token", None)
        return settlement

    def list_settlements(self, merchant_id: str, limit: int = 5,
                         status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM settlements WHERE merchant_id = ?"
        params: List[Any] = [merchant_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY batch_date DESC, batch_id DESC LIMIT ?"
        params.append(limit)
        return [self._settlement_row(row) for row in self._query(sql, tuple(params))]

    def get_latest_settlement(self, merchant_id: str,
                              status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = self.list_settlements(merchant_id, limit=1, status=status)
        return rows[0] if rows else None

    def get_settlements_for_period(self, merchant_id: str, period: str) -> List[Dict[str, Any]]:
        """period: latest | pending | last_week

        Not: eski surumde once limit uygulanip SONRA filtreleniyordu; SQL'de
        WHERE once kosuyor, yani "bekleyen" kayitlar artik limit yuzunden
        gozden kacmiyor.
        """
        if period == "pending":
            return [self._settlement_row(row) for row in self._query(
                "SELECT * FROM settlements WHERE merchant_id = ? "
                "AND status IN ('planlandı', 'beklemede') "
                "ORDER BY batch_date DESC, batch_id DESC LIMIT 10",
                (merchant_id,))]
        if period == "last_week":
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            return [self._settlement_row(row) for row in self._query(
                "SELECT * FROM settlements WHERE merchant_id = ? AND batch_date >= ? "
                "ORDER BY batch_date DESC, batch_id DESC LIMIT 20",
                (merchant_id, cutoff))]
        latest = self.get_latest_settlement(merchant_id)
        return [latest] if latest else []

    # ---------------------------------------------------------- transactions

    @staticmethod
    def _transaction_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """DB satirini eski JSON bicimine cevirir: ts -> timestamp."""
        txn = dict(row)
        txn["timestamp"] = txn.pop("ts", "")
        txn.pop("ts_token", None)
        txn.pop("ts_day", None)
        return txn

    def find_transactions(self, merchant_id: str,
                          amount_try: Optional[float] = None,
                          on_date: Optional[str] = None,
                          card_last4: Optional[str] = None,
                          status: Optional[str] = None,
                          limit: int = 5) -> List[Dict[str, Any]]:
        """Isletmenin islemlerini filtreler.

        amount_try bulanik esleser (±1 TL — STT kurusu yanlis duyabilir);
        on_date "bugun"/"dun"/"D-2" token'lari veya ISO tarih kabul eder.
        """
        sql = "SELECT * FROM transactions WHERE merchant_id = ?"
        params: List[Any] = [merchant_id]
        if amount_try is not None:
            sql += " AND ABS(amount_try - ?) <= 1.0"
            params.append(float(amount_try))
        if on_date:
            day = normalize_day(on_date)
            if day:
                sql += " AND ts_day = ?"
                params.append(day)
        if card_last4:
            sql += " AND card_last4 = ?"
            params.append(str(card_last4))
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY ts DESC, txn_id DESC LIMIT ?"
        params.append(limit)
        return [self._transaction_row(row) for row in self._query(sql, tuple(params))]

    def get_settlement_for_transaction(self, txn: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        batch_id = txn.get("settlement_batch_id")
        if not batch_id:
            return None
        row = self._query_one("SELECT * FROM settlements WHERE batch_id = ?", (batch_id,))
        return self._settlement_row(row) if row else None

    # --------------------------------------------------------------- devices

    @staticmethod
    def _device_row(row: Dict[str, Any]) -> Dict[str, Any]:
        device = dict(row)
        device.pop("last_seen_token", None)
        return device

    def get_devices(self, merchant_id: str) -> List[Dict[str, Any]]:
        return [self._device_row(row) for row in self._query(
            "SELECT * FROM pos_devices WHERE merchant_id = ? ORDER BY terminal_id",
            (merchant_id,))]

    # -------------------------------------------------------------------- kb

    def _kb_article(self, issue_id: str) -> Optional[Dict[str, Any]]:
        row = self._query_one(
            "SELECT * FROM kb_articles WHERE issue_id = ?", (issue_id,))
        if not row:
            return None
        article = dict(row)
        article.pop("sort_order", None)
        article["escalate_if_unresolved"] = bool(article.get("escalate_if_unresolved"))
        article["symptoms"] = [item["symptom"] for item in self._query(
            "SELECT symptom FROM kb_symptoms WHERE issue_id = ? ORDER BY id", (issue_id,))]
        article["steps"] = [item["step_text"] for item in self._query(
            "SELECT step_text FROM kb_steps WHERE issue_id = ? ORDER BY step_no", (issue_id,))]
        return article

    def match_kb(self, symptom_text: str) -> Optional[Dict[str, Any]]:
        """Bilgi tabaninda semptom ortusmesi ile en iyi kaydi bulur.

        Yon onemli: aranan sey semptomun METIN ICINDE gecmesi (text LIKE %symptom%
        degil, symptom IN text). SQL karsiligi instr(?, symptom_normalized) > 0.
        """
        text = tr_lower(symptom_text)
        if not text:
            return None
        row = self._query_one(
            """
            SELECT s.issue_id, COUNT(*) AS score
              FROM kb_symptoms AS s
              JOIN kb_articles AS a ON a.issue_id = s.issue_id
             WHERE instr(?, s.symptom_normalized) > 0
             GROUP BY s.issue_id
             ORDER BY score DESC, MIN(a.sort_order) ASC
             LIMIT 1
            """,
            (text,),
        )
        return self._kb_article(row["issue_id"]) if row else None

    # ------------------------------------------------------------------ plans

    def get_plan(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not plan_id:
            return None
        row = self._query_one(
            "SELECT * FROM commission_plans WHERE plan_id = ?", (plan_id,))
        if not row:
            return None
        plan = dict(row)
        plan["retention_only"] = bool(plan.get("retention_only"))
        return plan

    def get_retention_plan(self) -> Optional[Dict[str, Any]]:
        row = self._query_one(
            "SELECT * FROM commission_plans WHERE retention_only = 1 ORDER BY plan_id LIMIT 1")
        if not row:
            return None
        plan = dict(row)
        plan["retention_only"] = True
        return plan

    def get_upgrade_candidate(self, merchant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Isletmenin hak ettigi en uygun daha ucuz plan + aylik tasarruf."""
        current = self.get_plan(merchant.get("commission_plan_id"))
        if not current:
            return None
        series = self._volume_series(merchant)
        if len(series) < 2:
            return None
        avg_volume = sum(series[-2:]) / 2

        best = self._query_one(
            """
            SELECT * FROM commission_plans
             WHERE retention_only = 0
               AND rate_pct < ?
               AND min_monthly_volume_try <= ?
             ORDER BY rate_pct ASC, plan_id ASC
             LIMIT 1
            """,
            (current.get("rate_pct", 0), avg_volume),
        )
        if not best:
            return None
        best = dict(best)
        best["retention_only"] = bool(best.get("retention_only"))

        saving = avg_volume * (current["rate_pct"] - best["rate_pct"]) / 100
        saving -= best.get("monthly_fee_try", 0) - current.get("monthly_fee_try", 0)
        if saving <= 0:
            return None
        return {"plan": best, "current_plan": current, "monthly_saving_try": round(saving)}

    # ------------------------------------------------------- payment links

    def create_payment_link(self, merchant_id: str,
                            amount_try: Optional[float] = None,
                            description: Optional[str] = None) -> Dict[str, Any]:
        """Odeme linki uretir VE kaydeder (eskiden kayit yoktu, link ucuyordu)."""
        row = self._query_one(
            "SELECT business_name FROM merchants WHERE merchant_id = ?", (merchant_id,))
        name = (row or {}).get("business_name", "isletme")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()
                      .replace("ı", "i").replace("ş", "s").replace("ç", "c")
                      .replace("ö", "o").replace("ü", "u").replace("ğ", "g")).strip("-")
        slug = "-".join(slug.split("-")[:2]) or "isletme"

        link_id = uuid.uuid4().hex[:12]
        link = {
            "url": f"https://moka.link/{slug}-{link_id[:4]}",
            "merchant_id": merchant_id,
            "amount_try": amount_try,
            "description": description,
            "expires": (date.today() + timedelta(days=7)).isoformat(),
        }
        try:
            with db.session(self.db_path) as connection:
                connection.execute(
                    "INSERT INTO payment_links "
                    "(link_id, merchant_id, url, amount_try, description, expires, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (link_id, merchant_id, link["url"], amount_try, description or "",
                     link["expires"], datetime.now().isoformat()),
                )
        except sqlite3.Error:
            pass  # link uretimi kayittan onemli: DB yazamasak da cagri devam etsin
        return link

    # ------------------------------------------------------------- summaries

    def monthly_summary(self, merchant_id: str,
                        month: Optional[str] = None) -> Dict[str, Any]:
        """Bir ayin cirosu / tahmini komisyonu / islem sayisi ("YYYY-MM")."""
        merchant = self.get_merchant(merchant_id)
        if not merchant:
            return {}
        target = month or date.today().strftime("%Y-%m")
        volume = next(
            (entry.get("volume", 0) for entry in merchant.get("monthly_volume_try", [])
             if entry.get("month") == target),
            0,
        )
        plan = merchant.get("plan") or {}
        rate = plan.get("rate_pct", 0)
        count_row = self._query_one(
            "SELECT COUNT(*) AS c FROM transactions "
            "WHERE merchant_id = ? AND substr(ts, 1, 7) = ?",
            (merchant_id, target),
        )
        return {
            "month": target,
            "gross_try": volume,
            "commission_try": round(volume * rate / 100),
            "rate_pct": rate,
            "plan_name": plan.get("name"),
            "txn_count": (count_row or {}).get("c", 0),
        }

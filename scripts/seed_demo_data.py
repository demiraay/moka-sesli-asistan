#!/usr/bin/env python3
"""Is verisi veritabanini (data/moka.sqlite3) seed_data/*.json'dan doldurur.

Bu JSON'lar artik CALISMA ZAMANI verisi degil, yalnizca seed kaynagi. Uygulama
hicbir yerde onlari okumaz; her sey SQLite'tan gelir.

Kullanim:
    python3 scripts/seed_demo_data.py            # DB yoksa kur, varsa dokunma
    python3 scripts/seed_demo_data.py --force    # her seyi silip yeniden kur
    python3 scripts/seed_demo_data.py --refresh  # sadece goreli tarihleri tazele
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import db                                            # noqa: E402
from core.migrations.business import BUSINESS_MIGRATIONS       # noqa: E402
from core.phone_utils import normalize_phone_number            # noqa: E402
from core.repository import MerchantRepository, resolve_date_token, tr_lower  # noqa: E402

SEED_DIR = BASE_DIR / "scripts" / "seed_data"
DB_PATH = BASE_DIR / "data" / "moka.sqlite3"

# Testler ve demo senaryolari bu ID'lere bagli — seed onlari ASLA yeniden uretmemeli.
REQUIRED_IDS = {
    "merchants": ["M-1001", "M-1007", "M-1011", "M-1012"],
    "transactions": ["TXN-88213"],
    "settlements": ["SET-8990", "SET-9012"],
    "pos_devices": ["TRM-4451"],
    "commission_plans": ["PLAN-STD", "PLAN-PLUS", "PLAN-RET"],
    "kb_articles": ["KB-POS-01", "KB-POS-02"],
}

EXPECTED_COUNTS = {
    "commission_plans": 4,
    "merchants": 18,
    "pos_devices": 18,
    "settlements": 82,
    "transactions": 332,
    "kb_articles": 8,
    "merchant_monthly_volume": 108,
    "merchant_contacts": 54,          # 18 isletme x 3 temas
}

# CRM temas gecmisi havuzlari (isletme-360 zaman cizgisi icin). Uretim
# deterministik: her merchant_id kendi tohumunu belirler.
_NORMAL_CONTACTS = [
    ("Memnuniyet araması", "Genel memnuniyet soruldu, ek bir sorun bildirilmedi."),
    ("Hakediş bilgilendirme", "Haftalık hakediş akışı ve ödeme takvimi anlatıldı."),
    ("Komisyon planı görüşmesi", "Mevcut plan gözden geçirildi; uygunsa yükseltme önerilecek."),
    ("Yeni kampanya tanıtımı", "Sezonluk komisyon kampanyası hakkında bilgi verildi."),
    ("Cihaz kontrol araması", "POS cihazının sorunsuz çalıştığı teyit edildi."),
    ("Sözleşme yenileme", "Sözleşme yenileme koşulları paylaşıldı."),
]
_RECOVERY_CONTACTS = [
    ("Kayıp hacim takibi", "İşlem hacmindeki düşüş konuşuldu, nedeni araştırılıyor."),
    ("Geri kazanım araması", "Tutundurma planı ve indirimli komisyon teklif edildi."),
    ("Cihaz arıza takibi", "Cihazın kapalı olabileceği değerlendirildi; teknik ekip yönlendirildi."),
    ("Memnuniyetsizlik görüşmesi", "Rakip teklifi nedeniyle olası geçiş riski not edildi."),
]
_CONTACT_WINDOWS = ((1, 6), (8, 20), (22, 45))   # yakin / orta / eski temas


def load(name: str):
    return json.loads((SEED_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _seed_contacts(connection, merchant, now: str) -> None:
    """Her isletme icin 3 gercekci temas kaydi (18x3 = 54). Deterministik."""
    merchant_id = merchant["merchant_id"]
    rng = random.Random(int(hashlib.md5(merchant_id.encode()).hexdigest()[:8], 16))
    series = [entry.get("volume", 0) for entry in merchant.get("monthly_volume_try") or []]
    prev_avg = sum(series[-4:-1]) / 3 if len(series) >= 4 else 0
    declining = prev_avg > 0 and series[-1] < 0.5 * prev_avg
    pref = merchant.get("preferred_channel", "telefon")
    rep = merchant.get("account_manager", "")

    for low, high in _CONTACT_WINDOWS:
        days = rng.randint(low, high)
        token = f"D-{days}T{rng.randint(9, 18):02d}:{rng.choice((0, 15, 30, 45)):02d}:00"
        channel = pref if rng.random() < 0.6 else rng.choice(
            ("telefon", "whatsapp", "email", "ziyaret"))
        direction = "inbound" if rng.random() < 0.35 else "outbound"
        subject, note = rng.choice(_RECOVERY_CONTACTS if declining else _NORMAL_CONTACTS)
        connection.execute(
            "INSERT INTO merchant_contacts (merchant_id, channel, direction, subject, note, "
            "rep, contacted_token, contacted_at) VALUES (?,?,?,?,?,?,?,?)",
            (merchant_id, channel, direction, subject, note, rep,
             token, str(resolve_date_token(token))))


def seed(connection) -> None:
    now = datetime.now().isoformat()

    for table in ("payment_links", "identities", "merchant_contacts", "kb_steps",
                  "kb_symptoms", "kb_articles", "transactions", "settlements",
                  "pos_devices", "merchant_monthly_volume", "merchant_products",
                  "merchants", "commission_plans", "app_config"):
        connection.execute(f"DELETE FROM {table}")

    # --- plans -------------------------------------------------------------
    for plan in load("commission_plans"):
        connection.execute(
            "INSERT INTO commission_plans (plan_id, name, rate_pct, monthly_fee_try, "
            "min_monthly_volume_try, retention_only, description) VALUES (?,?,?,?,?,?,?)",
            (plan["plan_id"], plan["name"], plan["rate_pct"],
             plan.get("monthly_fee_try", 0), plan.get("min_monthly_volume_try", 0),
             int(bool(plan.get("retention_only"))), plan.get("description", "")))

    # --- merchants ---------------------------------------------------------
    today = date.today()
    for merchant in load("merchants"):
        merchant_id = merchant["merchant_id"]
        phone = merchant.get("phone", "")
        connection.execute(
            "INSERT INTO merchants (merchant_id, business_name, owner_name, salutation, "
            "sector, mcc, city, district, phone, phone_e164, email, commission_plan_id, "
            "iban_masked, status, joined, notes, account_manager, preferred_channel, "
            "tier, marketing_opt_in) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (merchant_id, merchant["business_name"], merchant.get("owner_name", ""),
             merchant.get("salutation", ""), merchant.get("sector", ""),
             merchant.get("mcc", ""), merchant.get("city", ""), merchant.get("district", ""),
             phone, normalize_phone_number(phone), merchant.get("email", ""),
             merchant["commission_plan_id"], merchant.get("iban_masked", ""),
             merchant.get("status", "active"), merchant.get("joined", ""),
             merchant.get("notes", ""), merchant.get("account_manager", ""),
             merchant.get("preferred_channel", "telefon"), merchant.get("tier", ""),
             int(bool(merchant.get("marketing_opt_in")))))

        for order, product in enumerate(merchant.get("products") or []):
            connection.execute(
                "INSERT INTO merchant_products (merchant_id, product_key, sort_order) "
                "VALUES (?,?,?)", (merchant_id, product, order))

        # month_offset: 0 = icinde bulunulan ay, -1 = onceki... JSON'daki mutlak
        # ay etiketleri anlamsiz (runtime'da zaten yeniden etiketleniyordu);
        # onemli olan SIRA. ensure_fresh() bunlari bugune sabitler.
        series = merchant.get("monthly_volume_try") or []
        for index, entry in enumerate(series):
            offset = index - (len(series) - 1)           # son eleman -> 0
            year, month = today.year, today.month + offset
            while month <= 0:
                month += 12
                year -= 1
            connection.execute(
                "INSERT INTO merchant_monthly_volume "
                "(merchant_id, month_offset, month, volume_try) VALUES (?,?,?,?)",
                (merchant_id, offset, f"{year:04d}-{month:02d}", entry.get("volume", 0)))

        # Kimlik eslesmesi kalici: eskiden yalnizca RAM'de (call_contexts) tutuluyordu.
        if phone:
            connection.execute(
                "INSERT INTO identities (identity, kind, merchant_id, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(identity) DO NOTHING",
                (normalize_phone_number(phone), "phone", merchant_id, now))

        # CRM temas gecmisi (isletme-360 zaman cizgisi + "son temas" kaynagi).
        _seed_contacts(connection, merchant, now)

    # --- devices -----------------------------------------------------------
    for device in load("pos_devices"):
        token = device.get("last_seen_at", "")
        connection.execute(
            "INSERT INTO pos_devices (terminal_id, merchant_id, model, status, firmware, "
            "last_seen_token, last_seen_at, note) VALUES (?,?,?,?,?,?,?,?)",
            (device["terminal_id"], device["merchant_id"], device.get("model", ""),
             device.get("status", ""), device.get("firmware", ""), token,
             str(resolve_date_token(token)), device.get("note") or ""))

    # --- settlements -------------------------------------------------------
    for settlement in load("settlements"):
        batch_token = settlement.get("batch_date", "")
        eta_token = settlement.get("payout_eta", "")
        connection.execute(
            "INSERT INTO settlements (batch_id, merchant_id, batch_date_token, batch_date, "
            "payout_eta_token, payout_eta, gross_try, commission_try, net_try, status, "
            "iban_masked, txn_count, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (settlement["batch_id"], settlement["merchant_id"],
             batch_token, str(resolve_date_token(batch_token)),
             eta_token, str(resolve_date_token(eta_token)),
             settlement.get("gross_try", 0), settlement.get("commission_try", 0),
             settlement.get("net_try", 0), settlement.get("status", ""),
             settlement.get("iban_masked", ""), settlement.get("txn_count", 0),
             settlement.get("note")))

    # --- transactions ------------------------------------------------------
    for txn in load("transactions"):
        token = txn.get("timestamp", "")
        resolved = str(resolve_date_token(token))
        connection.execute(
            "INSERT INTO transactions (txn_id, merchant_id, terminal_id, settlement_batch_id, "
            "channel, amount_try, commission_try, net_try, card_masked, card_last4, "
            "ts_token, ts, ts_day, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (txn["txn_id"], txn["merchant_id"], txn.get("terminal_id"),
             txn.get("settlement_batch_id"), txn.get("channel", ""),
             txn.get("amount_try", 0), txn.get("commission_try", 0), txn.get("net_try", 0),
             txn.get("card_masked", ""), txn.get("card_last4", ""),
             token, resolved, resolved[:10], txn.get("status", "")))

    # --- knowledge base ----------------------------------------------------
    for order, article in enumerate(load("support_kb")):
        issue_id = article["issue_id"]
        connection.execute(
            "INSERT INTO kb_articles (issue_id, category, title, escalate_if_unresolved, "
            "escalation, sort_order) VALUES (?,?,?,?,?,?)",
            (issue_id, article.get("category", ""), article.get("title", ""),
             int(bool(article.get("escalate_if_unresolved"))),
             article.get("escalation") or "", order))
        for symptom in article.get("symptoms") or []:
            # symptom_normalized: SQLite LOWER() Turkce 'I'/'İ' cozemez.
            connection.execute(
                "INSERT INTO kb_symptoms (issue_id, symptom, symptom_normalized) "
                "VALUES (?,?,?)", (issue_id, symptom, tr_lower(symptom)))
        for step_no, step in enumerate(article.get("steps") or [], start=1):
            connection.execute(
                "INSERT INTO kb_steps (issue_id, step_no, step_text) VALUES (?,?,?)",
                (issue_id, step_no, step))

    # --- singleton config --------------------------------------------------
    projects = load("projects")
    for key, value in (("project", projects[0] if projects else {}),
                       ("rules", load("rules")),
                       ("handoff_rules", load("handoff_rules"))):
        connection.execute(
            "INSERT INTO app_config (key, value_json) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)))


def verify(connection) -> list:
    problems = []

    for table, expected in EXPECTED_COUNTS.items():
        actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if actual != expected:
            problems.append(f"{table}: {actual} kayit, beklenen {expected}")

    # Sabit ID'ler korundu mu? (testler ve demo senaryolari bunlara bagli)
    for table, ids in REQUIRED_IDS.items():
        column = {"merchants": "merchant_id", "transactions": "txn_id",
                  "settlements": "batch_id", "pos_devices": "terminal_id",
                  "commission_plans": "plan_id", "kb_articles": "issue_id"}[table]
        for required in ids:
            row = connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?", (required,)).fetchone()
            if not row:
                problems.append(f"{table}.{column} = {required} KAYIP")

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        problems.append(f"{len(violations)} foreign key ihlali")

    duplicates = connection.execute(
        "SELECT phone_e164, COUNT(*) c FROM merchants WHERE phone_e164 != '' "
        "GROUP BY phone_e164 HAVING c > 1").fetchall()
    if duplicates:
        problems.append(f"{len(duplicates)} tekrar eden telefon numarasi")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Moka is verisi seed'i")
    parser.add_argument("--force", action="store_true",
                        help="Mevcut veriyi silip yeniden kur")
    parser.add_argument("--refresh", action="store_true",
                        help="Sadece goreli tarihleri bugune tazele")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    already_seeded = DB_PATH.exists()
    db.migrate(DB_PATH, BUSINESS_MIGRATIONS)

    if args.refresh:
        if not already_seeded:
            print("Veritabani yok; once seed calistirin.")
            return 1
        changed = MerchantRepository(DB_PATH).ensure_fresh()
        print("Tarihler tazelendi." if changed else "Tarihler zaten guncel.")
        return 0

    connection = db.connect(DB_PATH)
    try:
        count = connection.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
    finally:
        connection.close()

    if count and not args.force:
        print(f"Veritabani zaten dolu ({count} isletme). Yeniden kurmak icin: --force")
        return 0

    with db.session(DB_PATH) as connection:
        seed(connection)

    connection = db.connect(DB_PATH)
    try:
        problems = verify(connection)
    finally:
        connection.close()

    if problems:
        print("SEED DOGRULAMASI BASARISIZ:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    MerchantRepository(DB_PATH).ensure_fresh()
    print(f"Seed tamam: {DB_PATH}")
    for table, expected in EXPECTED_COUNTS.items():
        print(f"  {table}: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

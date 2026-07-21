"""Is verisi veritabani (moka.sqlite3) migration'lari.

Eskiden bu veri data/*.json dosyalarindaydi ve Config singleton'i tarafindan
bellege yuklenip Python join'leriyle sorgulaniyordu. Artik gercek iliskisel sema.

Iki DB dosyasi var — is verisi (burasi, seed'li ve okuma agirlikli) ve operasyon
verisi (admin.sqlite3: konusmalar, gorevler, lead'ler). Ayri tutulmalarinin
sebebi testler: AdminStore(db_path=tmp) fixture'i 5 test dosyasinda kullaniliyor;
tek dosyaya birlestirilseydi gecici DB'de M-1001 verisi olmazdi.

TARIH MODELI: goreli token'lar (D-1T16:40:00) HAM haliyle *_token sutununda
saklanir, cozulmus ISO degeri ayri sutunda tutulur. Cozulmus deger somut olmak
zorunda, yoksa index kullanilamaz ve her sorgu tam tarama olur. Tazeleme icin
bkz. MerchantRepository.ensure_fresh().
"""

from __future__ import annotations

from typing import List

from core.db import Migration


_V1_STATEMENTS: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS commission_plans (
        plan_id                TEXT PRIMARY KEY,
        name                   TEXT NOT NULL,
        rate_pct               REAL NOT NULL,
        monthly_fee_try        INTEGER NOT NULL DEFAULT 0,
        min_monthly_volume_try INTEGER NOT NULL DEFAULT 0,
        retention_only         INTEGER NOT NULL DEFAULT 0,
        description            TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS merchants (
        merchant_id        TEXT PRIMARY KEY,
        business_name      TEXT NOT NULL,
        owner_name         TEXT NOT NULL DEFAULT '',
        salutation         TEXT NOT NULL DEFAULT '',
        sector             TEXT NOT NULL DEFAULT '',
        mcc                TEXT NOT NULL DEFAULT '',
        city               TEXT NOT NULL DEFAULT '',
        district           TEXT NOT NULL DEFAULT '',
        phone              TEXT NOT NULL DEFAULT '',
        phone_e164         TEXT NOT NULL DEFAULT '',
        email              TEXT NOT NULL DEFAULT '',
        commission_plan_id TEXT NOT NULL REFERENCES commission_plans(plan_id),
        iban_masked        TEXT NOT NULL DEFAULT '',
        status             TEXT NOT NULL DEFAULT 'active',
        joined             TEXT NOT NULL DEFAULT '',
        notes              TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_merchants_plan ON merchants(commission_plan_id)",
    # Telefon eslesmesi eskiden son-10-hane string hilesiydi; artik indeksli esitlik.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_merchants_phone ON merchants(phone_e164)",
    """
    CREATE TABLE IF NOT EXISTS merchant_products (
        merchant_id TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        product_key TEXT NOT NULL,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (merchant_id, product_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS merchant_monthly_volume (
        merchant_id  TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        month_offset INTEGER NOT NULL,
        month        TEXT NOT NULL,
        volume_try   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (merchant_id, month_offset)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mmv_month ON merchant_monthly_volume(merchant_id, month)",
    """
    CREATE TABLE IF NOT EXISTS pos_devices (
        terminal_id     TEXT PRIMARY KEY,
        merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        model           TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT '',
        firmware        TEXT NOT NULL DEFAULT '',
        last_seen_token TEXT NOT NULL DEFAULT '',
        last_seen_at    TEXT NOT NULL DEFAULT '',
        note            TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pos_merchant ON pos_devices(merchant_id)",
    """
    CREATE TABLE IF NOT EXISTS settlements (
        batch_id         TEXT PRIMARY KEY,
        merchant_id      TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        batch_date_token TEXT NOT NULL DEFAULT '',
        batch_date       TEXT NOT NULL DEFAULT '',
        payout_eta_token TEXT NOT NULL DEFAULT '',
        payout_eta       TEXT NOT NULL DEFAULT '',
        gross_try        REAL NOT NULL DEFAULT 0,
        commission_try   REAL NOT NULL DEFAULT 0,
        net_try          REAL NOT NULL DEFAULT 0,
        status           TEXT NOT NULL DEFAULT '',
        iban_masked      TEXT NOT NULL DEFAULT '',
        txn_count        INTEGER NOT NULL DEFAULT 0,
        note             TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_settle_merchant ON settlements(merchant_id, batch_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_settle_status ON settlements(merchant_id, status, batch_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS transactions (
        txn_id              TEXT PRIMARY KEY,
        merchant_id         TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        terminal_id         TEXT REFERENCES pos_devices(terminal_id),
        settlement_batch_id TEXT REFERENCES settlements(batch_id),
        channel             TEXT NOT NULL DEFAULT '',
        amount_try          REAL NOT NULL DEFAULT 0,
        commission_try      REAL NOT NULL DEFAULT 0,
        net_try             REAL NOT NULL DEFAULT 0,
        card_masked         TEXT NOT NULL DEFAULT '',
        card_last4          TEXT NOT NULL DEFAULT '',
        ts_token            TEXT NOT NULL DEFAULT '',
        ts                  TEXT NOT NULL DEFAULT '',
        ts_day              TEXT NOT NULL DEFAULT '',
        status              TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_txn_merchant_day ON transactions(merchant_id, ts_day DESC, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_txn_last4 ON transactions(merchant_id, card_last4)",
    "CREATE INDEX IF NOT EXISTS idx_txn_amount ON transactions(merchant_id, amount_try)",
    "CREATE INDEX IF NOT EXISTS idx_txn_batch ON transactions(settlement_batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(merchant_id, status, ts DESC)",
    """
    CREATE TABLE IF NOT EXISTS kb_articles (
        issue_id               TEXT PRIMARY KEY,
        category               TEXT NOT NULL DEFAULT '',
        title                  TEXT NOT NULL DEFAULT '',
        escalate_if_unresolved INTEGER NOT NULL DEFAULT 0,
        escalation             TEXT NOT NULL DEFAULT '',
        sort_order             INTEGER NOT NULL DEFAULT 0
    )
    """,
    # symptom_normalized: SQLite LOWER() Turkce 'I'/'İ' cozemez, bu yuzden
    # normalize edilmis kopya SEED'de Python tr_lower() ile uretilir.
    """
    CREATE TABLE IF NOT EXISTS kb_symptoms (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id           TEXT NOT NULL REFERENCES kb_articles(issue_id) ON DELETE CASCADE,
        symptom            TEXT NOT NULL,
        symptom_normalized TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kb_symptom_issue ON kb_symptoms(issue_id)",
    "CREATE INDEX IF NOT EXISTS idx_kb_symptom_norm ON kb_symptoms(symptom_normalized)",
    """
    CREATE TABLE IF NOT EXISTS kb_steps (
        issue_id  TEXT NOT NULL REFERENCES kb_articles(issue_id) ON DELETE CASCADE,
        step_no   INTEGER NOT NULL,
        step_text TEXT NOT NULL,
        PRIMARY KEY (issue_id, step_no)
    )
    """,
    # Arayan kimligi (whatsapp telefonu / call-xxxx / manual-*) -> isletme.
    # Eskiden bu eslesme yalnizca RAM'deydi (call_contexts dict), process
    # yeniden baslayinca kayboluyordu.
    """
    CREATE TABLE IF NOT EXISTS identities (
        identity    TEXT PRIMARY KEY,
        kind        TEXT NOT NULL DEFAULT 'phone',
        merchant_id TEXT REFERENCES merchants(merchant_id) ON DELETE SET NULL,
        created_at  TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_identities_merchant ON identities(merchant_id)",
    # projects.json / rules.json / handoff_rules.json'in yeni evi.
    """
    CREATE TABLE IF NOT EXISTS app_config (
        key        TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    )
    """,
    # Uretilen odeme linkleri eskiden HICBIR YERE yazilmiyordu, ucup gidiyordu.
    """
    CREATE TABLE IF NOT EXISTS payment_links (
        link_id     TEXT PRIMARY KEY,
        merchant_id TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        url         TEXT NOT NULL,
        amount_try  REAL,
        description TEXT NOT NULL DEFAULT '',
        expires     TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_links_merchant ON payment_links(merchant_id, created_at DESC)",
]


# CRM katmani: panel "Musteriler" sekmesi ve isletme-360 gorunumu icin.
# account_manager / preferred_channel / tier / marketing_opt_in DOGRUDAN saklanir
# (turetilecek kaynak yok). risk_score / risk_tier / segment / last_contact_at
# BILEREK kolon DEGIL — merchant_monthly_volume + merchant_contacts'ten anlik
# hesaplanir; saklansaydi ensure_fresh() ay etiketlerini kaydirdikca bayatlardi.
_V2_STATEMENTS: List[str] = [
    "ALTER TABLE merchants ADD COLUMN account_manager   TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE merchants ADD COLUMN preferred_channel TEXT NOT NULL DEFAULT 'telefon'",
    "ALTER TABLE merchants ADD COLUMN tier              TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE merchants ADD COLUMN marketing_opt_in  INTEGER NOT NULL DEFAULT 0",
    # Temas gecmisi: isletme-360 zaman cizgisi + "son temas" tarihinin TEK kaynagi.
    # Tarihler cift-kolon token deseninde: contacted_token (ham D-3T14:20:00) +
    # contacted_at (cozulmus ISO). ensure_fresh() bu tabloyu da tazeler.
    """
    CREATE TABLE IF NOT EXISTS merchant_contacts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id) ON DELETE CASCADE,
        channel         TEXT NOT NULL DEFAULT '',
        direction       TEXT NOT NULL DEFAULT 'outbound',
        subject         TEXT NOT NULL DEFAULT '',
        note            TEXT NOT NULL DEFAULT '',
        rep             TEXT NOT NULL DEFAULT '',
        contacted_token TEXT NOT NULL DEFAULT '',
        contacted_at    TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_merchant_contacts ON merchant_contacts(merchant_id, contacted_at DESC)",
]


# Konusma->CRM kapali dongusu: agent canli konusmalardan temas/icgoru kaydi
# uretir. source ayrimi SEED verisini (source='seed') canli kayitlardan
# (source='ai' otomatik gunluk, source='insight' agent'in bilincli notu)
# ayirir — sayim dogrulamasi yalnizca seed'i sayar. session_id, bir oturumun
# tek temas kaydinda toplanmasi (upsert) icin anahtardir.
_V3_STATEMENTS: List[str] = [
    "ALTER TABLE merchant_contacts ADD COLUMN session_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE merchant_contacts ADD COLUMN source     TEXT NOT NULL DEFAULT 'seed'",
    "CREATE INDEX IF NOT EXISTS idx_mc_session ON merchant_contacts(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_source ON merchant_contacts(merchant_id, source, contacted_at DESC)",
]


# Konusma->CRM derinlestirme: her temas kaydina cozum durumu (outcome:
# çözüldü/açık/takip) ve musteri ruh hali/memnuniyet (sentiment) yansir.
# Boylece 360 ve raporlar "ne konusuldu"nun yani sira "nasil sonuclandi"yi
# da gosterir.
_V4_STATEMENTS: List[str] = [
    "ALTER TABLE merchant_contacts ADD COLUMN outcome   TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE merchant_contacts ADD COLUMN sentiment TEXT NOT NULL DEFAULT ''",
]


BUSINESS_MIGRATIONS: List[Migration] = [
    (1, "business_schema", _V1_STATEMENTS),
    (2, "crm_fields", _V2_STATEMENTS),
    (3, "crm_contact_source", _V3_STATEMENTS),
    (4, "crm_contact_outcome", _V4_STATEMENTS),
]

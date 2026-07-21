"""Operasyon veritabani (admin.sqlite3) migration'lari.

v1 : Mevcut AdminStore._ensure_schema semasinin birebir karsiligi. Tum ifadeler
     "IF NOT EXISTS" oldugu icin zaten kurulmus bir DB uzerinde de guvenle kosar
     ve yalnizca user_version damgasini atar.
v2 : Panel kontratini saglamlastirir + canli tablolara ILK index'leri ekler.

NOT: listing_* / unit_reservations / offers tablolari emlak projesinden kalmadir
ve hala 3 test onlara bagli (test_admin_store). Bu yuzden v1'de AYNEN korunurlar;
temizlikleri bu isin kapsami disinda.
"""

from __future__ import annotations

from typing import List

from core.db import Migration


_V1_STATEMENTS: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'default',
        created_at TEXT NOT NULL,
        last_message_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'default',
        user_input TEXT NOT NULL,
        agent_response TEXT NOT NULL,
        router_decision_json TEXT NOT NULL,
        context_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_ai_notes (
        user_id TEXT PRIMARY KEY,
        ai_summary TEXT NOT NULL,
        ai_notes_json TEXT NOT NULL,
        manual_notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT 'new',
        ai_paused INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        message TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT 'panel',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        sent_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS briefings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        due_at TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        done_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inventory_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS unit_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inventory_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        amount_try INTEGER,
        note TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'active',
        expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL DEFAULT '',
        is_cover INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_listing_photos_ref ON listing_photos(scope, ref_id)",
    """
    CREATE TABLE IF NOT EXISTS listing_price_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inventory_id TEXT NOT NULL,
        old_price INTEGER,
        new_price INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT 'panel',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_status_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inventory_id TEXT NOT NULL,
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'panel',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_listing_status_log_to ON listing_status_log(to_status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS ai_blocklist (
        user_id TEXT PRIMARY KEY,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sales_profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        consultant_name TEXT NOT NULL DEFAULT '',
        consultant_title TEXT NOT NULL DEFAULT '',
        phone_number TEXT NOT NULL DEFAULT '',
        whatsapp_number TEXT NOT NULL DEFAULT '',
        office_name TEXT NOT NULL DEFAULT '',
        office_address TEXT NOT NULL DEFAULT '',
        maps_url TEXT NOT NULL DEFAULT '',
        latitude TEXT NOT NULL DEFAULT '',
        longitude TEXT NOT NULL DEFAULT '',
        location_label TEXT NOT NULL DEFAULT '',
        auto_share_whatsapp_location INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
]


# v2 — handoff artik GERCEK sutun.
#
# Onceki hal: panel handoff'u ham SQL ile sayiyordu:
#     context_json LIKE '%"required": true%'
# Bu, json.dumps'in VARSAYILAN ayrac bosluguna bagliydi. ResponseBuilder.to_json()
# kompakt ayrac kullaniyor (separators=(",", ":")) — yani ayni veri iki farkli
# bicimde yazilabiliyordu ve sorgu sessizce kacirabiliyordu. Backfill her iki
# varyanti da kapsar.
_V2_STATEMENTS: List[str] = [
    "ALTER TABLE conversation_turns ADD COLUMN handoff_required INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE conversation_turns ADD COLUMN tool_names TEXT NOT NULL DEFAULT ''",
    """
    UPDATE conversation_turns
       SET handoff_required = 1
     WHERE context_json LIKE '%"required": true%'
        OR context_json LIKE '%"required":true%'
    """,
    # Canli tablolarda ILK performans index'leri. Onceki semada tanimli iki index
    # de olu emlak tablolarindaydi; sicak yollarda tek index yoktu.
    "CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_handoff ON conversation_turns(handoff_required, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_turns_created ON conversation_turns(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON conversation_sessions(user_id, channel, last_message_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lead_events_user ON lead_events(user_id, event_type, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(done, due_at)",
]


ADMIN_MIGRATIONS: List[Migration] = [
    (1, "initial_schema", _V1_STATEMENTS),
    (2, "handoff_column_and_indexes", _V2_STATEMENTS),
]

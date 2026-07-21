"""core/db.py migration runner ve admin semasi v1->v2."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from core.admin_store import AdminStore
from core.migrations.admin import ADMIN_MIGRATIONS
from core.migrations.business import BUSINESS_MIGRATIONS


class TestMigrationRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"

    def test_migrates_from_zero_to_latest(self):
        version = db.migrate(self.db_path, ADMIN_MIGRATIONS)
        self.assertEqual(version, 2)

    def test_second_run_is_noop(self):
        db.migrate(self.db_path, ADMIN_MIGRATIONS)
        self.assertEqual(db.migrate(self.db_path, ADMIN_MIGRATIONS), 2)

    def test_partial_migration_rolls_back_and_keeps_version(self):
        """Migration ortasinda hata: ROLLBACK edilir, user_version ARTMAZ."""
        broken = [
            (1, "ok", ["CREATE TABLE alpha (id INTEGER PRIMARY KEY)"]),
            (2, "broken", [
                "CREATE TABLE beta (id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ]),
        ]
        with self.assertRaises(RuntimeError):
            db.migrate(self.db_path, broken)

        connection = db.connect(self.db_path)
        try:
            self.assertEqual(db.get_version(connection), 1)
            self.assertTrue(db.table_exists(connection, "alpha"))
            # beta yaratildi ama ayni transaction'da geri alindi
            self.assertFalse(db.table_exists(connection, "beta"))
        finally:
            connection.close()

    def test_migrations_applied_in_version_order(self):
        out_of_order = [
            (2, "second", ["CREATE TABLE second_table (id INTEGER)"]),
            (1, "first", ["CREATE TABLE first_table (id INTEGER)"]),
        ]
        db.migrate(self.db_path, out_of_order)
        connection = db.connect(self.db_path)
        try:
            self.assertTrue(db.table_exists(connection, "first_table"))
            self.assertTrue(db.table_exists(connection, "second_table"))
        finally:
            connection.close()


class TestBusinessSchemaV2(unittest.TestCase):
    """Is verisi semasi v1->v3: CRM kolonlari + merchant_contacts + source/session."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "moka.sqlite3"

    def test_migrates_from_zero_to_latest(self):
        self.assertEqual(db.migrate(self.db_path, BUSINESS_MIGRATIONS), 3)

    def test_second_run_is_noop(self):
        db.migrate(self.db_path, BUSINESS_MIGRATIONS)
        self.assertEqual(db.migrate(self.db_path, BUSINESS_MIGRATIONS), 3)

    def test_v3_adds_source_and_session_columns(self):
        db.migrate(self.db_path, BUSINESS_MIGRATIONS)
        connection = db.connect(self.db_path)
        try:
            cols = set(db.column_names(connection, "merchant_contacts"))
        finally:
            connection.close()
        self.assertIn("source", cols)
        self.assertIn("session_id", cols)

    def test_crm_columns_added_to_merchants(self):
        db.migrate(self.db_path, BUSINESS_MIGRATIONS)
        connection = db.connect(self.db_path)
        try:
            cols = set(db.column_names(connection, "merchants"))
        finally:
            connection.close()
        for expected in ("account_manager", "preferred_channel", "tier", "marketing_opt_in"):
            self.assertIn(expected, cols)

    def test_merchant_contacts_table_and_index(self):
        db.migrate(self.db_path, BUSINESS_MIGRATIONS)
        connection = db.connect(self.db_path)
        try:
            self.assertTrue(db.table_exists(connection, "merchant_contacts"))
            cols = set(db.column_names(connection, "merchant_contacts"))
            indexes = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            connection.close()
        # Cift-kolon token deseni sart
        for expected in ("contacted_token", "contacted_at", "merchant_id", "channel"):
            self.assertIn(expected, cols)
        self.assertIn("idx_merchant_contacts", indexes)

    def test_upgrade_from_v1_only_is_idempotent(self):
        """Sadece v1'de olan DB en son surume yukseltilebilir (ALTER yeniden kosmaz)."""
        db.migrate(self.db_path, BUSINESS_MIGRATIONS[:1])   # yalniz v1
        self.assertEqual(db.migrate(self.db_path, BUSINESS_MIGRATIONS), 3)
        # Ikinci tam kosu da no-op
        self.assertEqual(db.migrate(self.db_path, BUSINESS_MIGRATIONS), 3)


class TestConnectionPragmas(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "pragma.sqlite3"

    def test_pragmas_are_set_on_every_connection(self):
        db.migrate(self.db_path, ADMIN_MIGRATIONS)
        for _ in range(2):
            connection = db.connect(self.db_path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                # foreign_keys BAGLANTI basinadir; her yeni baglantida ON olmali
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                connection.close()


class TestHandoffColumn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "admin.sqlite3"
        self.store = AdminStore(db_path=str(self.db_path))

    def _log(self, session_id, user_id, context, router_decision=None):
        self.store.log_turn(
            session_id=session_id, user_id=user_id, channel="voice",
            user_input="test", agent_response="ok",
            router_decision=router_decision or {"tool": "answer_general", "args": {}},
            context=context,
        )

    def _column(self, name):
        connection = db.connect(self.db_path)
        try:
            return [row[name] for row in
                    connection.execute(f"SELECT {name} FROM conversation_turns ORDER BY id")]
        finally:
            connection.close()

    def test_handoff_flag_written_from_context(self):
        self._log("s1", "u1", {"handoff": {"required": True, "reason": "ofkeli"}})
        self._log("s2", "u2", {"handoff": {"required": False}})
        self._log("s3", "u3", {})
        self.assertEqual(self._column("handoff_required"), [1, 0, 0])

    def test_tool_names_from_single_tool(self):
        self._log("s1", "u1", {}, {"tool": "get_settlement_status", "args": {}})
        self.assertEqual(self._column("tool_names"), ["get_settlement_status"])

    def test_tool_names_from_multi_tool_loop(self):
        """Cok adimli loop bir turda birden fazla arac calistirabilir."""
        self._log("s1", "u1", {}, {
            "tool": "find_transaction",
            "tools": [{"name": "find_transaction"}, {"name": "recommend_offer"}],
        })
        self.assertEqual(self._column("tool_names"), ["find_transaction,recommend_offer"])

    def test_backfill_covers_both_json_separator_styles(self):
        """v2 backfill'i hem bosluklu hem kompakt json.dumps ciktisini yakalar."""
        raw_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        db.migrate(raw_path, ADMIN_MIGRATIONS[:1])      # sadece v1

        connection = db.connect(raw_path)
        try:
            connection.execute(
                "INSERT INTO conversation_sessions VALUES ('s1','u1','voice','t','t')")
            for turn_id, context_json in (
                (1, '{"handoff": {"required": true}}'),   # varsayilan ayrac (bosluklu)
                (2, '{"handoff":{"required":true}}'),     # kompakt ayrac
                (3, '{"handoff": {"required": false}}'),
            ):
                connection.execute(
                    "INSERT INTO conversation_turns "
                    "(id, session_id, channel, user_input, agent_response, "
                    " router_decision_json, context_json, created_at) "
                    "VALUES (?,'s1','voice','x','y','{}',?,'t')",
                    (turn_id, context_json))
            connection.commit()
        finally:
            connection.close()

        db.migrate(raw_path, ADMIN_MIGRATIONS)           # v2'ye yukselt

        connection = db.connect(raw_path)
        try:
            flags = [row["handoff_required"] for row in connection.execute(
                "SELECT handoff_required FROM conversation_turns ORDER BY id")]
        finally:
            connection.close()
        self.assertEqual(flags, [1, 1, 0])

    def test_handoff_queue_uses_indexed_column(self):
        self._log("s1", "u1", {"handoff": {"required": True, "reason": "ofkeli musteri"}})
        self._log("s2", "u2", {"handoff": {"required": False}})

        queue = self.store.get_handoff_queue()
        self.assertEqual([item["user_id"] for item in queue], ["u1"])
        self.assertEqual(queue[0]["reason"], "ofkeli musteri")

    def test_indexes_exist_on_hot_paths(self):
        connection = db.connect(self.db_path)
        try:
            names = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            connection.close()
        for expected in ("idx_turns_session", "idx_turns_handoff",
                         "idx_sessions_user", "idx_lead_events_user"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()

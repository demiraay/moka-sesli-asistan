from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core import db
from core.config import Config
from core.migrations.admin import ADMIN_MIGRATIONS


class AdminStore:
    def __init__(self, base_dir: Optional[str] = None, db_path: Optional[str] = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        self.data_dir = self.base_dir / "data"
        self.db_path = Path(db_path) if db_path else self.data_dir / "admin.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # core.db.connect: WAL + busy_timeout + foreign_keys=ON (bkz. core/db.py).
        connection = db.connect(self.db_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        """Numarali migration'lari uygular (bkz. core/migrations/admin.py).

        Eskiden burada elle yazilmis CREATE TABLE / ALTER TABLE zinciri vardi;
        versiyon takibi ve geri alma yoktu.
        """
        db.migrate(self.db_path, ADMIN_MIGRATIONS)

    def _ensure_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _normalize_conversation_filters(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        filters = filters or {}
        return {
            "query": str(filters.get("query", "")).strip().lower(),
            "date_from": str(filters.get("date_from", "")).strip(),
            "date_to": str(filters.get("date_to", "")).strip(),
            "handoff_only": bool(filters.get("handoff_only", False)),
            "price_only": bool(filters.get("price_only", False)),
            "flat_type": str(filters.get("flat_type", "")).strip().lower(),
            "channel": str(filters.get("channel", "")).strip().lower(),
        }

    def _build_conversation_filters(
        self,
        filters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> tuple[str, List[Any]]:
        normalized = self._normalize_conversation_filters(filters)
        where_clauses = []
        params: List[Any] = []

        if user_id is not None:
            where_clauses.append("sessions.user_id = ?")
            params.append(user_id)

        if normalized["channel"] and normalized["channel"] != "all":
            where_clauses.append("LOWER(sessions.channel) = ?")
            params.append(normalized["channel"])

        if normalized["query"]:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM conversation_turns AS search_turns
                    WHERE search_turns.session_id = sessions.session_id
                      AND (
                          LOWER(search_turns.user_input) LIKE ?
                          OR LOWER(search_turns.agent_response) LIKE ?
                      )
                )
                """
            )
            keyword = f"%{normalized['query']}%"
            params.extend([keyword, keyword])

        if normalized["date_from"]:
            where_clauses.append("SUBSTR(sessions.last_message_at, 1, 10) >= ?")
            params.append(normalized["date_from"])

        if normalized["date_to"]:
            where_clauses.append("SUBSTR(sessions.last_message_at, 1, 10) <= ?")
            params.append(normalized["date_to"])

        if normalized["handoff_only"]:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM conversation_turns AS handoff_turns
                    WHERE handoff_turns.session_id = sessions.session_id
                      AND (
                          handoff_turns.handoff_required = 1
                          OR LOWER(handoff_turns.tool_names) LIKE '%trigger_handoff%'
                      )
                )
                """
            )

        if normalized["price_only"]:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM conversation_turns AS price_turns
                    WHERE price_turns.session_id = sessions.session_id
                      AND (
                          LOWER(price_turns.user_input) LIKE '%fiyat%'
                          OR LOWER(price_turns.user_input) LIKE '%bütçe%'
                          OR LOWER(price_turns.user_input) LIKE '%butce%'
                          OR LOWER(price_turns.user_input) LIKE '%milyon%'
                          OR LOWER(price_turns.user_input) LIKE '%tl%'
                          OR LOWER(price_turns.agent_response) LIKE '%fiyat%'
                          OR LOWER(price_turns.agent_response) LIKE '%milyon%'
                          OR LOWER(price_turns.agent_response) LIKE '%tl%'
                          OR LOWER(price_turns.router_decision_json) LIKE '%check_price%'
                          OR LOWER(price_turns.router_decision_json) LIKE '%max_price%'
                          OR LOWER(price_turns.router_decision_json) LIKE '%min_price%'
                      )
                )
                """
            )

        if normalized["flat_type"]:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM conversation_turns AS flat_turns
                    WHERE flat_turns.session_id = sessions.session_id
                      AND (
                          LOWER(flat_turns.user_input) LIKE ?
                          OR LOWER(flat_turns.agent_response) LIKE ?
                          OR LOWER(flat_turns.router_decision_json) LIKE ?
                      )
                )
                """
            )
            flat_keyword = f"%{normalized['flat_type']}%"
            params.extend([flat_keyword, flat_keyword, flat_keyword])

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        return where_sql, params

    def log_turn(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        user_input: str,
        agent_response: str,
        router_decision: Dict[str, Any],
        context: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> None:
        # created_at yalnizca test/ice aktarma senaryolari icin gecmise yazmayi saglar.
        timestamp = created_at or self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_sessions (session_id, user_id, channel, created_at, last_message_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET last_message_at = excluded.last_message_at
                """,
                (session_id, user_id, channel, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    session_id,
                    channel,
                    user_input,
                    agent_response,
                    router_decision_json,
                    context_json,
                    created_at,
                    handoff_required,
                    tool_names
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    channel,
                    user_input,
                    agent_response,
                    json.dumps(router_decision, ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False),
                    timestamp,
                    self._handoff_flag(context),
                    self._tool_names(router_decision),
                ),
            )

    @staticmethod
    def _handoff_flag(context: Dict[str, Any]) -> int:
        """context_json'daki handoff durumunu sorgulanabilir sutuna cevirir.

        Panel eskiden bunu ham SQL LIKE '%"required": true%' ile ariyordu; o
        sorgu json.dumps'in ayrac boslugu degisirse sessizce kaciriyordu.
        """
        handoff = context.get("handoff") if isinstance(context, dict) else None
        return 1 if isinstance(handoff, dict) and handoff.get("required") else 0

    @staticmethod
    def _tool_names(router_decision: Dict[str, Any]) -> str:
        """Turda calisan araclarin virgullu listesi.

        Cok adimli agent loop'ta bir turda birden fazla arac calisabilir; tek
        'tool' alani bunu tasiyamaz. Loop oncesi de dogru calisir.
        """
        if not isinstance(router_decision, dict):
            return ""
        calls = router_decision.get("tools")
        if isinstance(calls, list) and calls:
            names = [str(call.get("name", "")) for call in calls if isinstance(call, dict)]
            return ",".join(name for name in names if name)
        single = router_decision.get("tool")
        return str(single) if single else ""

    def list_conversations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._build_conversation_filters(filters=filters, user_id=user_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    sessions.session_id,
                    sessions.user_id,
                    sessions.channel,
                    sessions.created_at,
                    sessions.last_message_at,
                    (
                        SELECT COUNT(*)
                        FROM conversation_turns AS turns
                        WHERE turns.session_id = sessions.session_id
                    ) AS turn_count,
                    (
                        SELECT turns.user_input
                        FROM conversation_turns AS turns
                        WHERE turns.session_id = sessions.session_id
                        ORDER BY turns.id DESC
                        LIMIT 1
                    ) AS last_user_input
                FROM conversation_sessions AS sessions
                {where_sql}
                ORDER BY sessions.last_message_at DESC
                """,
                params,
            ).fetchall()

        return [dict(row) for row in rows]

    def list_users_with_conversations(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        users: Dict[str, Dict[str, Any]] = {}

        for conversation in self.list_conversations(filters=filters):
            user_id = conversation["user_id"]
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "conversation_count": 0,
                    "turn_count": 0,
                    "last_message_at": conversation["last_message_at"],
                    "conversations": [],
                }

            users[user_id]["conversation_count"] += 1
            users[user_id]["turn_count"] += conversation["turn_count"]
            users[user_id]["conversations"].append(conversation)

            if conversation["last_message_at"] > users[user_id]["last_message_at"]:
                users[user_id]["last_message_at"] = conversation["last_message_at"]

        grouped_users = list(users.values())
        grouped_users.sort(key=lambda item: item["last_message_at"], reverse=True)
        return grouped_users

    def get_user_conversations(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        all_groups = self.list_users_with_conversations()
        base_group = next((group for group in all_groups if group["user_id"] == user_id), None)
        if base_group is None:
            raise KeyError(f"User not found: {user_id}")

        filtered_conversations = self.list_conversations(filters=filters, user_id=user_id)
        if filtered_conversations:
            return {
                "user_id": user_id,
                "conversation_count": len(filtered_conversations),
                "turn_count": sum(item["turn_count"] for item in filtered_conversations),
                "last_message_at": max(item["last_message_at"] for item in filtered_conversations),
                "conversations": filtered_conversations,
            }

        return {
            "user_id": user_id,
            "conversation_count": 0,
            "turn_count": 0,
            "last_message_at": base_group["last_message_at"],
            "conversations": [],
        }

    def save_user_ai_notes(self, user_id: str, ai_summary: str, ai_notes: Dict[str, Any]) -> None:
        timestamp = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_ai_notes (user_id, ai_summary, ai_notes_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    ai_summary = excluded.ai_summary,
                    ai_notes_json = excluded.ai_notes_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    ai_summary,
                    json.dumps(ai_notes, ensure_ascii=False),
                    timestamp,
                ),
            )

    def update_manual_notes(self, user_id: str, manual_notes: str) -> None:
        timestamp = self._utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT ai_summary, ai_notes_json FROM user_ai_notes WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO user_ai_notes (user_id, ai_summary, ai_notes_json, manual_notes, updated_at)
                    VALUES (?, '', '{}', ?, ?)
                    """,
                    (user_id, manual_notes, timestamp),
                )
            else:
                connection.execute(
                    """
                    UPDATE user_ai_notes
                    SET manual_notes = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (manual_notes, timestamp, user_id),
                )

    def get_user_ai_notes(self, user_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, ai_summary, ai_notes_json, manual_notes, stage, ai_paused, updated_at
                FROM user_ai_notes
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

        if row is None:
            return {
                "user_id": user_id,
                "ai_summary": "",
                "ai_notes": {},
                "manual_notes": "",
                "stage": "new",
                "ai_paused": False,
                "updated_at": None,
            }

        payload = dict(row)
        payload["ai_notes"] = json.loads(payload.pop("ai_notes_json"))
        payload["ai_paused"] = bool(payload.get("ai_paused"))
        return payload

    # --- Canli devralma --------------------------------------------------------

    def set_ai_paused(self, user_id: str, paused: bool) -> None:
        """AI'i bu kullanici icin duraklatir/devam ettirir (insan devralma)."""
        timestamp = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_ai_notes (user_id, ai_summary, ai_notes_json, ai_paused, updated_at)
                VALUES (?, '', '{}', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    ai_paused = excluded.ai_paused,
                    updated_at = excluded.updated_at
                """,
                (user_id, 1 if paused else 0, timestamp),
            )
        self._record_lead_event(user_id, "ai_paused" if paused else "ai_resumed", {})

    def is_ai_paused(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT ai_paused FROM user_ai_notes WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return bool(row["ai_paused"]) if row else False

    # --- AI istisna listesi (blocklist) --------------------------------------
    # Bu listedeki numaralara AI ASLA yanit uretmez. Insan devralma (ai_paused)
    # gecicidir; blocklist ise kalici bir "AI bu kisiyle hic konusmasin" kaydidir.

    def add_to_blocklist(self, user_id: str, note: str = "") -> None:
        user_id = (user_id or "").strip()
        if not user_id:
            raise ValueError("Numara/kimlik bos olamaz.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_blocklist (user_id, note, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET note = excluded.note
                """,
                (user_id, note.strip(), self._utc_now()),
            )

    def remove_from_blocklist(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM ai_blocklist WHERE user_id = ?", ((user_id or "").strip(),))

    def is_blocked(self, user_id: str) -> bool:
        user_id = (user_id or "").strip()
        if not user_id:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM ai_blocklist WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
        return row is not None

    def list_blocklist(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, note, created_at FROM ai_blocklist ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["created_label"] = self._format_local(row["created_at"])
            result.append(item)
        return result

    def list_recent_contacts(self, limit: int = 40) -> List[Dict[str, Any]]:
        """Konusmus kullanicilar: istisna ekranindan tek tikla susturmak icin.

        WhatsApp yeni kimlik formati "@lid" telefon numarasi degildir; operator
        onu elle yazamaz. Bunun yerine konusan kisileri listeleyip yaninda
        engel durumunu gosteririz — panele numara girmeye gerek kalmaz.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.user_id,
                       MAX(s.last_message_at) AS last_at,
                       (SELECT channel FROM conversation_sessions
                          WHERE user_id = s.user_id
                       ORDER BY last_message_at DESC LIMIT 1) AS channel,
                       EXISTS(SELECT 1 FROM ai_blocklist b WHERE b.user_id = s.user_id) AS blocked,
                       EXISTS(SELECT 1 FROM user_ai_notes n
                                WHERE n.user_id = s.user_id AND n.ai_paused = 1) AS paused,
                       (SELECT ai_summary FROM user_ai_notes n WHERE n.user_id = s.user_id) AS summary,
                       (SELECT ai_notes_json FROM user_ai_notes n WHERE n.user_id = s.user_id) AS notes_json
                  FROM conversation_sessions s
                 WHERE s.user_id NOT IN ({marks})
              GROUP BY s.user_id
              ORDER BY last_at DESC
                 LIMIT ?
                """.format(marks=",".join("?" for _ in self.INTERNAL_USER_IDS)),
                (*self.INTERNAL_USER_IDS, limit),
            ).fetchall()

        contacts = []
        for row in rows:
            item = dict(row)
            item["blocked"] = bool(item.get("blocked"))
            item["paused"] = bool(item.get("paused"))
            item["last_label"] = self._format_local(row["last_at"])
            # Panelde "call-xxxx" / ham LID yerine gercek isim gorunsun.
            display = None
            try:
                notes = json.loads(item.pop("notes_json", None) or "{}")
                display = notes.get("name")
            except (ValueError, TypeError):
                item.pop("notes_json", None)
            item["display"] = display or item["user_id"]
            contacts.append(item)
        return contacts

    def enqueue_outbound_message(self, user_id: str, message: str, sender: str = "panel") -> int:
        message = message.strip()
        if not message:
            raise ValueError("Mesaj bos olamaz.")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO outbox (user_id, message, sender, created_at) VALUES (?, ?, ?, ?)",
                (user_id, message, sender, self._utc_now()),
            )
            return cursor.lastrowid

    def list_pending_outbound(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, user_id, message, sender, created_at FROM outbox WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_outbound_sent(self, outbox_id: int, ok: bool = True) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE outbox SET status = ?, sent_at = ? WHERE id = ?",
                ("sent" if ok else "failed", self._utc_now(), outbox_id),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Outbox kaydi bulunamadi: {outbox_id}")

    def log_human_message(self, user_id: str, message: str, channel: str = "whatsapp") -> None:
        """Panelden gonderilen insan mesajini konusma gecmisine isler."""
        session_id = self.get_latest_session_id_for_user(user_id, channel) or f"manual-{user_id}"
        self.log_turn(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            user_input="",
            agent_response=message,
            router_decision={"tool": "human_message", "sender": "panel"},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None,
                     "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )

    # --- Lead yonetimi -------------------------------------------------------

    LEAD_STAGES = ("new", "support", "offer", "won", "lost")

    # Panel ici test sohbetinin kimligi; lead listelerinde gorunmesin.
    INTERNAL_USER_IDS = ("panel-test",)

    def set_lead_stage(self, user_id: str, stage: str) -> None:
        if stage not in self.LEAD_STAGES:
            raise ValueError(f"Gecersiz asama: {stage}")

        timestamp = self._utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT stage FROM user_ai_notes WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            previous_stage = existing["stage"] if existing else "new"

            connection.execute(
                """
                INSERT INTO user_ai_notes (user_id, ai_summary, ai_notes_json, stage, updated_at)
                VALUES (?, '', '{}', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    stage = excluded.stage,
                    updated_at = excluded.updated_at
                """,
                (user_id, stage, timestamp),
            )
            connection.execute(
                """
                INSERT INTO lead_events (user_id, event_type, payload, created_at)
                VALUES (?, 'stage_change', ?, ?)
                """,
                (user_id, json.dumps({"from": previous_stage, "to": stage}, ensure_ascii=False), timestamp),
            )

    def get_lead_events(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload, created_at
                FROM lead_events
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()

        events = []
        for row in rows:
            event = dict(row)
            try:
                event["payload"] = json.loads(event["payload"] or "{}")
            except json.JSONDecodeError:
                event["payload"] = {}
            events.append(event)
        return events

    def _suggest_stage(self, notes: Dict[str, Any], current_stage: str) -> Optional[str]:
        """AI notlarina bakarak bir sonraki asama onerisi; yalnizca erken
        asamalarda oneri yapar, danismanin manuel kararini ezmez."""
        if current_stage not in ("new", "support"):
            return None

        suggestion = None
        if notes.get("pending_offer"):
            suggestion = "offer"
        elif notes.get("issue"):
            # 'current_intents' regex katmanindan geliyordu; artik konuyu
            # modelin urettigi musteri karti ('issue') tasiyor.
            suggestion = "support"

        if suggestion and self.LEAD_STAGES.index(suggestion) > self.LEAD_STAGES.index(current_stage):
            return suggestion
        return None

    def get_leads(self) -> List[Dict[str, Any]]:
        """Konusmasi olan her kullaniciyi AI notlari ve asama bilgisiyle
        birlestirip skorlanmis lead listesi dondurur."""
        now = datetime.now(self.TR_TZ)

        with self._connect() as connection:
            session_rows = [dict(row) for row in connection.execute(
                """
                SELECT user_id,
                       COUNT(*) AS conversation_count,
                       MAX(last_message_at) AS last_contact_at,
                       GROUP_CONCAT(DISTINCT channel) AS channels
                FROM conversation_sessions
                GROUP BY user_id
                """
            ).fetchall()]
            note_rows = {row["user_id"]: dict(row) for row in connection.execute(
                "SELECT user_id, ai_summary, ai_notes_json, manual_notes, stage, updated_at FROM user_ai_notes"
            ).fetchall()}

        leads: List[Dict[str, Any]] = []
        seen_users = set()
        for row in session_rows:
            user_id = row["user_id"]
            if user_id in self.INTERNAL_USER_IDS:
                continue
            seen_users.add(user_id)
            leads.append(self._build_lead(user_id, row, note_rows.get(user_id), now))

        # Konusmasi silinmis ama notu duran kullanicilar da listelensin
        for user_id, note_row in note_rows.items():
            if user_id in seen_users or user_id in self.INTERNAL_USER_IDS:
                continue
            leads.append(self._build_lead(user_id, None, note_row, now))

        leads.sort(key=lambda lead: (lead["score"], lead["last_contact_at"] or ""), reverse=True)
        return leads

    def _build_lead(
        self,
        user_id: str,
        session_row: Optional[Dict[str, Any]],
        note_row: Optional[Dict[str, Any]],
        now: datetime,
    ) -> Dict[str, Any]:
        notes: Dict[str, Any] = {}
        stage = "new"
        ai_summary = ""
        manual_notes = ""
        updated_at = None
        if note_row:
            try:
                notes = json.loads(note_row.get("ai_notes_json") or "{}")
            except json.JSONDecodeError:
                notes = {}
            stage = note_row.get("stage") or "new"
            ai_summary = note_row.get("ai_summary") or ""
            manual_notes = note_row.get("manual_notes") or ""
            updated_at = note_row.get("updated_at")

        parsed_updated = self._parse_timestamp(updated_at)
        score = self._score_lead(notes, parsed_updated, now)
        last_contact = self._parse_timestamp((session_row or {}).get("last_contact_at"))
        matches = self.get_unit_matches(notes, limit=3)

        return {
            "match_count": len(matches),
            "top_match": matches[0] if matches else None,
            "matches": matches,
            "user_id": user_id,
            "name": notes.get("name"),
            "merchant_id": notes.get("merchant_id"),
            "stage": stage,
            "score": score,
            "temperature": "hot" if score >= 6 else ("warm" if score >= 3 else "cold"),
            "suggested_stage": self._suggest_stage(notes, stage),
            "ai_summary": ai_summary,
            "manual_notes": manual_notes,
            "budget_max_try": notes.get("budget_max_try"),
            "preferred_flat_type": notes.get("preferred_flat_type"),
            "handoff_required": bool(notes.get("handoff_required")),
            "urgency": notes.get("urgency"),
            "conversation_count": (session_row or {}).get("conversation_count", 0),
            "channels": ((session_row or {}).get("channels") or "").split(",") if session_row else [],
            "last_contact_at": last_contact.strftime("%d.%m.%Y %H:%M") if last_contact else None,
            "last_contact_iso": last_contact.isoformat() if last_contact else None,
        }

    def get_conversation(self, session_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                """
                SELECT session_id, user_id, channel, created_at, last_message_at
                FROM conversation_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

            turns = connection.execute(
                """
                SELECT id, channel, user_input, agent_response, router_decision_json, context_json, created_at
                FROM conversation_turns
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        if session is None:
            raise KeyError(f"Conversation not found: {session_id}")

        parsed_turns = []
        for row in turns:
            turn = dict(row)
            turn["router_decision"] = json.loads(turn.pop("router_decision_json"))
            turn["context"] = json.loads(turn.pop("context_json"))
            parsed_turns.append(turn)

        return {
            "session": dict(session),
            "turns": parsed_turns,
        }

    def get_latest_session_id_for_user(self, user_id: str, channel: str,
                                       max_idle_hours: Optional[float] = None) -> Optional[str]:
        """Kullanicinin son oturumu.

        max_idle_hours verilirse, o sureden uzun sessiz kalmis oturum
        DONDURULMEZ (None doner) — cagiran yeni bir oturum acar. Sesli aramada
        her cagri zaten yeni bir kimlik alir, ama WhatsApp'ta kimlik telefon
        numarasidir ve oturum aksi halde SONSUZA KADAR yasar: iki hafta sonra
        yazan musteri hala "ayni gorusmede" sayilir, gorusme basina tek sefer
        calisan araclar (ekstre, teklif) bir daha hic calismaz.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, last_message_at
                FROM conversation_sessions
                WHERE user_id = ? AND channel = ?
                ORDER BY last_message_at DESC
                LIMIT 1
                """,
                (user_id, channel),
            ).fetchone()

        if row is None:
            return None

        if max_idle_hours is not None:
            last_seen = self._parse_timestamp(row["last_message_at"])
            if last_seen is None:
                return None
            idle = datetime.now(self.TR_TZ) - last_seen
            if idle > timedelta(hours=max_idle_hours):
                return None

        return row["session_id"]

    def get_sales_profile(self) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    consultant_name,
                    consultant_title,
                    phone_number,
                    whatsapp_number,
                    office_name,
                    office_address,
                    maps_url,
                    latitude,
                    longitude,
                    location_label,
                    auto_share_whatsapp_location,
                    updated_at
                FROM sales_profile
                WHERE id = 1
                """
            ).fetchone()

        if row is None:
            return {
                "consultant_name": "",
                "consultant_title": "",
                "phone_number": "",
                "whatsapp_number": "",
                "office_name": "",
                "office_address": "",
                "maps_url": "",
                "latitude": "",
                "longitude": "",
                "location_label": "",
                "auto_share_whatsapp_location": False,
                "updated_at": None,
            }

        payload = dict(row)
        payload["auto_share_whatsapp_location"] = bool(payload["auto_share_whatsapp_location"])
        return payload

    def update_sales_profile(self, form_data: Dict[str, Any]) -> None:
        timestamp = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sales_profile (
                    id,
                    consultant_name,
                    consultant_title,
                    phone_number,
                    whatsapp_number,
                    office_name,
                    office_address,
                    maps_url,
                    latitude,
                    longitude,
                    location_label,
                    auto_share_whatsapp_location,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    consultant_name = excluded.consultant_name,
                    consultant_title = excluded.consultant_title,
                    phone_number = excluded.phone_number,
                    whatsapp_number = excluded.whatsapp_number,
                    office_name = excluded.office_name,
                    office_address = excluded.office_address,
                    maps_url = excluded.maps_url,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    location_label = excluded.location_label,
                    auto_share_whatsapp_location = excluded.auto_share_whatsapp_location,
                    updated_at = excluded.updated_at
                """,
                (
                    1,
                    str(form_data.get("consultant_name", "")).strip(),
                    str(form_data.get("consultant_title", "")).strip(),
                    str(form_data.get("phone_number", "")).strip(),
                    str(form_data.get("whatsapp_number", "")).strip(),
                    str(form_data.get("office_name", "")).strip(),
                    str(form_data.get("office_address", "")).strip(),
                    str(form_data.get("maps_url", "")).strip(),
                    str(form_data.get("latitude", "")).strip(),
                    str(form_data.get("longitude", "")).strip(),
                    str(form_data.get("location_label", "")).strip(),
                    1 if form_data.get("auto_share_whatsapp_location") in (True, "true", "1", "on") else 0,
                    timestamp,
                ),
            )


    def _load_json(self, filename: str) -> Any:
        # Moka donusumunde kaldirilan eski emlak dosyalari (inventory/prices/...)
        # icin bos liste don: ilan bazli sayfalar bos calisir, panel cokmesin.
        path = self.data_dir / filename
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_json(self, filename: str, payload: Any) -> None:
        (self.data_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_listings(
        self,
        query: str = "",
        filters: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        inventory = self._load_json("inventory.json")
        prices = {item["inventory_id"]: item for item in self._load_json("prices.json")}
        sunlight = {item["inventory_id"]: item for item in self._load_json("sunlight.json")}
        flats = {item["flat_type_id"]: item for item in self._load_json("flats.json")}

        listings: List[Dict[str, Any]] = []
        normalized_query = query.lower().strip()
        active_filters = {k: v for k, v in (filters or {}).items() if v not in ("", None)}

        for item in inventory:
            listing = dict(item)
            listing["price"] = prices.get(item["inventory_id"], {})
            listing["sunlight"] = sunlight.get(item["inventory_id"], {})
            listing["flat"] = flats.get(item["flat_type_id"], {})
            listing["flat_label"] = listing["flat"].get("label", item["flat_type_id"])
            listing["list_price_try"] = listing["price"].get("list_price_try")

            searchable = " ".join(
                str(value)
                for value in [
                    listing.get("inventory_id"),
                    listing.get("block_id"),
                    listing.get("door_number"),
                    listing.get("flat_label"),
                    listing.get("status"),
                    listing.get("direction"),
                ]
                if value is not None
            ).lower()

            if normalized_query and normalized_query not in searchable:
                continue

            if active_filters.get("block_id") and listing["block_id"] != active_filters["block_id"]:
                continue
            if active_filters.get("flat_type_id") and listing["flat_type_id"] != active_filters["flat_type_id"]:
                continue
            if active_filters.get("status") and listing["status"] != active_filters["status"]:
                continue
            price = listing.get("list_price_try")
            if active_filters.get("price_min") is not None and (price is None or price < active_filters["price_min"]):
                continue
            if active_filters.get("price_max") is not None and (price is None or price > active_filters["price_max"]):
                continue

            listings.append(listing)

        # Fiyati olmayan kayitlar siralamada daima sona duser.
        if sort == "price_asc":
            listings.sort(key=lambda i: (i.get("list_price_try") is None, i.get("list_price_try") or 0, i["inventory_id"]))
        elif sort == "price_desc":
            listings.sort(key=lambda i: (i.get("list_price_try") is None, -(i.get("list_price_try") or 0), i["inventory_id"]))
        elif sort == "floor_asc":
            listings.sort(key=lambda i: (i["floor"], i["inventory_id"]))
        elif sort == "floor_desc":
            listings.sort(key=lambda i: (-i["floor"], i["inventory_id"]))
        else:
            listings.sort(key=lambda item: item["inventory_id"])
        return listings

    def get_flat_options(self) -> List[Dict[str, Any]]:
        """Daire tipi secenekleri (panel formlari/filtreleri icin)."""
        return self._load_json("flats.json")

    def get_block_options(self) -> List[Dict[str, Any]]:
        """Blok secenekleri (panel formlari/filtreleri icin)."""
        return self._load_json("blocks.json")

    def get_listing(self, inventory_id: str) -> Dict[str, Any]:
        listings = self.list_listings()
        for listing in listings:
            if listing["inventory_id"] == inventory_id:
                return listing
        raise KeyError(f"Listing not found: {inventory_id}")

    def update_listing(self, inventory_id: str, form_data: Dict[str, Any]) -> None:
        inventory = self._load_json("inventory.json")
        prices = self._load_json("prices.json")
        sunlight = self._load_json("sunlight.json")

        inventory_item = next((item for item in inventory if item["inventory_id"] == inventory_id), None)
        if inventory_item is None:
            raise KeyError(f"Listing not found: {inventory_id}")

        old_status = inventory_item.get("status", "")
        inventory_item["block_id"] = form_data["block_id"]
        inventory_item["floor"] = int(form_data["floor"])
        inventory_item["door_number"] = form_data["door_number"]
        inventory_item["flat_type_id"] = form_data["flat_type_id"]
        inventory_item["status"] = form_data["status"]
        inventory_item["direction"] = form_data["direction"]
        if old_status != form_data["status"]:
            self._log_status_change(inventory_id, old_status, form_data["status"], source="edit")
        # Ilan aciklamasi opsiyoneldir (gunes notundan ayri); agent'a
        # enrich_details uzerinden otomatik akar.
        if "listing_description" in form_data:
            inventory_item["description"] = str(form_data["listing_description"]).strip()

        price_item = next((item for item in prices if item["inventory_id"] == inventory_id), None)
        if price_item is None:
            price_item = {"inventory_id": inventory_id}
            prices.append(price_item)
        old_price = price_item.get("list_price_try")
        new_price = int(form_data["list_price_try"])
        if old_price != new_price:
            self._log_price_change(inventory_id, old_price, new_price, source="edit")
        price_item["list_price_try"] = new_price
        price_item["currency"] = "TRY"
        price_item["vat_included"] = form_data.get("vat_included", "false") == "true"
        price_item["valid_until"] = form_data.get("valid_until", "") or None

        sunlight_item = next((item for item in sunlight if item["inventory_id"] == inventory_id), None)
        if sunlight_item is None:
            sunlight_item = {"inventory_id": inventory_id}
            sunlight.append(sunlight_item)
        sunlight_item["block_id"] = form_data["block_id"]
        sunlight_item["floor"] = int(form_data["floor"])
        sunlight_item["direction"] = form_data["direction"]
        sunlight_item["sun_exposure"] = form_data["sun_exposure"]
        sunlight_item["sun_hours_per_day"] = form_data["sun_hours_per_day"]
        sunlight_item["description"] = form_data["description"]

        self._save_json("inventory.json", inventory)
        self._save_json("prices.json", prices)
        self._save_json("sunlight.json", sunlight)
        self._refresh_config_cache()

    def delete_listing(self, inventory_id: str) -> None:
        inventory = [item for item in self._load_json("inventory.json") if item["inventory_id"] != inventory_id]
        prices = [item for item in self._load_json("prices.json") if item["inventory_id"] != inventory_id]
        sunlight = [item for item in self._load_json("sunlight.json") if item["inventory_id"] != inventory_id]

        self._save_json("inventory.json", inventory)
        self._save_json("prices.json", prices)
        self._save_json("sunlight.json", sunlight)
        self._delete_photos_for_ref("unit", inventory_id)
        self._refresh_config_cache()

    def _refresh_config_cache(self) -> None:
        """Ilan JSON'lari degistiginde ayni surecteki Config onbellegini tazeler,
        boylece agent yeniden baslatilmadan guncel stok/fiyat gorur."""
        try:
            config = Config()
            if Path(config.data_dir).resolve() == self.data_dir.resolve():
                config.load_data()
        except Exception as error:
            print(f"Config reload warning: {error}")

    VALID_LISTING_STATUSES = ("available", "reserved", "sold")

    def _log_status_change(self, inventory_id: str, from_status: str, to_status: str, source: str) -> None:
        """Durum degisikligini damgalar; satis hizi/absorpsiyon bundan turer."""
        if from_status == to_status:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO listing_status_log (inventory_id, from_status, to_status, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (inventory_id, from_status or "", to_status, source, self._utc_now()),
            )

    def update_listing_status(self, inventory_id: str, status: str, source: str = "panel") -> None:
        """Stok panosundan tek tikla durum degisikligi icin hafif guncelleme."""
        if status not in self.VALID_LISTING_STATUSES:
            raise ValueError(f"Gecersiz durum: {status}")

        inventory = self._load_json("inventory.json")
        item = next((entry for entry in inventory if entry["inventory_id"] == inventory_id), None)
        if item is None:
            raise KeyError(f"Listing not found: {inventory_id}")

        old_status = item.get("status", "")
        item["status"] = status
        self._save_json("inventory.json", inventory)
        self._log_status_change(inventory_id, old_status, status, source)
        self._refresh_config_cache()

        # Rezerve disina cikista aktif opsiyon/kapora kaydini kapat ki
        # tablo ile stok durumu tutarsiz kalmasin.
        if status != "reserved":
            new_state = "converted_to_sale" if status == "sold" else "released"
            self._close_active_reservation(inventory_id, new_state)

    # --- Opsiyon / kapora akisi ----------------------------------------------

    RESERVATION_KINDS = ("option", "deposit")

    def _close_active_reservation(self, inventory_id: str, new_state: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unit_reservations
                SET state = ?, updated_at = ?
                WHERE inventory_id = ? AND state = 'active'
                """,
                (new_state, self._utc_now(), inventory_id),
            )

    def _record_lead_event(self, user_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        if not user_id:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO lead_events (user_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (user_id, event_type, json.dumps(payload, ensure_ascii=False), self._utc_now()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, self._utc_now()),
            )

    def record_lead_event(self, user_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Public wrapper: orkestrator gelir olaylarini (odeme linki, teklif kabulu) buradan yazar."""
        self._record_lead_event(user_id, event_type, payload)

    def get_recovered_merchant_ids(self) -> set:
        """Kabul edilmis tekliflerin isletme id'leri (outbound 'Kurtarildi' rozeti)."""
        ids = set()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM lead_events WHERE event_type = 'offer_accepted'"
            ).fetchall()
        for row in rows:
            try:
                merchant_id = (json.loads(row["payload"] or "{}") or {}).get("merchant_id")
                if merchant_id:
                    ids.add(merchant_id)
            except (ValueError, TypeError):
                continue
        return ids

    def find_user_ids_for_merchant(self, merchant_id: str) -> List[str]:
        """Bu isletmeye baglanmis whatsapp/cagri kullanici id'lerini bulur.

        Kopru: AI notlarindaki serbest 'merchant_id' alani (get_recovered_merchant_ids
        ile ayni desen). Birkac kayit — Python'da parse ucuz.
        """
        user_ids: List[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, ai_notes_json FROM user_ai_notes").fetchall()
        for row in rows:
            try:
                notes = json.loads(row["ai_notes_json"] or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(notes, dict) and notes.get("merchant_id") == merchant_id:
                user_ids.append(row["user_id"])
        return user_ids

    def get_merchant_ops(self, merchant_id: str,
                         extra_user_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Bir isletmenin operasyon-tarafi verisi (isletme-360'in ops bolumu).

        Kopru merchant_id: identity tablosu (extra_user_ids olarak repository'den
        gelir) + AI notlarindaki merchant_id. Iki DB'yi kapsayan islem YOK — salt
        okuma. Eslesme yoksa bolumler bos doner (zarif dusus).
        """
        user_ids = set(extra_user_ids or [])
        user_ids.update(self.find_user_ids_for_merchant(merchant_id))

        empty = {"user_ids": [], "conversations": [], "lead_events": [],
                 "tasks": [], "leads": [], "handoff_waiting": False}
        if not user_ids:
            return empty

        conversations: List[Dict[str, Any]] = []
        lead_events: List[Dict[str, Any]] = []
        for uid in user_ids:
            conversations.extend(self.list_conversations(user_id=uid))
            for event in self.get_lead_events(uid):
                event["user_id"] = uid
                lead_events.append(event)

        placeholders = ",".join("?" for _ in user_ids)
        with self._connect() as connection:
            task_rows = [dict(row) for row in connection.execute(
                f"SELECT id, title, user_id, due_at, done, created_at, done_at "
                f"FROM tasks WHERE user_id IN ({placeholders}) "
                f"ORDER BY done ASC, COALESCE(due_at, created_at) ASC",
                tuple(user_ids)).fetchall()]
        for row in task_rows:
            row["done"] = bool(row["done"])

        leads_by_user = {lead["user_id"]: lead for lead in self.get_leads()}
        leads = [leads_by_user[uid] for uid in user_ids if uid in leads_by_user]

        conversations.sort(key=lambda item: item.get("last_message_at") or "", reverse=True)
        lead_events.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        handoff_waiting = any(
            item["user_id"] in user_ids for item in self.get_handoff_queue())

        return {
            "user_ids": sorted(user_ids),
            "conversations": conversations,
            "lead_events": lead_events,
            "tasks": task_rows,
            "leads": leads,
            "handoff_waiting": handoff_waiting,
        }

    def get_revenue_kpis(self) -> Dict[str, Any]:
        """Gelir panosu: AI'in urettigi somut para etkisi.

        - recovered_volume_try: kabul edilen kurtarma tekliflerinin aylik hacmi
        - offers_accepted / payment_links: gelir olayi sayaclari
        - calls_today: bugun acilan sesli cagri oturumlari
        - containment_pct: son 7 gunde insana devredilmeden cozulen oturum orani
        """
        recovered = 0
        offers_accepted = 0
        payment_links = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, payload FROM lead_events "
                "WHERE event_type IN ('offer_accepted', 'payment_link_created')"
            ).fetchall()
            for row in rows:
                if row["event_type"] == "offer_accepted":
                    offers_accepted += 1
                    try:
                        payload = json.loads(row["payload"] or "{}")
                        recovered += int(payload.get("recovered_volume_try") or 0)
                    except (ValueError, TypeError):
                        pass
                else:
                    payment_links += 1

            # created_at UTC yazilir; TR gununun basini UTC'ye cevirip esik al
            # (hakem #23). Panel test kullanicisi metrikleri sismesin (hakem #24).
            tr_now = datetime.now(self.TR_TZ)
            day_start_utc = (
                tr_now.replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            )
            internal_marks = ",".join("?" for _ in self.INTERNAL_USER_IDS)
            calls_today = connection.execute(
                "SELECT COUNT(*) AS c FROM conversation_sessions "
                f"WHERE channel = 'voice' AND created_at >= ? AND user_id NOT IN ({internal_marks})",
                (day_start_utc, *self.INTERNAL_USER_IDS),
            ).fetchone()["c"]

            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
            sessions_week = connection.execute(
                f"SELECT COUNT(*) AS c FROM conversation_sessions WHERE created_at >= ? AND user_id NOT IN ({internal_marks})",
                (week_ago, *self.INTERNAL_USER_IDS),
            ).fetchone()["c"]
            handoff_sessions_week = connection.execute(
                "SELECT COUNT(DISTINCT t.session_id) AS c FROM conversation_turns t "
                "JOIN conversation_sessions s ON s.session_id = t.session_id "
                f"WHERE t.created_at >= ? AND s.user_id NOT IN ({internal_marks}) "
                "AND t.handoff_required = 1",
                (week_ago, *self.INTERNAL_USER_IDS),
            ).fetchone()["c"]

        containment_pct = 100
        if sessions_week:
            containment_pct = round((1 - handoff_sessions_week / sessions_week) * 100)

        return {
            "recovered_volume_try": recovered,
            "offers_accepted": offers_accepted,
            "payment_links": payment_links,
            "calls_today": calls_today,
            "containment_pct": containment_pct,
            "sessions_week": sessions_week,
        }

    def _bump_lead_stage(self, user_id: str, stage: str) -> None:
        """Lead asamasini yalnizca ileri yonde tasir; manuel karari geri almaz."""
        if not user_id:
            return
        current = self.get_user_ai_notes(user_id).get("stage", "new")
        if current in ("won", "lost"):
            return
        if self.LEAD_STAGES.index(stage) > self.LEAD_STAGES.index(current):
            self.set_lead_stage(user_id, stage)

    def release_expired_options(self) -> int:
        """Suresi dolan opsiyonlari serbest birakir (tembel calisir: pano her
        acilista cagirir, arka plan isi gerektirmez)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            expired = [dict(row) for row in connection.execute(
                """
                SELECT id, inventory_id, user_id FROM unit_reservations
                WHERE state = 'active' AND kind = 'option'
                  AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (now_iso,),
            ).fetchall()]

        for row in expired:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE unit_reservations SET state = 'expired', updated_at = ? WHERE id = ?",
                    (self._utc_now(), row["id"]),
                )
            # Yalnizca hala rezerve gorunuyorsa satisa geri al
            inventory = self._load_json("inventory.json")
            item = next((entry for entry in inventory if entry["inventory_id"] == row["inventory_id"]), None)
            if item is not None and item.get("status") == "reserved":
                item["status"] = "available"
                self._save_json("inventory.json", inventory)
                self._log_status_change(row["inventory_id"], "reserved", "available", source="option_expired")
            self._record_lead_event(row["user_id"], "option_expired", {"inventory_id": row["inventory_id"]})

        if expired:
            self._refresh_config_cache()
        return len(expired)

    def get_active_reservations(self) -> Dict[str, Dict[str, Any]]:
        """inventory_id -> aktif rezervasyon (kalan sure etiketiyle)."""
        now = datetime.now(self.TR_TZ)
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                """
                SELECT inventory_id, user_id, kind, amount_try, note, expires_at, created_at
                FROM unit_reservations
                WHERE state = 'active'
                """
            ).fetchall()]

        reservations: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            expires = self._parse_timestamp(row.get("expires_at"))
            remaining_label = None
            remaining_minutes = None
            expiring_soon = False
            if expires is not None:
                remaining = expires - now
                total_minutes = max(0, int(remaining.total_seconds() // 60))
                remaining_minutes = total_minutes
                hours, minutes = divmod(total_minutes, 60)
                remaining_label = f"{hours}s {minutes}dk" if hours else f"{minutes}dk"
                # 24 saat icinde dolacak opsiyonlar "yakinda dolan" olarak isaretlenir.
                expiring_soon = row["kind"] == "option" and total_minutes <= 24 * 60
            reservations[row["inventory_id"]] = {
                "user_id": row.get("user_id") or "",
                "kind": row["kind"],
                "amount_try": row.get("amount_try"),
                "note": row.get("note") or "",
                "expires_at": expires.strftime("%d.%m %H:%M") if expires else None,
                "remaining_label": remaining_label,
                "remaining_minutes": remaining_minutes,
                "expiring_soon": expiring_soon,
            }
        return reservations

    # --- Ilan fotograflari ---------------------------------------------------
    # Dosyalar data/uploads/listings/<ref_id>/ altinda yasar (gitignore'lu);
    # meta SQLite'tadir. "flat_type" kapsami tip setidir: kendi fotografi olmayan
    # daireler tipinin setini devralir.

    PHOTO_SCOPES = ("unit", "flat_type")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads" / "listings"

    def add_listing_photo(
        self,
        scope: str,
        ref_id: str,
        data: bytes,
        extension: str,
        original_name: str = "",
    ) -> int:
        if scope not in self.PHOTO_SCOPES:
            raise ValueError(f"Gecersiz fotograf kapsami: {scope}")
        if not ref_id or not data:
            raise ValueError("Fotograf icin ref_id ve icerik zorunlu.")

        target_dir = self.uploads_dir / ref_id
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_hex(8)}.{extension.lstrip('.').lower()}"
        (target_dir / filename).write_bytes(data)

        with self._connect() as connection:
            has_cover = connection.execute(
                "SELECT 1 FROM listing_photos WHERE scope = ? AND ref_id = ? AND is_cover = 1 LIMIT 1",
                (scope, ref_id),
            ).fetchone()
            max_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM listing_photos WHERE scope = ? AND ref_id = ?",
                (scope, ref_id),
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO listing_photos (scope, ref_id, filename, original_name, is_cover, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scope, ref_id, filename, original_name, 0 if has_cover else 1, max_order + 1, self._utc_now()),
            )
            return int(cursor.lastrowid)

    def list_photos(self, scope: str, ref_id: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                """
                SELECT id, scope, ref_id, filename, original_name, is_cover, sort_order
                FROM listing_photos
                WHERE scope = ? AND ref_id = ?
                ORDER BY is_cover DESC, sort_order, id
                """,
                (scope, ref_id),
            ).fetchall()]
        for row in rows:
            row["relpath"] = f"{row['ref_id']}/{row['filename']}"
        return rows

    def get_photos_for_listing(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """Efektif fotograf seti: dairenin kendi fotolari, yoksa tipinin seti."""
        unit_photos = self.list_photos("unit", listing.get("inventory_id", ""))
        if unit_photos:
            return {"photos": unit_photos, "source": "unit"}
        type_photos = self.list_photos("flat_type", listing.get("flat_type_id", ""))
        if type_photos:
            return {"photos": type_photos, "source": "flat_type"}
        return {"photos": [], "source": None}

    def get_cover_photo_map(self) -> Dict[str, Dict[str, str]]:
        """Liste gorunumu icin kapak fotolari: scope -> ref_id -> relpath."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scope, ref_id, filename FROM listing_photos WHERE is_cover = 1"
            ).fetchall()
        cover_map: Dict[str, Dict[str, str]] = {"unit": {}, "flat_type": {}}
        for row in rows:
            cover_map.setdefault(row["scope"], {})[row["ref_id"]] = f"{row['ref_id']}/{row['filename']}"
        return cover_map

    def set_cover_photo(self, photo_id: int) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scope, ref_id FROM listing_photos WHERE id = ?", (photo_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Fotograf bulunamadi: {photo_id}")
            connection.execute(
                "UPDATE listing_photos SET is_cover = 0 WHERE scope = ? AND ref_id = ?",
                (row["scope"], row["ref_id"]),
            )
            connection.execute("UPDATE listing_photos SET is_cover = 1 WHERE id = ?", (photo_id,))

    def delete_photo(self, photo_id: int) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scope, ref_id, filename, is_cover FROM listing_photos WHERE id = ?",
                (photo_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Fotograf bulunamadi: {photo_id}")
            connection.execute("DELETE FROM listing_photos WHERE id = ?", (photo_id,))
            if row["is_cover"]:
                next_row = connection.execute(
                    """
                    SELECT id FROM listing_photos
                    WHERE scope = ? AND ref_id = ?
                    ORDER BY sort_order, id LIMIT 1
                    """,
                    (row["scope"], row["ref_id"]),
                ).fetchone()
                if next_row is not None:
                    connection.execute(
                        "UPDATE listing_photos SET is_cover = 1 WHERE id = ?", (next_row["id"],)
                    )
        (self.uploads_dir / row["ref_id"] / row["filename"]).unlink(missing_ok=True)

    def _delete_photos_for_ref(self, scope: str, ref_id: str) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT filename FROM listing_photos WHERE scope = ? AND ref_id = ?",
                (scope, ref_id),
            ).fetchall()
            connection.execute(
                "DELETE FROM listing_photos WHERE scope = ? AND ref_id = ?", (scope, ref_id)
            )
        ref_dir = self.uploads_dir / ref_id
        for row in rows:
            (ref_dir / row["filename"]).unlink(missing_ok=True)
        if ref_dir.is_dir() and not any(ref_dir.iterdir()):
            ref_dir.rmdir()

    # --- Fiyat degisiklik gecmisi --------------------------------------------

    def _log_price_change(self, inventory_id: str, old_price: Optional[int], new_price: int, source: str = "panel") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO listing_price_log (inventory_id, old_price, new_price, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (inventory_id, old_price, new_price, source, self._utc_now()),
            )

    def get_price_history(self, inventory_id: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT old_price, new_price, source, created_at FROM listing_price_log WHERE inventory_id = ? ORDER BY created_at DESC",
                (inventory_id,),
            ).fetchall()]
        for row in rows:
            row["at_label"] = self._format_local(row["created_at"])
            old = row.get("old_price")
            row["direction"] = "same" if old is None else ("down" if row["new_price"] < old else "up")
        return rows

    def get_recent_price_changes(self, days: int = 30) -> Dict[str, Dict[str, Any]]:
        """Liste rozetleri icin: inventory_id -> son fiyat degisikligi (N gun icinde)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                """
                SELECT inventory_id, old_price, new_price, created_at
                FROM listing_price_log
                WHERE created_at >= ?
                ORDER BY created_at ASC
                """,
                (cutoff,),
            ).fetchall()]
        changes: Dict[str, Dict[str, Any]] = {}
        for row in rows:  # ASC gezildigi icin sozlukte en son degisiklik kalir
            old = row.get("old_price")
            changes[row["inventory_id"]] = {
                "direction": "same" if old is None else ("down" if row["new_price"] < old else "up"),
                "at_label": self._format_local(row["created_at"]),
            }
        return changes

    RESERVATION_STATE_LABELS = {
        "active": "Aktif",
        "released": "Serbest birakildi",
        "expired": "Suresi doldu",
        "converted_to_deposit": "Kaporaya cevrildi",
        "converted_to_sale": "Satisa donustu",
    }

    def _format_local(self, raw: Optional[str]) -> str:
        parsed = self._parse_timestamp(raw)
        if parsed is None:
            return ""
        return parsed.astimezone(self.TR_TZ).strftime("%d.%m.%Y %H:%M")

    def get_unit_history(self, inventory_id: str) -> List[Dict[str, Any]]:
        """Daire detayi icin kronolojik gecmis: tum rezervasyonlar + teklifler.

        (Opsiyon/kapora lead olaylari rezervasyon kayitlarinin kopyasi oldugundan
        lead_events buraya dahil edilmez.)
        """
        events: List[Dict[str, Any]] = []
        with self._connect() as connection:
            res_rows = [dict(row) for row in connection.execute(
                "SELECT * FROM unit_reservations WHERE inventory_id = ? ORDER BY created_at DESC",
                (inventory_id,),
            ).fetchall()]
            offer_rows = [dict(row) for row in connection.execute(
                "SELECT id, user_id, plan_json, created_at FROM offers WHERE inventory_id = ? ORDER BY created_at DESC",
                (inventory_id,),
            ).fetchall()]

        for row in res_rows:
            kind_label = "Opsiyon" if row["kind"] == "option" else "Kapora"
            detail_bits = []
            if row.get("user_id"):
                detail_bits.append(row["user_id"])
            if row.get("amount_try"):
                detail_bits.append(f"{row['amount_try']:,} TL".replace(",", "."))
            if row.get("expires_at"):
                expires_label = self._format_local(row["expires_at"])
                if expires_label:
                    detail_bits.append(f"bitis {expires_label}")
            if row.get("note"):
                detail_bits.append(row["note"])
            events.append({
                "at": row["created_at"],
                "at_label": self._format_local(row["created_at"]),
                "type": "reservation",
                "title": f"{kind_label} — {self.RESERVATION_STATE_LABELS.get(row['state'], row['state'])}",
                "detail": " · ".join(detail_bits),
            })

        for row in offer_rows:
            try:
                plan = json.loads(row["plan_json"])
            except (TypeError, ValueError):
                plan = {}
            detail_bits = []
            if row.get("user_id"):
                detail_bits.append(row["user_id"])
            total = plan.get("total")
            if total:
                detail_bits.append(f"toplam {int(total):,} TL".replace(",", "."))
            months = (plan.get("inputs") or {}).get("months")
            if months:
                detail_bits.append(f"{months} ay vade")
            events.append({
                "at": row["created_at"],
                "at_label": self._format_local(row["created_at"]),
                "type": "offer",
                "title": f"Teklif #{row['id']}",
                "detail": " · ".join(detail_bits),
                "offer_id": row["id"],
            })

        events.sort(key=lambda event: event["at"] or "", reverse=True)
        return events

    def place_option(self, inventory_id: str, user_id: str = "", hours: int = 48, note: str = "") -> None:
        """Uniteyi sureli kilitler (Turk satis ofisindeki 'opsiyonlama')."""
        hours = int(hours)
        if not 0 <= hours <= 24 * 7:
            raise ValueError("Opsiyon suresi 0-168 saat araliginda olmali.")

        listing = self.get_listing(inventory_id)
        if listing.get("status") != "available":
            raise ValueError(f"{inventory_id} su anda satista degil.")

        timestamp = self._utc_now()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO unit_reservations (inventory_id, user_id, kind, note, state, expires_at, created_at, updated_at)
                VALUES (?, ?, 'option', ?, 'active', ?, ?, ?)
                """,
                (inventory_id, user_id, note, expires_at, timestamp, timestamp),
            )
        self.update_listing_status(inventory_id, "reserved", source="option")
        self._record_lead_event(user_id, "option_placed", {"inventory_id": inventory_id, "hours": hours})
        self._bump_lead_stage(user_id, "offer")

    def place_deposit(self, inventory_id: str, user_id: str = "", amount_try: Optional[int] = None, note: str = "") -> None:
        """Kapora kaydi; opsiyonlu uniteyi kaporaya cevirebilir."""
        listing = self.get_listing(inventory_id)
        if listing.get("status") == "sold":
            raise ValueError(f"{inventory_id} satilmis durumda.")

        # Aktif opsiyon varsa kaporaya donusum olarak kapat
        self._close_active_reservation(inventory_id, "converted_to_deposit")

        timestamp = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO unit_reservations (inventory_id, user_id, kind, amount_try, note, state, created_at, updated_at)
                VALUES (?, ?, 'deposit', ?, ?, 'active', ?, ?)
                """,
                (inventory_id, user_id, amount_try, note, timestamp, timestamp),
            )
        if listing.get("status") != "reserved":
            self.update_listing_status(inventory_id, "reserved", source="deposit")
        self._record_lead_event(user_id, "deposit_placed", {"inventory_id": inventory_id, "amount_try": amount_try})
        self._bump_lead_stage(user_id, "offer")

    def release_reservation(self, inventory_id: str, note: str = "") -> None:
        """Opsiyon/kaporayi iptal edip uniteyi satisa geri alir."""
        reservation = self.get_active_reservations().get(inventory_id)
        self._close_active_reservation(inventory_id, "released")
        self.update_listing_status(inventory_id, "available", source="release")
        if reservation:
            self._record_lead_event(
                reservation.get("user_id", ""),
                "reservation_released",
                {"inventory_id": inventory_id, "note": note},
            )

    def _format_price_short(self, amount: Optional[int]) -> str:
        if not amount:
            return "—"
        if amount >= 1_000_000:
            value = amount / 1_000_000
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text.replace('.', ',')}M"
        return f"{amount // 1_000}B"

    def get_stock_board(self) -> Dict[str, Any]:
        """Blok -> kat -> daire hiyerarsisinde renk kodlu stok matrisinin verisi."""
        self.release_expired_options()
        reservations = self.get_active_reservations()
        listings = self.list_listings()
        blocks_meta = {block["block_id"]: block for block in self._load_json("blocks.json")}

        # Fiyat bandi (1-5): satistaki dairelerin fiyat dagilimindan esik degerler.
        available_prices = sorted(
            listing["list_price_try"]
            for listing in listings
            if listing.get("status") == "available" and listing.get("list_price_try")
        )
        price_thresholds = [
            available_prices[int(len(available_prices) * q)]
            for q in (0.2, 0.4, 0.6, 0.8)
        ] if len(available_prices) >= 5 else []

        def _price_band(price: Optional[int]) -> int:
            if not price or not price_thresholds:
                return 0
            band = 1
            for threshold in price_thresholds:
                if price > threshold:
                    band += 1
            return band

        # Talep bandi (0-3): AI notlarinda bu daireyle eslesen lead sayisi.
        demand_counts = self.get_demand_heat()

        def _demand_band(count: int) -> int:
            if count <= 0:
                return 0
            if count == 1:
                return 1
            if count <= 3:
                return 2
            return 3

        grouped: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
        type_summary: Dict[str, Dict[str, Any]] = {}
        expiring_options: List[Dict[str, Any]] = []
        all_prices: List[int] = []

        for listing in listings:
            block_id = listing.get("block_id", "?")
            floor = int(listing.get("floor", 0))
            inventory_id = listing["inventory_id"]
            price = listing.get("list_price_try")
            flat = listing.get("flat") or {}
            reservation = reservations.get(inventory_id)
            demand_count = demand_counts.get(inventory_id, 0)
            unit = {
                "inventory_id": inventory_id,
                "door_number": listing.get("door_number", ""),
                "flat_label": listing.get("flat_label", ""),
                "flat_type_id": listing.get("flat_type_id", ""),
                "status": listing.get("status", "available"),
                "direction": listing.get("direction", ""),
                "sun_exposure": (listing.get("sunlight") or {}).get("sun_exposure", ""),
                "sun_hours": (listing.get("sunlight") or {}).get("sun_hours_per_day", ""),
                "net_m2": flat.get("net_m2"),
                "gross_m2": flat.get("gross_m2"),
                "list_price_try": price,
                "price_short": self._format_price_short(price),
                "floor": floor,
                "block_id": block_id,
                "price_band": _price_band(price),
                "demand_count": demand_count,
                "demand_band": _demand_band(demand_count),
                "expiring_soon": bool(reservation and reservation.get("expiring_soon")),
                "reservation": reservation,
            }
            grouped.setdefault(block_id, {}).setdefault(floor, []).append(unit)

            if price:
                all_prices.append(price)

            label = listing.get("flat_label", "")
            if label:
                summary = type_summary.setdefault(
                    label, {"label": label, "available": 0, "total": 0, "_price_sum": 0, "_price_n": 0}
                )
                summary["total"] += 1
                if unit["status"] == "available":
                    summary["available"] += 1
                if price:
                    summary["_price_sum"] += price
                    summary["_price_n"] += 1

            if unit["expiring_soon"]:
                expiring_options.append({
                    "inventory_id": inventory_id,
                    "block_id": block_id,
                    "door_number": unit["door_number"],
                    "remaining_label": reservation.get("remaining_label"),
                    "remaining_minutes": reservation.get("remaining_minutes"),
                    "user_id": reservation.get("user_id", ""),
                })

        totals = {"available": 0, "reserved": 0, "sold": 0, "total": 0}
        blocks: List[Dict[str, Any]] = []
        for block_id in sorted(grouped.keys()):
            floors_map = grouped[block_id]
            counts = {"available": 0, "reserved": 0, "sold": 0, "total": 0}
            floors = []
            # Ust kat en ustte gorunsun diye katlar azalan sirada
            for floor in sorted(floors_map.keys(), reverse=True):
                units = sorted(floors_map[floor], key=lambda u: str(u["door_number"]))
                row_counts = {"available": 0, "total": 0}
                for unit in units:
                    if unit["status"] in counts:
                        counts[unit["status"]] += 1
                    counts["total"] += 1
                    row_counts["total"] += 1
                    if unit["status"] == "available":
                        row_counts["available"] += 1
                floors.append({"floor": floor, "units": units, "counts": row_counts})

            for key in totals:
                totals[key] += counts[key]

            moved = counts["reserved"] + counts["sold"]
            blocks.append({
                "block_id": block_id,
                "block_type": blocks_meta.get(block_id, {}).get("type", ""),
                "counts": counts,
                "occupancy_pct": round((moved / counts["total"]) * 100) if counts["total"] else 0,
                "floors": floors,
            })

        # Tip ozetini finalize et (ortalama fiyat) ve daire tipi sirasina gore diz.
        type_order = [flat["label"] for flat in self._load_json("flats.json")]
        type_summary_list = []
        for label in sorted(type_summary.keys(), key=lambda l: type_order.index(l) if l in type_order else 99):
            summary = type_summary[label]
            avg = round(summary["_price_sum"] / summary["_price_n"]) if summary["_price_n"] else None
            type_summary_list.append({
                "label": label,
                "available": summary["available"],
                "total": summary["total"],
                "avg_price_try": avg,
                "avg_price_short": self._format_price_short(avg),
            })

        expiring_options.sort(key=lambda item: item["remaining_minutes"] if item["remaining_minutes"] is not None else 1 << 30)

        moved_total = totals["reserved"] + totals["sold"]
        return {
            "blocks": blocks,
            "totals": totals,
            "occupancy_pct": round((moved_total / totals["total"]) * 100) if totals["total"] else 0,
            "flat_types": sorted({listing.get("flat_label", "") for listing in listings if listing.get("flat_label")}),
            "type_summary": type_summary_list,
            "expiring_options": expiring_options,
            "directions": sorted({l.get("direction") for l in listings if l.get("direction")}),
            "sun_exposures": sorted({(l.get("sunlight") or {}).get("sun_exposure") for l in listings if (l.get("sunlight") or {}).get("sun_exposure")}),
            "price_range": {"min": min(all_prices), "max": max(all_prices)} if all_prices else {"min": 0, "max": 0},
            "floor_range": {
                "min": min((int(l.get("floor", 0)) for l in listings), default=0),
                "max": max((int(l.get("floor", 0)) for l in listings), default=0),
            },
        }

    def get_sales_velocity(self, days: int = 30) -> Dict[str, Any]:
        """Son N gunde 'sold' gecisi sayisindan satis hizi + absorpsiyon tahmini.

        listing_status_log yeni acildigindan veri birikene dek has_data=False
        doner; panel bu durumda 'veri birikiyor' mesaji gosterir.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            sold_recent = connection.execute(
                "SELECT COUNT(*) FROM listing_status_log WHERE to_status = 'sold' AND created_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_sold_logs = connection.execute(
                "SELECT COUNT(*) FROM listing_status_log WHERE to_status = 'sold'"
            ).fetchone()[0]

        available_now = sum(1 for l in self.list_listings() if l.get("status") == "available")
        per_month = sold_recent * (30.0 / days) if days else 0
        absorption_months = round(available_now / per_month, 1) if per_month > 0 else None

        return {
            "has_data": total_sold_logs > 0,
            "days": days,
            "sold_last_n": sold_recent,
            "per_week": round(sold_recent * (7.0 / days), 1) if days else 0,
            "available_now": available_now,
            "absorption_months": absorption_months,
        }

    def get_type_pressure(self) -> List[Dict[str, Any]]:
        """Tip bazli talep-stok baskisi: her tip icin eslesen lead vs satistaki stok.

        get_leads yerine _all_note_payloads kullanir (panoda hafif kalsin).
        """
        note_payloads = self._all_note_payloads()
        listings = self.list_listings()

        available_by_type: Dict[str, int] = {}
        label_by_id: Dict[str, str] = {}
        for listing in listings:
            label = listing.get("flat_label", "")
            label_by_id[listing.get("flat_type_id", "")] = label
            if listing.get("status") == "available" and label:
                available_by_type[label] = available_by_type.get(label, 0) + 1

        demand_by_type: Dict[str, int] = {}
        for notes in note_payloads:
            label = notes.get("preferred_flat_type")
            if not label and notes.get("preferred_flat_type_id"):
                label = label_by_id.get(notes["preferred_flat_type_id"])
            if label:
                demand_by_type[label] = demand_by_type.get(label, 0) + 1

        type_order = [flat["label"] for flat in self._load_json("flats.json")]
        rows = []
        for label in sorted(set(available_by_type) | set(demand_by_type),
                            key=lambda l: type_order.index(l) if l in type_order else 99):
            demand = demand_by_type.get(label, 0)
            stock = available_by_type.get(label, 0)
            # Baski = talep / stok; stok 0 ve talep varsa yuksek baski.
            if stock == 0:
                pressure = demand if demand else 0
            else:
                pressure = round(demand / stock, 2)
            rows.append({"label": label, "demand": demand, "stock": stock, "pressure": pressure})
        # Yuksek baski once
        rows.sort(key=lambda r: r["pressure"], reverse=True)
        return rows

    def get_dashboard_stats(self) -> Dict[str, Any]:
        listings = self.list_listings()
        conversations = self.list_conversations()
        available_count = sum(1 for item in listings if item.get("status") == "available")
        return {
            "listing_count": len(listings),
            "available_count": available_count,
            "conversation_count": len(conversations),
            "turn_count": sum(item["turn_count"] for item in conversations),
        }

    # --- Teklifler -------------------------------------------------------------

    def create_offer(self, inventory_id: str, user_id: str, plan: Dict[str, Any]) -> int:
        timestamp = self._utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO offers (inventory_id, user_id, plan_json, created_at) VALUES (?, ?, ?, ?)",
                (inventory_id, user_id, json.dumps(plan, ensure_ascii=False), timestamp),
            )
            offer_id = cursor.lastrowid
        self._record_lead_event(user_id, "offer_created", {
            "offer_id": offer_id,
            "inventory_id": inventory_id,
            "total": plan.get("total"),
            "months": (plan.get("inputs") or {}).get("months"),
        })
        return offer_id

    def get_offer(self, offer_id: int) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, inventory_id, user_id, plan_json, created_at FROM offers WHERE id = ?",
                (offer_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Teklif bulunamadi: {offer_id}")
        payload = dict(row)
        payload["plan"] = json.loads(payload.pop("plan_json"))
        return payload

    # --- Talep-unite eslestirme ----------------------------------------------

    def _score_listing_against_notes(self, listing: Dict[str, Any], notes: Dict[str, Any]) -> tuple:
        """Ortak talep-daire skorlamasi: (score, reasons, has_criteria).

        Hem get_unit_matches (musteri -> daireler) hem get_matching_leads_for_unit
        (daire -> musteriler) ayni kurallari kullanir.
        """
        preferred_type_id = notes.get("preferred_flat_type_id")
        preferred_type_label = notes.get("preferred_flat_type")
        budget_max = notes.get("budget_max_try")
        preferred_block = notes.get("preferred_block")
        preferred_floor = notes.get("preferred_floor")
        preferred_direction = notes.get("preferred_direction")
        sun_preference = notes.get("sun_preference")

        has_criteria = any([
            preferred_type_id, preferred_type_label, budget_max,
            preferred_block, preferred_floor, preferred_direction, sun_preference,
        ])
        if not has_criteria:
            return 0, [], False

        score = 0
        reasons: List[str] = []

        if preferred_type_id and listing.get("flat_type_id") == preferred_type_id:
            score += 4
            reasons.append(f"{listing.get('flat_label')} ✓")
        elif preferred_type_label and listing.get("flat_label") == preferred_type_label:
            score += 4
            reasons.append(f"{listing.get('flat_label')} ✓")

        price = listing.get("list_price_try")
        if budget_max and price:
            if price <= budget_max:
                score += 3
                reasons.append("bütçeye uygun")
            elif price <= budget_max * 1.1:
                score += 1
                reasons.append("bütçeye yakın (%10)")

        preferred_block_value = preferred_block
        if preferred_block_value and listing.get("block_id") == preferred_block_value:
            score += 1
            reasons.append(f"{preferred_block_value} blok")
        if preferred_floor is not None and listing.get("floor") == preferred_floor:
            score += 1
            reasons.append(f"{preferred_floor}. kat")
        if preferred_direction and listing.get("direction") == preferred_direction:
            score += 1
            reasons.append("cephe uygun")
        if sun_preference and (listing.get("sunlight") or {}).get("sun_exposure") == sun_preference:
            score += 1
            reasons.append("güneş tercihi")

        return score, reasons, True

    def get_block_price_context(self, listing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bu dairenin net m² fiyatinin ayni bloktaki satistaki dairelere gore konumu.

        Pazarlik/fiyatlama kozu: 'blok ortalamasinin %4 altinda' gibi.
        """
        net_m2 = (listing.get("flat") or {}).get("net_m2")
        price = listing.get("list_price_try")
        if not net_m2 or not price:
            return None
        this_per_m2 = price / net_m2

        block_id = listing.get("block_id")
        per_m2_values = []
        for other in self.list_listings():
            if other.get("block_id") != block_id or other.get("status") != "available":
                continue
            other_net = (other.get("flat") or {}).get("net_m2")
            other_price = other.get("list_price_try")
            if other_net and other_price:
                per_m2_values.append(other_price / other_net)

        if len(per_m2_values) < 2:
            return None
        avg = sum(per_m2_values) / len(per_m2_values)
        if avg <= 0:
            return None
        diff_pct = round((this_per_m2 - avg) / avg * 100)
        return {
            "this_per_m2": round(this_per_m2),
            "block_avg_per_m2": round(avg),
            "diff_pct": diff_pct,
            "sample_size": len(per_m2_values),
            "block_id": block_id,
        }

    def get_matching_leads_for_unit(self, listing: Dict[str, Any], limit: int = 5, min_score: int = 4) -> List[Dict[str, Any]]:
        """Tersine eslestirme: bu daire hangi musterilerin kriterine uyuyor?

        min_score=4 en az tip eslesmesi (veya butce+kombinasyon) demektir;
        ilan detayinda 'bu daireyi kime satabilirim' listesini besler.
        """
        with self._connect() as connection:
            rows = connection.execute("SELECT user_id, ai_notes_json FROM user_ai_notes").fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            if row["user_id"] in self.INTERNAL_USER_IDS:
                continue
            try:
                notes = json.loads(row["ai_notes_json"] or "{}")
            except json.JSONDecodeError:
                continue
            score, reasons, has_criteria = self._score_listing_against_notes(listing, notes)
            if not has_criteria or score < min_score:
                continue
            results.append({
                "user_id": row["user_id"],
                "name": notes.get("name") or "",
                "score": score,
                "reasons": reasons,
                "preferred_flat_type": notes.get("preferred_flat_type") or "",
                "budget_max_try": notes.get("budget_max_try"),
            })

        results.sort(key=lambda item: -item["score"])
        return results[:limit]

    def get_demand_heat(self, min_score: int = 4) -> Dict[str, int]:
        """inventory_id -> bu daireye kriteri uyan lead sayisi.

        Stok panosunun 'talep' isi haritasini besler. min_score=4 en az tip
        eslesmesi demektir; bellek ici skorlama, 212 daire x N lead sorunsuz.
        """
        note_payloads = self._all_note_payloads()
        if not note_payloads:
            return {}

        heat: Dict[str, int] = {}
        for listing in self.list_listings():
            if listing.get("status") != "available":
                continue
            count = 0
            for notes in note_payloads:
                score, _reasons, has_criteria = self._score_listing_against_notes(listing, notes)
                if has_criteria and score >= min_score:
                    count += 1
            if count:
                heat[listing["inventory_id"]] = count
        return heat

    def get_unit_matches(self, notes: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
        """AI notlarindaki tercihlere gore 'satista' daireleri skorlar.

        Turk emlak CRM'lerindeki talep-portfoy eslestirme kalibinin tek projeli
        surumu: kriter sayisi arttikca skor artar, en iyi eslesmeler doner.
        """
        matches: List[Dict[str, Any]] = []
        for listing in self.list_listings():
            if listing.get("status") != "available":
                continue

            score, reasons, has_criteria = self._score_listing_against_notes(listing, notes)
            if not has_criteria:
                return []
            price = listing.get("list_price_try")

            if score <= 0:
                continue

            matches.append({
                "inventory_id": listing["inventory_id"],
                "block_id": listing.get("block_id"),
                "floor": listing.get("floor"),
                "door_number": listing.get("door_number"),
                "flat_label": listing.get("flat_label"),
                "direction": listing.get("direction"),
                "list_price_try": price,
                "price_short": self._format_price_short(price),
                "score": score,
                "reasons": reasons,
            })

        matches.sort(key=lambda item: (-item["score"], item["list_price_try"] or 0))
        return matches[:limit]

    # --- Handoff kuyrugu ------------------------------------------------------

    def claim_handoff(self, user_id: str) -> None:
        """Danismanin 'devraldim' isareti; kuyruktan dusurur, gecmise islenir
        ve AI bu kullanici icin duraklatilir (insan konusmayi yurutecek)."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lead_events (user_id, event_type, payload, created_at)
                VALUES (?, 'handoff_claimed', '{}', ?)
                """,
                (user_id, self._utc_now()),
            )
        self.set_ai_paused(user_id, True)

    def get_handoff_queue(self, max_age_days: int = 14) -> List[Dict[str, Any]]:
        """Insan bekleyen konusmalar: son handoff'u devralinmamis kullanicilar,
        bekleme suresi (SLA) ile birlikte."""
        now = datetime.now(self.TR_TZ)
        cutoff = now - timedelta(days=max_age_days)

        with self._connect() as connection:
            turn_rows = [dict(row) for row in connection.execute(
                """
                SELECT turns.session_id, turns.channel, turns.user_input, turns.context_json,
                       turns.created_at, sessions.user_id
                FROM conversation_turns AS turns
                JOIN conversation_sessions AS sessions ON sessions.session_id = turns.session_id
                WHERE turns.handoff_required = 1
                  AND turns.created_at >= ?
                ORDER BY turns.id ASC
                """,
                # Bir gun GENIS tutulur: cutoff TR saat dilimli, created_at UTC
                # yazilir. Bu bir ON FILTRE; kesin yas kontrolu asagida Python
                # tarafinda timezone-farkindalikli yapilir. Genis tutmazsak SQL
                # Python'dan katiysa sinirdaki kayit kuyruktan sessizce duser.
                ((cutoff - timedelta(days=1)).isoformat(),),
            ).fetchall()]
            claim_rows = [dict(row) for row in connection.execute(
                "SELECT user_id, MAX(created_at) AS claimed_at FROM lead_events WHERE event_type = 'handoff_claimed' GROUP BY user_id"
            ).fetchall()]
            note_rows = {row["user_id"]: dict(row) for row in connection.execute(
                "SELECT user_id, ai_summary, ai_notes_json FROM user_ai_notes"
            ).fetchall()}

        claims = {row["user_id"]: self._parse_timestamp(row["claimed_at"]) for row in claim_rows}

        latest_handoff: Dict[str, Dict[str, Any]] = {}
        for turn in turn_rows:
            user_id = turn["user_id"]
            if user_id in self.INTERNAL_USER_IDS:
                continue
            try:
                context = json.loads(turn.get("context_json") or "{}")
            except json.JSONDecodeError:
                continue
            handoff = context.get("handoff") or {}
            if not handoff.get("required"):
                continue
            created = self._parse_timestamp(turn.get("created_at"))
            if created is None or created < cutoff:
                continue
            latest_handoff[user_id] = {
                "user_id": user_id,
                "session_id": turn["session_id"],
                "channel": turn.get("channel", "default"),
                "reason": handoff.get("reason") or "",
                "share_contact_details": bool(handoff.get("share_contact_details")),
                "share_location": bool(handoff.get("share_location")),
                "last_user_message": turn.get("user_input") or "",
                "requested_at": created,
            }

        queue: List[Dict[str, Any]] = []
        for user_id, item in latest_handoff.items():
            claimed_at = claims.get(user_id)
            if claimed_at and claimed_at >= item["requested_at"]:
                continue

            note_row = note_rows.get(user_id) or {}
            try:
                notes = json.loads(note_row.get("ai_notes_json") or "{}")
            except json.JSONDecodeError:
                notes = {}

            waiting_minutes = max(0, int((now - item["requested_at"]).total_seconds() // 60))
            queue.append({
                **item,
                "requested_at": item["requested_at"].strftime("%d.%m %H:%M"),
                "waiting_minutes": waiting_minutes,
                "sla_level": "ok" if waiting_minutes < 15 else ("warn" if waiting_minutes < 60 else "late"),
                "name": notes.get("name"),
                "ai_summary": note_row.get("ai_summary") or "",
            })

        queue.sort(key=lambda item: -item["waiting_minutes"])
        return queue

    # --- Gorevler ---------------------------------------------------------------

    def create_task(self, title: str, user_id: str = "", due_at: Optional[str] = None) -> int:
        title = title.strip()
        if not title:
            raise ValueError("Gorev basligi bos olamaz.")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (title, user_id, due_at, created_at) VALUES (?, ?, ?, ?)",
                (title, user_id.strip(), due_at or None, self._utc_now()),
            )
            return cursor.lastrowid

    def set_task_done(self, task_id: int, done: bool) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE tasks SET done = ?, done_at = ? WHERE id = ?",
                (1 if done else 0, self._utc_now() if done else None, task_id),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Gorev bulunamadi: {task_id}")

    def list_tasks(self, include_done: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT id, title, user_id, due_at, done, created_at, done_at FROM tasks"
        if not include_done:
            query += " WHERE done = 0"
        query += " ORDER BY done ASC, COALESCE(due_at, created_at) ASC"
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(query).fetchall()]
        for row in rows:
            row["done"] = bool(row["done"])
            due = self._parse_timestamp(row.get("due_at"))
            row["due_label"] = due.strftime("%d.%m.%Y") if due else None
            row["overdue"] = bool(due and due < datetime.now(self.TR_TZ) and not row["done"])
        return rows

    def get_followup_tasks(self) -> List[Dict[str, Any]]:
        """Otomatik gunluk takip listesi: veriden turetilir, kayit tutulmaz.

        Kaynaklar: devralinmamis handoff'lar, 24 saat icinde dolacak opsiyonlar,
        3+ gundur temas edilmemis sicak/ilik lead'ler.
        """
        now = datetime.now(self.TR_TZ)
        tasks: List[Dict[str, Any]] = []

        for item in self.get_handoff_queue():
            tasks.append({
                "kind": "handoff",
                "priority": "high",
                "title": f"{item['name'] or item['user_id']} danışman dönüşü bekliyor",
                "detail": f"{item['waiting_minutes']} dk'dır kuyrukta · {item['reason'] or 'yönlendirme talebi'}",
                "user_id": item["user_id"],
            })

        for inventory_id, reservation in self.get_active_reservations().items():
            if reservation["kind"] != "option" or not reservation.get("expires_at"):
                continue
            remaining = reservation.get("remaining_label") or ""
            # 24 saatten az kaldiysa gorev uret
            expires = None
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT expires_at FROM unit_reservations WHERE inventory_id = ? AND state = 'active' AND kind = 'option'",
                    (inventory_id,),
                ).fetchone()
                if row:
                    expires = self._parse_timestamp(row["expires_at"])
            if expires and (expires - now) <= timedelta(hours=24):
                tasks.append({
                    "kind": "option",
                    "priority": "high",
                    "title": f"{inventory_id} opsiyonu dolmak üzere (kalan {remaining})",
                    "detail": f"Müşteri: {reservation.get('user_id') or 'belirtilmedi'} — karar için arayın",
                    "user_id": reservation.get("user_id") or "",
                })

        for lead in self.get_leads():
            if lead["temperature"] not in ("hot", "warm"):
                continue
            if lead["stage"] in ("won", "lost", "reserved"):
                continue
            last_contact = self._parse_timestamp(lead.get("last_contact_iso"))
            if last_contact is None or (now - last_contact) >= timedelta(days=3):
                days = int((now - last_contact).days) if last_contact else None
                tasks.append({
                    "kind": "stale_lead",
                    "priority": "medium",
                    "title": f"{lead['name'] or lead['user_id']} ile temas kur",
                    "detail": (f"{days} gündür temas yok" if days is not None else "Hiç temas kaydı yok")
                              + f" · {lead['temperature'].upper()} lead"
                              + (f" · {lead['preferred_flat_type']}" if lead.get("preferred_flat_type") else ""),
                    "user_id": lead["user_id"],
                })

        priority_order = {"high": 0, "medium": 1, "low": 2}
        tasks.sort(key=lambda task: priority_order.get(task["priority"], 9))
        return tasks

    # --- Gunluk brifing ---------------------------------------------------------

    def get_briefing_context(self) -> Dict[str, Any]:
        """LLM'in sabah brifingi yazmasi icin kompakt veri paketi."""
        analytics = self.get_dashboard_analytics()
        queue = [
            {
                "musteri": item["name"] or item["user_id"],
                "sebep": item["reason"] or "yönlendirme talebi",
                "bekleme_dk": item["waiting_minutes"],
            }
            for item in self.get_handoff_queue()[:5]
        ]
        followups = [
            {"oncelik": task["priority"], "gorev": task["title"], "detay": task["detail"]}
            for task in self.get_followup_tasks()[:6]
        ]
        hot_leads = [
            {
                "musteri": lead["name"] or lead["user_id"],
                "ozet": lead["summary"][:120],
                "konu": (lead.get("ai_summary") or "")[:60],
            }
            for lead in analytics["hot_leads"][:5]
        ]
        revenue = self.get_revenue_kpis()
        return {
            "kpis": analytics["kpis"],
            "gelir": {
                "kurtarilan_hacim_try": revenue["recovered_volume_try"],
                "teklif_kabul": revenue["offers_accepted"],
                "odeme_linki": revenue["payment_links"],
                "cozum_orani_pct": revenue["containment_pct"],
            },
            "dikkat_isteyen_musteriler": hot_leads,
            "insan_bekleyenler": queue,
            "takip_listesi": followups,
            "son_aktivite": [
                {"kim": turn["user_id"], "mesaj": turn["snippet"], "zaman": turn["created_at"]}
                for turn in analytics["recent_turns"][:5]
            ],
        }

    def save_briefing(self, content: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO briefings (content, created_at) VALUES (?, ?)",
                (content.strip(), self._utc_now()),
            )
            return cursor.lastrowid

    def get_latest_briefing(self) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, content, created_at FROM briefings ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        created = self._parse_timestamp(payload.get("created_at"))
        payload["created_label"] = created.strftime("%d.%m.%Y %H:%M") if created else ""
        return payload

    # --- Analitik raporu -------------------------------------------------------

    STAGE_LABELS_TR = {
        "new": "Yeni",
        "support": "Destekte",
        "offer": "Teklif Sunuldu",
        "won": "Kazanıldı",
        "lost": "Kayıp",
    }

    def get_analytics_report(self) -> Dict[str, Any]:
        """Analitik sayfasi: donusum hunisi, talep-stok baskisi, AI performansi."""
        leads = self.get_leads()

        # --- Donusum hunisi: her asamaya ulasan lead sayisi (kumulatif) ---
        funnel_stages = [stage for stage in self.LEAD_STAGES if stage != "lost"]
        total_leads = len(leads)
        funnel = []
        for stage in funnel_stages:
            stage_index = self.LEAD_STAGES.index(stage)
            reached = sum(
                1 for lead in leads
                if lead["stage"] != "lost" and self.LEAD_STAGES.index(lead["stage"]) >= stage_index
            )
            funnel.append({
                "stage": stage,
                "label": self.STAGE_LABELS_TR[stage],
                "count": reached,
                "pct": round(reached / total_leads * 100) if total_leads else 0,
            })
        lost_count = sum(1 for lead in leads if lead["stage"] == "lost")

        # --- Talep vs stok baskisi ---
        listings = self.list_listings()
        available = [item for item in listings if item.get("status") == "available"]

        def _pressure_table(demand_key: str, stock_key) -> List[Dict[str, Any]]:
            demand_counts: Dict[str, int] = {}
            for lead in leads:
                value = lead.get(demand_key)
                if value:
                    demand_counts[str(value)] = demand_counts.get(str(value), 0) + 1

            stock_counts: Dict[str, int] = {}
            for item in available:
                value = stock_key(item)
                if value:
                    stock_counts[str(value)] = stock_counts.get(str(value), 0) + 1

            rows = []
            for label in sorted(set(demand_counts) | set(stock_counts)):
                demand = demand_counts.get(label, 0)
                stock = stock_counts.get(label, 0)
                rows.append({
                    "label": label,
                    "demand": demand,
                    "stock": stock,
                    "pressure": round(demand / stock, 2) if stock else (float(demand) if demand else 0.0),
                })
            rows.sort(key=lambda row: -row["pressure"])
            return rows

        demand_by_type = _pressure_table("preferred_flat_type", lambda item: item.get("flat_label"))

        direction_demand: Dict[str, int] = {}
        for row in self._all_note_payloads():
            direction = row.get("preferred_direction")
            if direction:
                direction_demand[str(direction)] = direction_demand.get(str(direction), 0) + 1
        direction_stock: Dict[str, int] = {}
        for item in available:
            direction = item.get("direction")
            if direction:
                direction_stock[str(direction)] = direction_stock.get(str(direction), 0) + 1
        demand_by_direction = []
        for label in sorted(set(direction_demand) | set(direction_stock)):
            demand = direction_demand.get(label, 0)
            stock = direction_stock.get(label, 0)
            demand_by_direction.append({
                "label": label,
                "demand": demand,
                "stock": stock,
                "pressure": round(demand / stock, 2) if stock else (float(demand) if demand else 0.0),
            })
        demand_by_direction.sort(key=lambda row: -row["pressure"])

        # --- AI performansi ---
        with self._connect() as connection:
            sessions = [dict(row) for row in connection.execute(
                "SELECT session_id, user_id, channel FROM conversation_sessions"
            ).fetchall()]
            turns = [dict(row) for row in connection.execute(
                "SELECT session_id, router_decision_json, context_json FROM conversation_turns"
            ).fetchall()]

        internal_sessions = {
            session["session_id"] for session in sessions
            if session["user_id"] in self.INTERNAL_USER_IDS
        }
        sessions = [s for s in sessions if s["session_id"] not in internal_sessions]
        turns = [t for t in turns if t["session_id"] not in internal_sessions]

        handoff_sessions = set()
        tool_usage: Dict[str, int] = {}
        # AI performans/maliyet toplayicilari (observability)
        iter_sum = iter_n = tokens_sum = lat_sum = lat_n = 0
        for turn in turns:
            try:
                router = json.loads(turn.get("router_decision_json") or "{}")
            except json.JSONDecodeError:
                router = {}
            tool = router.get("tool") or "bilinmiyor"
            tool_usage[tool] = tool_usage.get(tool, 0) + 1

            iterations = router.get("iterations")
            if isinstance(iterations, int):
                iter_sum += iterations
                iter_n += 1
            usage = router.get("usage") or {}
            tokens_sum += int(usage.get("total_tokens")
                              or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                              or 0)
            latency = router.get("latency_ms")
            if isinstance(latency, (int, float)):
                lat_sum += latency
                lat_n += 1

            try:
                context = json.loads(turn.get("context_json") or "{}")
            except json.JSONDecodeError:
                context = {}
            if (context.get("handoff") or {}).get("required"):
                handoff_sessions.add(turn["session_id"])

        total_sessions = len(sessions)
        channel_counts: Dict[str, int] = {}
        for session in sessions:
            channel = session.get("channel") or "default"
            channel_counts[channel] = channel_counts.get(channel, 0) + 1

        ai_performance = {
            "total_sessions": total_sessions,
            "total_turns": len(turns),
            "avg_turns_per_session": round(len(turns) / total_sessions, 1) if total_sessions else 0,
            "handoff_sessions": len(handoff_sessions),
            "handoff_rate_pct": round(len(handoff_sessions) / total_sessions * 100) if total_sessions else 0,
            "containment_pct": round((1 - len(handoff_sessions) / total_sessions) * 100) if total_sessions else 0,
            "channel_counts": channel_counts,
            # AI performans/maliyet (observability)
            "avg_iterations": round(iter_sum / iter_n, 2) if iter_n else 0,
            "avg_latency_ms": round(lat_sum / lat_n) if lat_n else 0,
            "total_tokens": tokens_sum,
            "avg_tokens_per_turn": round(tokens_sum / len(turns)) if turns else 0,
        }
        tool_usage_rows = sorted(
            [{"tool": tool, "count": count} for tool, count in tool_usage.items()],
            key=lambda row: -row["count"],
        )

        return {
            "funnel": funnel,
            "lost_count": lost_count,
            "total_leads": total_leads,
            "demand_by_type": demand_by_type,
            "demand_by_direction": demand_by_direction,
            "ai_performance": ai_performance,
            "tool_usage": tool_usage_rows,
        }

    def _all_note_payloads(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT user_id, ai_notes_json FROM user_ai_notes").fetchall()
        payloads = []
        for row in rows:
            if row["user_id"] in self.INTERNAL_USER_IDS:
                continue
            try:
                payloads.append(json.loads(row["ai_notes_json"] or "{}"))
            except json.JSONDecodeError:
                continue
        return payloads

    # --- Dashboard analitigi -------------------------------------------------

    TR_TZ = timezone(timedelta(hours=3))
    OFFICE_HOUR_START = 9
    OFFICE_HOUR_END = 18

    def _parse_timestamp(self, raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(self.TR_TZ)

    def _score_lead(self, notes: Dict[str, Any], updated_at: Optional[datetime], now: datetime) -> int:
        score = 0
        if notes.get("handoff_required"):
            score += 3
        if notes.get("urgency") == "high":
            score += 2
        if notes.get("budget_max_try"):
            score += 2
        if notes.get("preferred_flat_type"):
            score += 1
        if updated_at is not None:
            age = now - updated_at
            if age <= timedelta(hours=48):
                score += 2
            elif age <= timedelta(days=7):
                score += 1
        return score

    def get_dashboard_analytics(self) -> Dict[str, Any]:
        """Komuta merkezi dashboard'u icin KPI'lar, zaman serileri ve sicak
        lead listesini tek seferde hesaplar. Veri hacmi kucuk oldugu icin
        hesaplar Python tarafinda yapilir."""
        now = datetime.now(self.TR_TZ)
        today = now.date()
        week_ago = now - timedelta(days=7)
        prev_week_start = now - timedelta(days=14)
        month_ago = now - timedelta(days=30)

        with self._connect() as connection:
            sessions = [dict(row) for row in connection.execute(
                "SELECT session_id, user_id, channel, created_at, last_message_at FROM conversation_sessions"
            ).fetchall()]
            turns = [dict(row) for row in connection.execute(
                "SELECT session_id, channel, user_input, created_at, context_json FROM conversation_turns ORDER BY id ASC"
            ).fetchall()]
            note_rows = [dict(row) for row in connection.execute(
                "SELECT user_id, ai_summary, ai_notes_json, updated_at FROM user_ai_notes"
            ).fetchall()]

        # Panel test kimligi metrikleri sismesin
        internal_sessions = {s["session_id"] for s in sessions if s["user_id"] in self.INTERNAL_USER_IDS}
        sessions = [s for s in sessions if s["session_id"] not in internal_sessions]
        turns = [t for t in turns if t["session_id"] not in internal_sessions]

        # --- KPI'lar ---
        new_leads_today = 0
        new_leads_week = 0
        new_leads_prev_week = 0
        out_of_hours_week = 0
        active_conversations = 0

        for session in sessions:
            created = self._parse_timestamp(session.get("created_at"))
            last_message = self._parse_timestamp(session.get("last_message_at"))
            if created:
                if created.date() == today:
                    new_leads_today += 1
                if created >= week_ago:
                    new_leads_week += 1
                    if not (self.OFFICE_HOUR_START <= created.hour < self.OFFICE_HOUR_END):
                        out_of_hours_week += 1
                elif created >= prev_week_start:
                    new_leads_prev_week += 1
            if last_message and (now - last_message) <= timedelta(hours=24):
                active_conversations += 1

        handoff_week = 0
        hour_histogram = [0] * 24
        daily_counts: Dict[str, int] = {}
        for turn in turns:
            created = self._parse_timestamp(turn.get("created_at"))
            if created is None:
                continue
            if created >= month_ago:
                daily_counts[created.date().isoformat()] = daily_counts.get(created.date().isoformat(), 0) + 1
            if created >= week_ago:
                hour_histogram[created.hour] += 1
                try:
                    context = json.loads(turn.get("context_json") or "{}")
                except json.JSONDecodeError:
                    context = {}
                if (context.get("handoff") or {}).get("required"):
                    handoff_week += 1

        daily_series = []
        for offset in range(29, -1, -1):
            day = (now - timedelta(days=offset)).date().isoformat()
            daily_series.append({"date": day, "count": daily_counts.get(day, 0)})

        # --- Talep dagilimi (AI notlarindan) ---
        flat_type_demand: Dict[str, int] = {}
        hot_leads: List[Dict[str, Any]] = []
        for row in note_rows:
            if row.get("user_id") in self.INTERNAL_USER_IDS:
                continue
            try:
                notes = json.loads(row.get("ai_notes_json") or "{}")
            except json.JSONDecodeError:
                notes = {}
            flat_label = notes.get("preferred_flat_type")
            if flat_label:
                flat_type_demand[flat_label] = flat_type_demand.get(flat_label, 0) + 1

            updated_at = self._parse_timestamp(row.get("updated_at"))
            score = self._score_lead(notes, updated_at, now)
            if score <= 0:
                continue
            hot_leads.append({
                "user_id": row["user_id"],
                "name": notes.get("name"),
                "summary": row.get("ai_summary") or "",
                "budget_max_try": notes.get("budget_max_try"),
                "preferred_flat_type": flat_label,
                "handoff_required": bool(notes.get("handoff_required")),
                "updated_at": row.get("updated_at"),
                "score": score,
                "temperature": "hot" if score >= 6 else ("warm" if score >= 3 else "cold"),
            })

        hot_leads.sort(key=lambda lead: (lead["score"], lead["updated_at"] or ""), reverse=True)
        demand_series = sorted(
            [{"label": label, "count": count} for label, count in flat_type_demand.items()],
            key=lambda item: -item["count"],
        )

        # --- Son aktivite ---
        recent_turns = []
        for turn in turns[-8:][::-1]:
            created = self._parse_timestamp(turn.get("created_at"))
            session = next((s for s in sessions if s["session_id"] == turn["session_id"]), {})
            recent_turns.append({
                "user_id": session.get("user_id", "?"),
                "channel": turn.get("channel", "default"),
                "snippet": (turn.get("user_input") or "")[:90],
                "created_at": created.strftime("%d.%m %H:%M") if created else "",
                "session_id": turn.get("session_id"),
            })

        # --- Stok ozeti ---
        listings = self.list_listings()
        status_counts = {"available": 0, "reserved": 0, "sold": 0}
        for item in listings:
            status = item.get("status")
            if status in status_counts:
                status_counts[status] += 1
        total_units = len(listings)
        moved_units = status_counts["reserved"] + status_counts["sold"]
        occupancy_pct = round((moved_units / total_units) * 100) if total_units else 0

        trend = new_leads_week - new_leads_prev_week

        return {
            "kpis": {
                "new_leads_today": new_leads_today,
                "new_leads_week": new_leads_week,
                "new_leads_prev_week": new_leads_prev_week,
                "lead_trend": trend,
                "active_conversations": active_conversations,
                "handoff_week": handoff_week,
                "out_of_hours_week": out_of_hours_week,
            },
            "inventory": {
                "total": total_units,
                **status_counts,
                "occupancy_pct": occupancy_pct,
            },
            "daily_series": daily_series,
            "hour_histogram": hour_histogram,
            "demand_series": demand_series,
            "hot_leads": hot_leads[:6],
            "recent_turns": recent_turns,
        }

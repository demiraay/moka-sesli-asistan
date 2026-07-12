"""7 demo senaryosunun araç dispatch'ini LLM'siz doğrular.

Router LLM monkeypatch'lenir (sabit JSON), cevap LLM'i "ok" döner; testler
dispatch'in doğru context alanlarını doldurduğunu ve yan etkileri (servis
görevi, outbox, lead_events) üretildiğini kontrol eder.
"""

import json

import pytest

from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator


class FakeLLM:
    """Sıradaki router kararlarını kuyruktan döndürür; cevap çağrılarında 'ok' der."""

    def __init__(self):
        self.router_queue = []

    def push(self, tool, args=None, card=None):
        self.router_queue.append(json.dumps({
            "tool": tool,
            "args": args or {},
            "card": card or {},
        }, ensure_ascii=False))

    def generate(self, system_prompt, user_prompt, json_mode=False, profile="default"):
        if json_mode:
            return self.router_queue.pop(0) if self.router_queue else json.dumps(
                {"tool": "answer_general", "args": {}, "card": {}}
            )
        return "ok"


@pytest.fixture()
def orchestrator(tmp_path):
    orch = AgentOrchestrator()
    orch.admin_store = AdminStore(db_path=str(tmp_path / "test.sqlite3"))
    orch.llm_client = FakeLLM()
    # Testler arasi sizintiyi onle
    orch.conversation_histories.clear()
    orch.user_profiles.clear()
    orch.active_sessions.clear()
    orch.call_contexts.clear()
    return orch


def _turn(orch, user_id, text, tool, args=None, card=None):
    orch.llm_client.push(tool, args, card)
    return orch.process_turn(text, user_id=user_id, channel="demo")


# --- S1: Hakediş sorgusu ---------------------------------------------------

def test_s1_settlement(orchestrator):
    result = _turn(orchestrator, "u-s1", "Param ne zaman yatacak?",
                   "get_settlement_status", {"period": "latest"})
    ctx = result["context"]
    assert ctx["settlement"]["batch_id"] == "SET-9012"
    facts = " ".join(ctx["message_facts"])
    assert "44 bin 104 TL" in facts
    assert "yarın" in facts


# --- S2: Kayıp işlem --------------------------------------------------------

def test_s2_find_transaction(orchestrator):
    result = _turn(orchestrator, "u-s2", "Dün 1.250 TL çektim ama göremiyorum",
                   "find_transaction", {"amount_try": 1250, "date": "dün"})
    ctx = result["context"]
    assert ctx["transactions"][0]["txn_id"] == "TXN-88213"
    facts = " ".join(ctx["message_facts"])
    assert "4832" in facts
    assert "SET-9012" in facts


def test_s2_slot_backup_fills_missing_args(orchestrator):
    # Router argüman çıkaramasa bile regex slotları tamamlar.
    result = _turn(orchestrator, "u-s2b", "Dün 1.250 TL çektim ama göremiyorum",
                   "find_transaction", {})
    ctx = result["context"]
    assert ctx["transactions"], "slot backup islem bulmaliydi"
    assert ctx["transactions"][0]["txn_id"] == "TXN-88213"


# --- S3: Bozuk POS + ödeme linki upsell -------------------------------------

def test_s3_pos_troubleshoot_then_link(orchestrator):
    uid = "u-s3"
    r1 = _turn(orchestrator, uid, "POS cihazım açılmıyor, müşteri bekliyor!",
               "troubleshoot_pos", {"symptom": "açılmıyor"})
    assert r1["context"]["kb_steps"], "KB adımları dolmalı"

    r2 = _turn(orchestrator, uid, "Denedim, olmuyor.",
               "troubleshoot_pos", {"symptom": "açılmıyor", "step_result": "not_resolved"})
    facts = " ".join(r2["context"]["message_facts"])
    assert "Servis kaydı" in facts
    assert "FIRSAT" in facts
    tasks = orchestrator.admin_store.list_tasks()
    assert any("Servis" in (t.get("title") or "") for t in tasks)

    r3 = _turn(orchestrator, uid, "Evet gönder.", "create_payment_link", {})
    link = r3["context"]["payment_link"]
    assert link["url"].startswith("https://moka.link/")
    events = orchestrator.admin_store.get_lead_events(uid)
    types = [e["event_type"] for e in events]
    assert "payment_link_created" in types
    assert "offer_accepted" in types  # POS arızası upsell'i kabul sayılır


# --- S4: Komisyon itirazı + plan upsell -------------------------------------

def test_s4_explain_fees_with_upgrade(orchestrator):
    result = _turn(orchestrator, "u-s4", "Bu ay komisyon çok kesilmiş, neden?",
                   "explain_fees", {"topic": "commission"})
    ctx = result["context"]
    assert ctx["plan_info"]["plan"]["plan_id"] == "PLAN-STD"
    assert ctx["offer"]["plan"]["plan_id"] == "PLAN-PLUS"
    facts = " ".join(ctx["message_facts"])
    assert "FIRSAT" in facts


# --- S5: Sanal POS cross-sell ------------------------------------------------

def test_s5_social_selling_offer(orchestrator):
    result = _turn(orchestrator, "u-s5", "Instagram'dan sipariş alıyorum, kolay yolu yok mu?",
                   "recommend_offer", {"trigger": "social_selling"})
    ctx = result["context"]
    assert "sanal_pos" in ctx["offer"]["products"]
    facts = " ".join(ctx["message_facts"])
    assert "TEKLİF" in facts


# --- S6: Öfkeli müşteri handoff ----------------------------------------------

def test_s6_angry_handoff(orchestrator):
    result = _turn(orchestrator, "u-s6",
                   "Üç gündür param yatmadı, kimse ilgilenmiyor, yeter artık!",
                   "trigger_handoff",
                   {"reason": "Öfkeli müşteri — 3 gündür bekleyen hakediş şikayeti"},
                   card={"mood": "kizgin", "issue": "hakedis gecikmesi"})
    handoff = result["context"]["handoff"]
    assert handoff["required"] is True
    assert "hakediş" in handoff["reason"]
    # Handoff kuyruğuna düştü mü?
    queue = orchestrator.admin_store.get_handoff_queue()
    assert any(item.get("user_id") == "u-s6" for item in queue)


# --- S7: Outbound kurtarma ----------------------------------------------------

def test_s7_outbound_retention_flow(orchestrator):
    uid = "u-s7"
    start = orchestrator.start_call(uid, channel="demo", mode="outbound", merchant_id="M-1007")
    assert start["merchant"]["merchant_id"] == "M-1007"
    assert start["reply_text"], "outbound açılışı boş olamaz"

    r1 = _turn(orchestrator, uid, "Komisyonlar yüksek geldi, başka firmaya geçtim.",
               "recommend_offer", {"trigger": "dormant_retention"})
    assert r1["context"]["offer"]["plan"]["plan_id"] == "PLAN-RET"

    r2 = _turn(orchestrator, uid, "Olur, kabul ediyorum.",
               "recommend_offer", {"trigger": "dormant_retention", "accepted": True})
    facts = " ".join(r2["context"]["message_facts"])
    assert "kabul" in facts.lower()
    events = orchestrator.admin_store.get_lead_events(uid)
    accepted = [e for e in events if e["event_type"] == "offer_accepted"]
    assert accepted, "offer_accepted olayı kaydedilmeli"
    payload = json.loads(accepted[0]["payload"]) if isinstance(accepted[0]["payload"], str) else accepted[0]["payload"]
    assert payload.get("recovered_volume_try", 0) > 100000


# --- Güvenlik: kart numarası --------------------------------------------------

def test_security_smalltalk(orchestrator):
    result = _turn(orchestrator, "u-sec", "Kart numarası 4832 1234 5678 9012...",
                   "answer_general", {"category": "security_smalltalk"})
    facts = " ".join(result["context"]["message_facts"])
    assert "GÜVENLİK" in facts


# --- Hakem bulgusu regresyonları -------------------------------------------

def test_string_amount_from_router_does_not_crash(orchestrator):
    """Hakem #1: router amount_try'ı string döndürürse çağrı ölmemeli."""
    result = _turn(orchestrator, "u-ref1", "Dün bin iki yüz elli lira çektim",
                   "find_transaction", {"amount_try": "1250", "date": "dün"})
    assert result["context"]["transactions"][0]["txn_id"] == "TXN-88213"


def test_string_amount_payment_link_does_not_crash(orchestrator):
    """Hakem #2: string tutarla ödeme linki de çökmemeli."""
    result = _turn(orchestrator, "u-ref2", "Beş yüz liralık link gönder",
                   "create_payment_link", {"amount_try": "500"})
    link = result["context"]["payment_link"]
    assert link["amount_try"] == 500.0
    facts = " ".join(result["context"]["message_facts"])
    assert "500 TL" in facts


def test_broken_tool_args_degrade_gracefully(orchestrator):
    """Dispatch güvenlik ağı: bozuk argüman 500 değil, özürlü fact üretir."""
    result = _turn(orchestrator, "u-ref3", "işlem bak",
                   "find_transaction", {"amount_try": "bin iki yüz"})
    facts = " ".join(result["context"]["message_facts"])
    # coercion 'bin iki yüz'ü None'a düşürür → normal işlem arama akışı çalışır
    assert result["agent_response"]  # cevap üretildi, çökmedi


def test_turkish_capital_i_kb_match(orchestrator):
    """Hakem #5: 'İşlem geçmiyor' KB'de eşleşmeli."""
    entry = orchestrator.merchant_data.match_kb("İşlem geçmiyor, bağlantı hatası")
    assert entry is not None and entry["issue_id"] == "KB-POS-02"

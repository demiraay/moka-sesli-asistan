"""7 demo senaryosunun araç dispatch'ini LLM'siz doğrular.

Router LLM monkeypatch'lenir (sabit JSON), cevap LLM'i "ok" döner; testler
dispatch'in doğru context alanlarını doldurduğunu ve yan etkileri (servis
görevi, outbox, lead_events) üretildiğini kontrol eder.
"""

import json

import pytest

from core.admin_store import AdminStore
from core.llm import LLMResponse, ToolCall
from core.orchestrator import AGENT_LOOP_ENABLED, AgentOrchestrator


class FakeLLM:
    """Planlama fazında kuyruktan tool_call döndürür; cevap fazında 'ok' der.

    push() imzası korundu, böylece S1–S7 senaryo gövdeleri değişmedi.
    """

    def __init__(self):
        self.router_queue = []

    def push(self, tool, args=None, card=None):
        batch = [(tool, args or {})]
        if card:
            # Kart artık bir araç: update_customer_card (alan bazında merge).
            batch.append(("update_customer_card", card))
        self.router_queue.append(batch)

    def chat(self, messages, *, tools=None, tool_choice="auto", json_mode=False,
             profile="default", timeout=25, max_tokens=None):
        if not tools or not self.router_queue:
            # Araç yok ya da kuyruk bitti → düz cevap → planlama döngüsü biter.
            return LLMResponse(content="ok", tool_calls=[], finish_reason="stop")
        batch = self.router_queue.pop(0)
        return LLMResponse(
            content=None, finish_reason="tool_calls",
            tool_calls=[ToolCall(id=f"c{index}", name=name, arguments=args)
                        for index, (name, args) in enumerate(batch)])

    def generate(self, system_prompt, user_prompt, json_mode=False, profile="default"):
        if json_mode:      # AGENT_ENABLED=0 geri dönüş yolu
            batch = self.router_queue.pop(0) if self.router_queue else [("answer_general", {})]
            name, args = batch[0]
            card = dict(batch[1][1]) if len(batch) > 1 else {}
            return json.dumps({"tool": name, "args": args, "card": card},
                              ensure_ascii=False)
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


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="çok adımlı loop kapalı (AGENT_ENABLED=0)")
def test_s2_model_refines_args_across_iterations(orchestrator):
    """Çok adımlı loop'un asıl kanıtı.

    Eskiden bunu regex slot backup'ı yapıyordu: model argüman çıkaramazsa
    SlotMapper tutarı araç argümanlarına enjekte ediyordu. Artık model aracın
    sonucunu GÖRÜP kendisi düzeltiyor — 1. iterasyonda filtresiz arar, dönen
    sonucu okur, 2. iterasyonda tutarla daraltır.
    """
    orchestrator.llm_client.push("find_transaction", {})
    orchestrator.llm_client.push("find_transaction", {"amount_try": 1250, "date": "dün"})
    result = orchestrator.process_turn("Dün 1.250 TL çektim ama göremiyorum",
                                       user_id="u-s2b", channel="demo")

    decision = result["router_decision"]
    assert decision["iterations"] == 2, "model iki tur çalışmalıydı"
    assert [t["name"] for t in decision["tools"]] == ["find_transaction", "find_transaction"]
    assert decision["stop_reason"] == "done"
    assert result["context"]["transactions"][0]["txn_id"] == "TXN-88213"


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
    """Coercion 'bin iki yüz'ü None'a düşürür → normal arama akışı SÜRER
    (aksaklık fact'i üretilmez, işlem listesi döner)."""
    result = _turn(orchestrator, "u-ref3", "işlem bak",
                   "find_transaction", {"amount_try": "bin iki yüz"})
    facts = " ".join(result["context"]["message_facts"])
    assert "aksaklık" not in facts, "coercion çalışsaydı güvenlik ağına düşmezdi"
    assert result["context"]["transactions"], "tutar filtresiz arama işlem bulmalıydı"


def test_dispatch_safety_net_catches_handler_crash(orchestrator, monkeypatch):
    """Handler gerçekten patlarsa: 500 yok, özürlü fact + cevap var."""
    from core import tools

    def boom(*args, **kwargs):
        raise RuntimeError("kasıtlı patlama")
    # Handler'lar artık registry'de yaşıyor (core/tools/handlers.py).
    monkeypatch.setattr(tools.REGISTRY["find_transaction"], "fn", boom)
    result = _turn(orchestrator, "u-ref4", "işlem bak",
                   "find_transaction", {"amount_try": 1250})
    facts = " ".join(result["context"]["message_facts"])
    assert "aksaklık" in facts
    assert result["agent_response"]


def test_turkish_capital_i_kb_match(orchestrator):
    """Hakem #5: 'İşlem geçmiyor' KB'de eşleşmeli."""
    entry = orchestrator.merchant_data.match_kb("İşlem geçmiyor, bağlantı hatası")
    assert entry is not None and entry["issue_id"] == "KB-POS-02"


# --- Operator konsolu: isletme DEGIL -------------------------------------

def test_ops_console_has_no_merchant_identity(orchestrator):
    """Operator hatta bir isletme degildir.

    Aksi halde asistan operatore "Mehmet Bey" diye hitap eder ve M-1001'in
    verisini onun verisi sanardi.
    """
    orchestrator.llm_client.push("find_dormant_merchants", {})
    result = orchestrator.process_turn("Kimleri aramaliyiz?",
                                       user_id="ops-console", channel="ops")

    profile = orchestrator.user_profiles["ops-console"]
    assert profile["merchant_id"] is None
    assert profile["merchant_display"] == "Moka operasyon ekibi"
    assert result["agent_response"]


def test_merchant_call_still_resolves_identity(orchestrator):
    """Regresyon: normal cagrida isletme cozumlemesi bozulmadi."""
    orchestrator.llm_client.push("get_settlement_status", {"period": "latest"})
    orchestrator.process_turn("Param ne zaman yatacak?", user_id="u-ops-reg", channel="demo")
    assert orchestrator.user_profiles["u-ops-reg"]["merchant_id"] == "M-1001"


# --- Uydurma korumasi -----------------------------------------------------

@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_turn_without_tools_warns_against_claiming_actions(orchestrator):
    """Hicbir arac calismadiysa cevap modeli EYLEM IDDIA ETMEMELI.

    Canli provada gorulen hata: model 'Evet, link gonderin' turunda araci
    cagirmadi ama "linki olusturdum, SMS gonderdim" dedi — link yoktu.
    """
    result = orchestrator.process_turn("Evet, gönderin", user_id="u-noop", channel="demo")
    facts = " ".join(result["context"]["message_facts"])
    assert "HİÇBİR İŞLEM YAPILMADI" in facts
    assert "ASLA söyleme" in facts


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_card_only_turn_also_warns(orchestrator):
    """Sadece hafiza guncellendiyse de fiilen bir sey YAPILMAMISTIR."""
    orchestrator.llm_client.push("update_customer_card", {"mood": "gergin"})
    result = orchestrator.process_turn("Biraz sinirliyim", user_id="u-cardonly", channel="demo")
    facts = " ".join(result["context"]["message_facts"])
    assert "HİÇBİR İŞLEM YAPILMADI" in facts


def test_real_tool_turn_has_no_such_warning(orchestrator):
    result = _turn(orchestrator, "u-realtool", "Param ne zaman yatacak?",
                   "get_settlement_status", {"period": "latest"})
    facts = " ".join(result["context"]["message_facts"])
    assert "HİÇBİR İŞLEM YAPILMADI" not in facts


# --- Planlayici transkripti (araç sonucu hafızası) -------------------------

@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_previous_tool_results_reach_the_next_turn(orchestrator):
    """Asıl kazanım: model geçen turun ARAÇ SONUCUNU görür.

    Eskiden transkript her turda sıfırdan kuruluyordu; yalnızca düz metin
    mesajlar veriliyor, tool_calls ve tool sonuçları atılıyordu. Model
    "geçen sefer ne bulduğunu" Ada'nın prozasından çıkarmak zorundaydı.
    """
    seen = {}

    real_chat = orchestrator.llm_client.chat

    def spy(messages, **kwargs):
        seen["messages"] = list(messages)
        return real_chat(messages, **kwargs)

    _turn(orchestrator, "u-mem", "Dün 1250 TL çektim",
          "find_transaction", {"amount_try": 1250})

    orchestrator.llm_client.chat = spy
    orchestrator.llm_client.push("get_settlement_status", {"period": "latest"})
    orchestrator.process_turn("Peki o para ne zaman yatacak?",
                              user_id="u-mem", channel="demo")

    roles = [m["role"] for m in seen["messages"]]
    assert "tool" in roles, "önceki turun araç sonucu bağlamda yok"

    tool_messages = [m for m in seen["messages"] if m["role"] == "tool"]
    assert any(m["name"] == "find_transaction" for m in tool_messages)
    # Sonucun kendisi de duruyor mu (yalnızca araç adı değil)?
    assert any("islem bulundu" in (m.get("content") or "").lower()
               for m in tool_messages)


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_transcript_records_the_reply_the_customer_heard(orchestrator):
    """Planlama fazının düz yazısı ATILIR; transkripte NİHAİ cevap girmeli."""
    result = _turn(orchestrator, "u-said", "Param ne zaman yatacak?",
                   "get_settlement_status", {"period": "latest"})

    transcript = orchestrator.planner_transcripts["demo:u-said"]
    assistant_texts = [m.get("content") for m in transcript
                       if m["role"] == "assistant" and m.get("content")]
    assert result["agent_response"] in assistant_texts


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_transcript_shape_is_a_valid_tool_exchange(orchestrator):
    _turn(orchestrator, "u-shape", "Param ne zaman yatacak?",
          "get_settlement_status", {"period": "latest"})
    transcript = orchestrator.planner_transcripts["demo:u-shape"]

    assert transcript[0]["role"] == "user"
    # Her tool mesajı, kendisini doğuran assistant tool_call'ına bağlı olmalı
    call_ids = {c["id"] for m in transcript if m.get("tool_calls")
                for c in m["tool_calls"]}
    for message in transcript:
        if message["role"] == "tool":
            assert message["tool_call_id"] in call_ids, "yetim tool mesajı"


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_reset_clears_the_transcript(orchestrator):
    _turn(orchestrator, "u-reset", "Param ne zaman yatacak?",
          "get_settlement_status", {"period": "latest"})
    assert orchestrator.planner_transcripts["demo:u-reset"]
    orchestrator.reset_conversation("u-reset", channel="demo")
    assert orchestrator.planner_transcripts["demo:u-reset"] == []


@pytest.mark.skipif(not AGENT_LOOP_ENABLED,
                    reason="planlayıcı transkripti yalnızca agent loop'ta var")
def test_tool_results_are_persisted_for_restart_recovery(orchestrator):
    """Süreç yeniden başlarsa transkript DB'den kurulabilmeli."""
    _turn(orchestrator, "u-persist", "Dün 1250 TL çektim",
          "find_transaction", {"amount_try": 1250})

    session_id = orchestrator._get_session_id("u-persist", "demo")
    turn = orchestrator.admin_store.get_conversation(session_id)["turns"][-1]
    stored = turn["router_decision"]["tools"][0]
    assert stored["name"] == "find_transaction"
    assert stored["result"], "araç sonucu DB'ye yazılmamış"

    # Bellek soğukken DB'den kurulan transkript araç sonucunu taşımalı
    orchestrator.planner_transcripts.clear()
    rebuilt = orchestrator._get_planner_transcript("u-persist", "demo")
    assert any(m["role"] == "tool" and m.get("content") for m in rebuilt)


# --- Tekrar korumasının kalıcılığı ve oturum kapsamı ----------------------

def _fresh_orchestrator(store):
    """Süreç yeniden başlamış gibi: bellek boş, DB aynı."""
    orch = AgentOrchestrator()
    orch.admin_store = store
    orch.llm_client = FakeLLM()
    orch.conversation_histories.clear()
    orch.planner_transcripts.clear()
    orch.user_profiles.clear()
    orch.active_sessions.clear()
    orch.call_contexts.clear()
    return orch


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="guard'lar agent loop'ta")
def test_once_per_call_guard_survives_restart(orchestrator):
    """Yeniden başlatma AYNI görüşmede ikinci ekstreye izin vermemeli."""
    _turn(orchestrator, "u-guard", "Ekstremi gönderir misin?",
          "send_statement", {"period": "this_month"})
    assert orchestrator.user_profiles["u-guard"].get("_once_send_statement")

    revived = _fresh_orchestrator(orchestrator.admin_store)
    revived.llm_client.push("send_statement", {"period": "this_month"})
    result = revived.process_turn("Tekrar gönderir misin?",
                                  user_id="u-guard", channel="demo")

    executed = [t for t in result["router_decision"]["tools"]
                if t["name"] == "send_statement"]
    assert executed and executed[0]["suppressed"], "ikinci ekstre engellenmeliydi"


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="guard'lar agent loop'ta")
def test_offer_made_survives_restart(orchestrator):
    _turn(orchestrator, "u-offer", "Instagram'dan satıyorum",
          "recommend_offer", {"trigger": "social_selling"})
    assert orchestrator.user_profiles["u-offer"]["offer_made"] is True

    revived = _fresh_orchestrator(orchestrator.admin_store)
    revived._get_user_profile("u-offer")
    revived._apply_restored_state(
        revived.user_profiles["u-offer"],
        revived._get_session_id("u-offer", "demo"))
    assert revived.user_profiles["u-offer"]["offer_made"] is True


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="guard'lar agent loop'ta")
def test_guards_do_not_leak_into_a_new_conversation(orchestrator):
    """Yeni görüşmede müşteri TEKRAR ekstre isteyebilmeli.

    Guard koşulsuz kalıcı olsaydı, oturum hiç bitmediği için müşteri bir daha
    asla ekstre alamazdı.
    """
    _turn(orchestrator, "u-newconv", "Ekstremi gönder",
          "send_statement", {"period": "this_month"})

    revived = _fresh_orchestrator(orchestrator.admin_store)
    profile = revived._get_user_profile("u-newconv")
    revived._apply_restored_state(profile, "bambaska-bir-oturum")

    assert not profile.get("_once_send_statement")
    assert not profile.get("offer_made")


def test_idle_session_expires_and_starts_a_new_one(orchestrator):
    """Uzun sessizlikten sonra gelen mesaj YENİ görüşme sayılır."""
    from datetime import datetime, timedelta, timezone
    from core import db

    _turn(orchestrator, "u-idle", "Merhaba", "answer_general", {"category": "greeting"})
    old_session = orchestrator._get_session_id("u-idle", "demo")

    # Oturumu 2 gün öncesine çek
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with db.session(orchestrator.admin_store.db_path) as connection:
        connection.execute(
            "UPDATE conversation_sessions SET last_message_at = ? WHERE session_id = ?",
            (stale, old_session))

    orchestrator.active_sessions.clear()
    assert orchestrator._get_session_id("u-idle", "demo") != old_session


def test_recent_session_is_reused(orchestrator):
    _turn(orchestrator, "u-recent", "Merhaba", "answer_general", {"category": "greeting"})
    session = orchestrator._get_session_id("u-recent", "demo")
    orchestrator.active_sessions.clear()
    assert orchestrator._get_session_id("u-recent", "demo") == session


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="engelleme agent loop'ta")
def test_suppressed_tool_is_explained_to_the_composer(orchestrator):
    """Engellenen araç cevap modeline AÇIKÇA bildirilmeli.

    Canlı provada görülen: guard ekstreyi engelledi ama Ada "tekrar
    iletiyorum" dedi — hiçbir şey gönderilmemişti.
    """
    _turn(orchestrator, "u-supp", "Ekstremi gönder",
          "send_statement", {"period": "this_month"})

    orchestrator.llm_client.push("send_statement", {"period": "this_month"})
    result = orchestrator.process_turn("Bir daha gönder", user_id="u-supp", channel="demo")

    facts = " ".join(result["context"]["message_facts"])
    assert "TEKRAR ENGELLENDI" in facts
    assert "ZATEN" in facts
    assert "ekstre gönderildi" in facts  # panel etiketi, hangi işlem olduğu belli


# --- Konuşma bağlamı oturuma ait ------------------------------------------

@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="kart agent loop'ta yönetiliyor")
def test_old_card_does_not_leak_into_a_new_conversation(orchestrator):
    """YENİ konuşma, eskisinin bağlamıyla başlamamalı.

    Gerçek hata: müşteri "selam" dedi, ajan ona kendisinin hiç söylemediği
    "44 bin 104 liralık tutarın tarihini" sordu — kart önceki konuşmadan
    kalmıştı ve "tutar biliniyor, tarih eksik" diyordu.
    """
    _turn(orchestrator, "u-kart", "Dün 1250 TL çektim",
          "find_transaction", {"amount_try": 1250})
    assert orchestrator.user_profiles["u-kart"]["card"]["amount_mentioned_try"] == 1250

    revived = _fresh_orchestrator(orchestrator.admin_store)
    profile = revived._get_user_profile("u-kart")
    revived._apply_restored_state(profile, "yepyeni-bir-oturum")

    assert not profile.get("card"), "eski kart yeni konuşmaya taşındı"
    assert profile.get("conversation_focus") is None
    assert profile.get("pending_offer") is None


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="kart agent loop'ta yönetiliyor")
def test_card_survives_a_restart_within_the_same_conversation(orchestrator):
    """Aynı görüşmede ise kart KORUNMALI (yeniden başlatmaya dayanıklılık)."""
    _turn(orchestrator, "u-kart2", "Dün 1250 TL çektim",
          "find_transaction", {"amount_try": 1250})
    session = orchestrator._get_session_id("u-kart2", "demo")

    revived = _fresh_orchestrator(orchestrator.admin_store)
    profile = revived._get_user_profile("u-kart2")
    revived._apply_restored_state(profile, session)

    assert profile["card"]["amount_mentioned_try"] == 1250


def test_identity_persists_across_conversations(orchestrator):
    """Kimlik görüşmeye değil KİŞİYE aittir; o taşınmalı."""
    _turn(orchestrator, "u-kimlik", "Merhaba", "answer_general", {"category": "greeting"})

    revived = _fresh_orchestrator(orchestrator.admin_store)
    profile = revived._get_user_profile("u-kimlik")
    revived._apply_restored_state(profile, "bambaska-oturum")
    assert profile.get("merchant_id") == "M-1001"


# --- Kimlik dogrulama: taninmayana isimle hitap etme ----------------------

def test_unverified_caller_is_not_greeted_by_name(orchestrator):
    """Kimlik cozulemeyince (varsayilana dusulunce) isimle hitap YASAK.

    Gercek hata: tanidigi herkese "Mehmet Bey" diyordu.
    """
    orchestrator.llm_client.push("answer_general", {"category": "greeting"})
    result = orchestrator.process_turn("selam", user_id="taninmayan-lid",
                                       channel="whatsapp")
    profile = orchestrator.user_profiles["taninmayan-lid"]
    assert profile["identity_verified"] is False

    block = orchestrator._build_merchant_profile_block(
        orchestrator.merchant_data.get_merchant(profile["merchant_id"]),
        "taninmayan-lid", identity_verified=False)
    assert "ISIMLE HITAP ETME" in block
    assert "DOGRULANMADI" in block


def test_verified_caller_keeps_the_salutation(orchestrator):
    """Panelden secilen (call_context) isletme dogrulanmis sayilir."""
    orchestrator.set_call_context("u-ver", "M-1001", mode="inbound")
    orchestrator.llm_client.push("answer_general", {"category": "greeting"})
    orchestrator.process_turn("selam", user_id="u-ver", channel="voice")

    profile = orchestrator.user_profiles["u-ver"]
    assert profile["identity_verified"] is True

    block = orchestrator._build_merchant_profile_block(
        orchestrator.merchant_data.get_merchant("M-1001"), "u-ver",
        identity_verified=True)
    assert "hitap:" in block
    assert "DOGRULANMADI" not in block


def test_phone_match_is_verified(orchestrator):
    """Kayitli telefonla eslesen arayan dogrulanmistir."""
    row = orchestrator.merchant_data._query_one(
        "SELECT phone, merchant_id FROM merchants WHERE phone_e164 != '' LIMIT 1")
    orchestrator.llm_client.push("answer_general", {"category": "greeting"})
    profile = orchestrator._get_user_profile("u-phone")
    profile["phone_number"] = row["phone"]
    orchestrator._resolve_merchant("u-phone", profile)
    assert profile["identity_verified"] is True
    assert profile["merchant_id"] == row["merchant_id"]


@pytest.mark.skipif(not AGENT_LOOP_ENABLED, reason="kimlik agent loop yolunda")
def test_unverified_stays_unverified_across_turns(orchestrator):
    """Onceki turda varsayimla dusen merchant, sonraki turda 'dogrulandi'
    sayilmamali. Gercek hata: 2. mesajda birden "Muhammed Bey" diyordu."""
    for _ in range(3):
        orchestrator.llm_client.push("answer_general", {"category": "greeting"})
        orchestrator.process_turn("selam", user_id="wa-lid-xyz", channel="whatsapp")
        assert orchestrator.user_profiles["wa-lid-xyz"]["identity_verified"] is False

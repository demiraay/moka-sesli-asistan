"""Destek domaini regex katmani: SlotMapper + IntentParser."""

from core.intent import IntentParser
from core.slots import SlotMapper


def test_slots_amount_and_date():
    slots = SlotMapper().extract("Dün 1.250 TL çektim ama hesapta göremiyorum")
    assert slots["amount_try"] == 1250
    assert slots["date"] == "dün"


def test_slots_decimal_amount_and_weekday():
    slots = SlotMapper().extract("Salı günü 500,50 TL'lik işlem iade oldu mu?")
    assert slots["amount_try"] == 500.5
    assert slots["date"] == "salı"


def test_slots_terminal_and_last4():
    slots = SlotMapper().extract("TRM-4451 cihazım bozuk, 4832 ile biten karttan çekim vardı")
    assert slots["terminal_id"] == "TRM-4451"
    assert slots["card_last4"] == "4832"


def test_intents_support_domain():
    parser = IntentParser()
    assert "settlement" in parser.parse("Param ne zaman yatacak?")
    assert "transaction" in parser.parse("Dün çektim ama göremiyorum")
    assert "pos_issue" in parser.parse("POS cihazım açılmıyor")
    assert "fees" in parser.parse("Komisyon neden bu kadar yüksek?")
    assert "statement" in parser.parse("Ekstre gönderir misiniz")
    anger = parser.parse("Yeter artık, sizi şikayet edeceğim, temsilci bağlayın!")
    assert "anger" in anger and "human_request" in anger

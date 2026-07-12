from typing import List, Dict, Any

TOOLS_SCHEMA = [
    {
        "name": "get_settlement_status",
        "description": "Call this tool whenever the merchant asks about their payout/settlement (hakediş): when money will arrive, how much, why it hasn't arrived.\n\nEXAMPLES:\n- User: 'Param ne zaman yatacak?' -> Args: {'period': 'latest'}\n- User: 'Dünkü satışların parası yattı mı?' -> Args: {'period': 'latest'}\n- User: 'Bekleyen ödemem var mı?' -> Args: {'period': 'pending'}\n- User: 'Üç gündür param yatmadı' -> Args: {'period': 'pending'}\n- User: 'Geçen haftaki hakedişlerimi göster' -> Args: {'period': 'last_week'}",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Which settlements to look at. 'latest' = most recent batch (default), 'pending' = not yet paid out, 'last_week' = last 7 days.",
                    "enum": ["latest", "pending", "last_week"]
                }
            }
        }
    },
    {
        "name": "find_transaction",
        "description": "Call this tool when the merchant asks about a SPECIFIC card transaction: a sale they can't see, a refund, a cancelled payment.\n\nEXAMPLES:\n- User: 'Dün 1.250 TL çektim ama göremiyorum' -> Args: {'amount_try': 1250, 'date': 'dün'}\n- User: 'Bugün saat 2'deki işlem geçti mi?' -> Args: {'date': 'bugün'}\n- User: '4832 ile biten karttan çekilen para nerede?' -> Args: {'card_last4': '4832'}\n- User: 'İade ettiğim işlem ne durumda?' -> Args: {'status': 'iade'}",
        "parameters": {
            "type": "object",
            "properties": {
                "amount_try": {
                    "type": "number",
                    "description": "Transaction amount in TL if the merchant mentioned one (e.g. 1250)."
                },
                "date": {
                    "type": "string",
                    "description": "Day of the transaction: 'bugün', 'dün' or an ISO date."
                },
                "card_last4": {
                    "type": "string",
                    "description": "Last 4 digits of the customer card if mentioned."
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status if the merchant asks about refunds/cancellations.",
                    "enum": ["onaylandı", "iade", "iptal", "beklemede"]
                }
            }
        }
    },
    {
        "name": "troubleshoot_pos",
        "description": "Call this tool when the merchant reports a device or integration problem: POS not turning on, connection errors, printer/slip problems, virtual POS 3D errors.\n\nEXAMPLES:\n- User: 'POS cihazım açılmıyor' -> Args: {'symptom': 'açılmıyor'}\n- User: 'Cihaz bağlanmıyor, işlem geçmiyor' -> Args: {'symptom': 'bağlanmıyor işlem geçmiyor'}\n- User: 'Denedim, yine olmadı' (after steps were already given for a POS issue) -> Args: {'symptom': '<same symptom as before>', 'step_result': 'not_resolved'}\n- User: 'Tamam düzeldi, çalıştı' (after steps) -> Args: {'symptom': '<same symptom>', 'step_result': 'resolved'}",
        "parameters": {
            "type": "object",
            "required": ["symptom"],
            "properties": {
                "symptom": {
                    "type": "string",
                    "description": "The problem in the merchant's words (Turkish), e.g. 'açılmıyor', 'bağlantı hatası', 'fiş yazmıyor'."
                },
                "terminal_id": {
                    "type": "string",
                    "description": "Terminal ID like TRM-4451 if the merchant mentioned it."
                },
                "step_result": {
                    "type": "string",
                    "description": "Set ONLY on a follow-up turn: 'not_resolved' if the merchant says the given steps did not work (a service ticket will be created), 'resolved' if fixed.",
                    "enum": ["resolved", "not_resolved"]
                }
            }
        }
    },
    {
        "name": "explain_fees",
        "description": "Call this tool when the merchant asks about commissions, deductions or their plan: why an amount was cut, what their rate is, plan details.\n\nEXAMPLES:\n- User: 'Bu komisyon neden bu kadar çok?' -> Args: {'topic': 'commission'}\n- User: 'Bu ay ne kadar kesinti olmuş?' -> Args: {'topic': 'deduction'}\n- User: 'Benim planım ne, oranım kaç?' -> Args: {'topic': 'plan_details'}",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "'commission' = why/how much commission, 'deduction' = total cuts this month, 'plan_details' = current plan info.",
                    "enum": ["commission", "deduction", "plan_details"]
                }
            }
        }
    },
    {
        "name": "send_statement",
        "description": "Call this tool when the merchant asks for a statement/report (ekstre, döküm, hesap özeti) to be sent.\n\nEXAMPLES:\n- User: 'Bu ayın ekstresini gönder' -> Args: {'period': 'this_month'}\n- User: 'Geçen ayın dökümünü mail at' -> Args: {'period': 'last_month'}",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Which month's statement.",
                    "enum": ["this_month", "last_month"]
                }
            }
        }
    },
    {
        "name": "create_payment_link",
        "description": "Call this tool when the merchant wants a payment link created NOW (or accepts the assistant's offer to create one, e.g. while their POS is broken).\n\nEXAMPLES:\n- User: 'Bana bir ödeme linki oluştur' -> Args: {}\n- User: '500 liralık link gönder' -> Args: {'amount_try': 500}\n- User: 'Evet gönder, link olsun' (accepting the assistant's payment-link offer) -> Args: {}",
        "parameters": {
            "type": "object",
            "properties": {
                "amount_try": {
                    "type": "number",
                    "description": "Fixed amount in TL if the merchant specified one; omit for an open-amount link."
                },
                "description": {
                    "type": "string",
                    "description": "Short note for the link if given."
                }
            }
        }
    },
    {
        "name": "recommend_offer",
        "description": "REVENUE TOOL. Call this when there is a concrete opportunity to offer the merchant a better/new Moka product, matching one of the triggers:\n\nEXAMPLES:\n- Merchant's issue is resolved AND their volume has clearly grown / they complain commission is high -> Args: {'trigger': 'volume_growth'}\n- User: 'Instagram'dan sipariş alıyorum, havale ile uğraşıyorum' -> Args: {'trigger': 'social_selling'}\n- User: 'Komisyonlar yüksek, başka firmaya geçtim/geçeceğim' (churn signal, esp. on outbound retention calls) -> Args: {'trigger': 'dormant_retention'}\n- User accepts a retention/plan offer ('olur', 'kabul ediyorum', 'geçelim') -> Args: {'trigger': '<same trigger as before>', 'accepted': true}\n- Merchant's POS is broken/in service and they risk losing sales -> Args: {'trigger': 'pos_out_of_service'}\n\nDO NOT call this before the merchant's actual problem is addressed.",
        "parameters": {
            "type": "object",
            "required": ["trigger"],
            "properties": {
                "trigger": {
                    "type": "string",
                    "description": "What opportunity was detected.",
                    "enum": ["volume_growth", "social_selling", "dormant_retention", "pos_out_of_service"]
                },
                "accepted": {
                    "type": "boolean",
                    "description": "Set true ONLY when the merchant explicitly accepts the previously made offer in this turn."
                }
            }
        }
    },
    {
        "name": "trigger_handoff",
        "description": "Call this tool IMMEDIATELY to hand the call to a human representative when:\n\n- The merchant is ANGRY or fed up: 'yeter artık', 'rezalet', 'sizi şikayet edeceğim', repeated complaints, insults.\n- Fraud/security concern: 'kartımı kopyalamışlar', suspicious transactions, stolen device.\n- Chargeback/dispute: 'müşteri parasını geri istiyor, itiraz açmış'.\n- Legal threat: 'avukatıma gidiyorum', 'mahkemeye vereceğim'.\n- Account closure request: 'hesabımı kapatın', 'sözleşmeyi feshedeceğim' (try ONE retention response first if mood allows).\n- The troubleshooting steps failed twice, or the issue is beyond the available tools.\n- The merchant explicitly asks for a human: 'müşteri temsilcisine bağla', 'bir insanla görüşmek istiyorum'.\n\nDO NOT use for ordinary questions the tools can answer.",
        "parameters": {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Clear Turkish reason for handoff, e.g. 'Öfkeli müşteri — 3 gündür bekleyen hakediş şikayeti'."
                },
                "missing_info": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Missing info items from EKSIK if any (usually empty — merchant identity comes from the line)."
                }
            }
        }
    },
    {
        "name": "answer_general",
        "description": "Call this tool for greetings, small talk, questions about Moka/how things work, or anything not covered by other tools.\n\nEXAMPLES:\n- User: 'Merhaba' -> Category: 'greeting'\n- User: 'Siz kimsiniz, Moka nedir?' -> Category: 'company_info'\n- User: 'Ödeme linki nasıl çalışıyor?' -> Category: 'how_it_works'\n- User: 'Hafta sonu da çalışıyor musunuz?' -> Category: 'working_hours'\n- User starts reading a FULL card number (16 digits) -> Category: 'security_smalltalk' (the assistant must interrupt and warn!)\n- User: 'Teşekkürler, iyi günler' -> Category: 'thanks'",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category of the message.",
                    "enum": ["greeting", "company_info", "how_it_works", "working_hours", "security_smalltalk", "thanks", "other"]
                }
            }
        }
    }
]


def get_router_system_prompt() -> str:
    """
    Returns the system prompt for the Tool Selection LLM.
    """
    import json
    return f"""You are a smart orchestrator for the AI support agent of Moka United, a Turkish payment company. The caller is a MERCHANT (isletme sahibi) whose identity is already known from the phone line. Your job is to analyze the User Input and select the correct TOOL to execute.

AVAILABLE TOOLS:
{json.dumps(TOOLS_SCHEMA, indent=2, ensure_ascii=False)}

INSTRUCTIONS:
1. Output MUST be valid JSON only. No markdown, no explanations.
2. Format: {{ "tool": "tool_name", "args": {{ "param": "value" }}, "card": {{ ... }} }}
   The "card" field maintains the caller's memory profile. Rules for "card":
   - Look at "MUSTERI KARTI" in the user message (the current card) and update it from the new message.
   - If something CHANGED (e.g. a new issue), REPLACE the old value — never keep both.
   - Keep unchanged fields as they are; add new info. Only record what the caller explicitly said; use null when unknown. Never invent.
   - Card fields (all optional): owner_name, business_name, issue (one short Turkish phrase, e.g. "POS acilmiyor"), amount_mentioned_try (number), date_mentioned (e.g. "dün"), terminal_id, card_last4, mood ("sakin"/"gergin"/"kizgin"), upsell_opportunity (short phrase or null), changed (list of what changed in THIS message; [] if nothing).
   - If nothing changed, still echo the current card unchanged (with changed: []).
3. If the user asks multiple things, pick the most critical one (device down > money questions > general).
4. ANGER RULE: if mood is "kizgin" AND the complaint is unresolved (or this is a repeated complaint), choose "trigger_handoff" with a clear reason. A calm question about the same topic is NOT a handoff.
5. SECURITY RULE: if the caller starts reading a full card number (16 digits) or asks you to store card data, choose "answer_general" with category "security_smalltalk" — the assistant will interrupt and warn.
6. REVENUE RULE: after the caller's actual issue is addressed, if the message contains a growth signal (ciro artisi, komisyon yuksek ama ciro buyumus), social selling (Instagram/internetten satis), churn intent (baska firmaya gectim/gecegim), or a broken POS blocking sales, call "recommend_offer" with the matching trigger. Never offer before the problem is handled.
7. FOLLOW-UP RULE: interpret short replies from context. "Denedim olmadi" after troubleshooting steps -> troubleshoot_pos with step_result "not_resolved". "Evet gonder" after a payment-link offer -> create_payment_link. "Olur, kabul ediyorum" after a plan/retention offer -> recommend_offer with the same trigger and accepted: true.
8. Use conversation context, not only literal keywords. Resolve pronouns ("o islem", "bu cihaz") from context.
9. INFO SUFFICIENCY: The user message includes "ELIMDEKI BILGILER" (BILINEN = already known, EKSIK = missing). For "trigger_handoff", "missing_info" may ONLY contain items from EKSIK — never list something already in BILINEN. The merchant's identity, business and phone are ALWAYS known from the line — never ask for them and never list them as missing.
10. NEVER invent amounts, dates or transaction details in args — only use what the caller said.

TURKISH -> TOOL MAPPING:
- "param ne zaman yatacak", "hakedis", "yatan para", "param yatmadi" -> get_settlement_status
- "cektim", "islem", "goremiyorum", "iade", "iptal" (a specific transaction) -> find_transaction
- "cihaz", "pos", "acilmiyor", "baglanmiyor", "fis yazmiyor", "3d hatasi" -> troubleshoot_pos
- "komisyon", "kesinti", "oran", "ucret" -> explain_fees
- "ekstre", "dokum", "hesap ozeti" -> send_statement
- "odeme linki olustur/gonder" -> create_payment_link
- "temsilci", "insan", "yetkili", anger, fraud, legal -> trigger_handoff
- greetings, Moka hakkinda, nasil calisir -> answer_general
"""

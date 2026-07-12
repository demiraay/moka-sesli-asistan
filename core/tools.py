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


# Router LLM'e giden SIKISTIRILMIS arac rehberi. Tam JSON semasi (TOOLS_SCHEMA)
# ~4.5K token tutuyor ve Groq free tier'in dakikalik token limitini (TPM) tek
# turda yiyordu; router icin ayni bilgi bu kisa rehberle verilir.
COMPACT_TOOL_GUIDE = """1. get_settlement_status{period: latest|pending|last_week} — hakedis/para yatma sorulari. "Param ne zaman yatacak"->latest, "param yatmadi/bekleyen"->pending.
2. find_transaction{amount_try?, date?, card_last4?, status?: onaylandı|iade|iptal|beklemede} — belirli bir islem: "dun 1250 TL cektim goremiyorum"->{amount_try:1250,date:"dün"}. "4832 ile biten"->{card_last4:"4832"}.
3. troubleshoot_pos{symptom, terminal_id?, step_result?: resolved|not_resolved} — cihaz/entegrasyon arizasi (acilmiyor, baglanmiyor, fis yazmiyor, 3d hatasi). Adimlar verildikten SONRA "denedim olmadi"->step_result:"not_resolved", "duzeldi"->"resolved" (ayni symptom ile).
4. explain_fees{topic: commission|deduction|plan_details} — komisyon/kesinti/plan sorulari.
5. send_statement{period: this_month|last_month} — ekstre/dokum gonderimi.
6. create_payment_link{amount_try?, description?} — musteri odeme linki istedi VEYA asistanin link teklifini kabul etti ("evet gonder").
7. recommend_offer{trigger: volume_growth|social_selling|dormant_retention|pos_out_of_service, accepted?} — GELIR araci, sadece sorun cozuldukten sonra: ciro buyumesi/komisyon itirazi->volume_growth; "Instagram'dan satiyorum"->social_selling; "baska firmaya gectim" (churn)->dormant_retention; bozuk POS satis kaybettiriyor->pos_out_of_service. Musteri onceki teklifi kabul ederse ("olur/kabul") ayni trigger + accepted:true.
8. trigger_handoff{reason, missing_info?} — OFKELI musteri ("yeter artik", "sikayet edecegim"), fraud/guvenlik, chargeback, hukuki tehdit, hesap kapatma, iki kez cozulemeyen ariza, acik insan talebi ("temsilci baglayin"). Siradan sorular icin KULLANMA.
9. answer_general{category: greeting|company_info|how_it_works|working_hours|security_smalltalk|thanks|other} — selamlasma, Moka bilgisi, tesekkur. Musteri TAM kart numarasi okumaya baslarsa -> security_smalltalk (asistan kesip uyaracak)."""


def get_router_system_prompt() -> str:
    """
    Returns the system prompt for the Tool Selection LLM.
    """
    return f"""You are a smart orchestrator for the AI support agent of Moka United, a Turkish payment company. The caller is a MERCHANT (isletme sahibi) whose identity is already known from the phone line. Your job is to analyze the User Input and select the correct TOOL to execute.

AVAILABLE TOOLS (name{{args}} — when to use):
{COMPACT_TOOL_GUIDE}

INSTRUCTIONS:
1. Output MUST be valid JSON only. No markdown. Format: {{ "tool": "...", "args": {{...}}, "card": {{...}} }}
2. "card" = caller memory. Update the "MUSTERI KARTI" from the new message: replace changed values (never keep both), keep the rest, never invent, null when unknown. Fields (all optional): owner_name, business_name, issue (short Turkish phrase), amount_mentioned_try (number), date_mentioned, terminal_id, card_last4, mood ("sakin"/"gergin"/"kizgin"), upsell_opportunity, changed (what changed THIS message; [] if nothing).
3. Multiple asks -> pick the most critical (device down > money > general).
4. ANGER: mood "kizgin" + unresolved/repeated complaint -> trigger_handoff. A calm question is NOT a handoff.
5. REVENUE: offer only AFTER the actual issue is addressed (rule 7 in the tool guide).
6. FOLLOW-UPS: interpret short replies from recent conversation (see tool guide items 3, 6, 7).
7. INFO SUFFICIENCY: "ELIMDEKI BILGILER" lists BILINEN/EKSIK. missing_info may only contain EKSIK items. Merchant identity/business/phone are ALWAYS known from the line — never ask, never list as missing.
8. NEVER invent amounts, dates or details in args — only what the caller said. Resolve pronouns ("o islem") from context.
"""

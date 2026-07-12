from typing import List, Dict, Any

TOOLS_SCHEMA = [
    {
        "name": "search_inventory",
        "description": "CRITICAL: Call this tool WHENEVER the user asks about available flats, stock status, 'empty' units, or specific unit types. \n\nEXAMPLES:\n- User: 'Hangi evler boş?' -> Args: {'status': 'available'}\n- User: '3+1 var mı?' -> Args: {'flat_type_id': 'FT-3P1', 'status': 'available'}\n- User: 'En ucuz daire hangisi?' -> Args: {'status': 'available', 'sort_by': 'price_asc'}\n- User: '5. katta daire var mı?' -> Args: {'floor': 5, 'status': 'available'}\n- User: '10 Milyondan ucuz evler' -> Args: {'max_price': 10000000, 'status': 'available'}",
        "parameters": {
            "type": "object",
            "properties": {
                "flat_type_id": {
                    "type": "string",
                    "description": "Type of flat. Mapped to IDs: 'FT-1P1' (1+1), 'FT-2P1' (2+1), 'FT-3P1' (3+1), 'FT-4P1' (4+1), 'FT-DUP' (Duplex/Dubleks)."
                },
                "floor": {
                    "type": "integer",
                    "description": "Specific floor number (e.g. 1, 2, 3). 'Zemin' or 'Giriş' is 1."
                },
                "block_id": {
                    "type": "string",
                    "description": "Block letter (A, B, C, D...)."
                },
                "status": {
                    "type": "string",
                    "description": "Status of the unit. usually 'available' unless user asks for sold units.",
                    "enum": ["available", "sold", "reserved"]
                },
                "direction": {
                    "type": "string",
                    "description": "Compass direction. Mappings: 'Kuzey'->'North', 'Güney'->'South', 'Doğu'->'East', 'Batı'->'West'.",
                    "enum": ["North", "South", "East", "West", "North-East", "North-West", "South-East", "South-West"]
                },
                "sun_exposure": {
                    "type": "string",
                    "description": "Sunlight preference. 'high' (Güneş alan/Aydınlık/Sunny), 'none' (Karanlık/Dark).",
                    "enum": ["high", "medium", "low", "none"]
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum price in TRY."
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum price in TRY."
                },
                "sort_by": {
                    "type": "string",
                    "description": "Sort order for results. Use 'price_asc' for 'cheapest/en ucuz', 'price_desc' for 'most expensive/en pahalı'.",
                    "enum": ["price_asc", "price_desc"]
                }
            }
        }
    },
    {
        "name": "check_price",
        "description": "Call this tool ONLY if the user explicitly asks for price of a KNOWN unit or type. \n\nEXAMPLES:\n- User: 'Bu dairenin fiyatı ne?' (needs context) -> Args: {}\n- User: 'INV-001 kaça?' -> Args: {'inventory_id': 'INV-0001'}\n- User: '3+1 fiyatları ne?' -> PREFER search_inventory return price, strictly use check_price if user wants general price list.",
        "parameters": {
             "type": "object",
             "properties": {
                 "inventory_id": {
                     "type": "string", 
                     "description": "Specific inventory ID if known."
                 }
             }
        }
    },
    {
        "name": "trigger_handoff",
        "description": "Call this tool IMMEDIATELY for actionable high-intent actions.\n\nEXAMPLES:\n- User: 'Kapora bırakmak istiyorum' -> Reason: 'High purchase intent'\n- User: 'Ziyaret edebilir miyim?' -> Reason: 'Visit request', share_location: true\n- User: 'İndirim yapar mısınız?' -> Reason: 'Negotiation/Discount request'\n- User: 'Beni arayın' -> Reason: 'Call request', share_contact_details: true\n- User: 'Ofise gelip görüşmek istiyorum' -> Reason: 'Office visit request', share_contact_details: true, share_location: true\n\nDO NOT use this tool for early exploration such as 'Ev almak istiyorum', 'Daire bakıyorum', or 'Ev arıyorum'. In those cases continue the conversation and ask a short qualification question.\nOnly set share_contact_details/share_location when the user is asking to receive those details now, not merely checking if the line is responsive.",
        "parameters": {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "The clear reason for handoff."
                },
                "missing_info": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of missing customer info if known (e.g. ['phone', 'name'])."
                },
                "share_contact_details": {
                    "type": "boolean",
                    "description": "Set true only when the user is ready to receive the consultant's phone/WhatsApp details in this turn."
                },
                "share_location": {
                    "type": "boolean",
                    "description": "Set true only when the user explicitly wants address, map, location, office visit, or directions now."
                }
            }
        }
    },
    {
        "name": "answer_general",
        "description": "Call this tool for greetings, general project info, qualification questions, reassurance about contact availability, or when the user refers to the currently discussed listing without needing a database search.\n\nEXAMPLES:\n- User: 'Merhaba' -> Category: 'greeting'\n- User: 'Proje nerede?' -> Category: 'location'\n- User: 'Havuz var mı?' -> Category: 'social_facilities'\n- User: 'Evleriniz hakkında bilgi almak istiyorum' -> Category: 'project_overview'\n- User: 'Ev almak istiyorum' -> Category: 'qualification'\n- User: 'Ararsam da cevap veriyor mu?' -> Category: 'contact_reassurance'\n- User: 'Bu evi anlat bana' while a listing is already being discussed -> Category: 'listing_overview'\n(If user asks 'Ev var mı?', use search_inventory instead!)",
        "parameters": {
            "type": "object",
            "properties": {
                 "category": {
                    "type": "string",
                     "description": "Category: 'greeting', 'location', 'social_facilities', 'project_overview', 'qualification', 'contact_reassurance', 'listing_overview', or 'other'."
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
    return f"""You are a smart orchestrator for a Real Estate AI Agent.
Your job is to analyze the User Input and select the correct TOOL to execute.

AVAILABLE TOOLS:
{json.dumps(TOOLS_SCHEMA, indent=2)}

INSTRUCTIONS:
1. Output MUST be valid JSON only. No markdown, no explanations.
2. Format: {{ "tool": "tool_name", "args": {{ "param": "value" }}, "card": {{ ... }} }}
   The "card" field maintains the customer's memory profile. Rules for "card":
   - Look at "MUSTERI KARTI" in the user message (the current card) and update it from the new message.
   - If the customer CHANGED a preference (e.g. earlier 3+1, now 4+1), REPLACE the old value — never keep both.
   - Keep unchanged fields as they are; add new info; do not drop the phone if present.
   - Only record what the customer explicitly said; use null when unknown. Never invent.
   - Card fields (all optional): name, flat_type (e.g. "4+1"), budget_max_try (number), budget_min_try (number), block, floor (number), direction (e.g. "guneybati"), sun (e.g. "gunes alan"), urgency ("high"/null), intent (one short sentence), changed (list of what changed in THIS message; [] if nothing).
   - If nothing about the profile changed, still echo the current card unchanged (with changed: []).
3. If user asks multiple things, pick the most critical one (Availability > General).
4. For "2+1 var mı" -> tool: "search_inventory", args: {{ "flat_type_id": "FT-2P1", "status": "available" }}
5. For "Fiyatı ne" -> tool: "check_price" (if referring to context) or "search_inventory" (if generic "daire fiyatları").
6. For "Ziyaret etmek istiyorum" -> tool: "trigger_handoff".
7. For "Merhaba" -> tool: "answer_general".
8. For generic buying intent like "ev almak istiyorum" or "daire arıyorum" -> DO NOT trigger handoff. Use "answer_general" and let the assistant ask a short qualifying question.
9. If the conversation already has a currently discussed listing and the user says things like "bu evi anlat", "bunu istiyorum", "buna bakalım", or other referential follow-ups, use the conversation context to decide. Prefer "answer_general" with category "listing_overview" for detail requests, and "trigger_handoff" for clear selection or move-forward intent.
10. Use conversation context, not only literal keywords. If the user refers to a previously discussed listing with pronouns like "bu", "bunu", "o daire", or "that one", resolve it from context.
11. For contact reassurance questions such as "Ararsam cevap veriyor mu?" or "Dönüş oluyor mu?" without asking to receive the details right now, use "answer_general" with category "contact_reassurance" instead of "trigger_handoff".
12. For handoff decisions, set "share_contact_details" or "share_location" only if the user wants those details to be sent in this turn. Do not send location or contact info just because handoff is required.
13. INFO SUFFICIENCY: The user message includes "ELIMDEKI BILGILER" (BILINEN = already known, EKSIK = missing). For "trigger_handoff", "missing_info" may ONLY contain items from EKSIK — never list something already in BILINEN (e.g. never "telefon" for a WhatsApp customer whose phone is known). If BILINEN covers everything the action needs, leave missing_info empty.

IMPORTANT: Map Turkish terms to Schema IDs:
- "2+1" -> "FT-2P1"
- "3+1" -> "FT-3P1"
- "Dubleks" -> "FT-DUP"
- "Güney" -> "South", "Kuzey" -> "North"
- "Güneş alan" -> "sun_exposure": "high"
"""

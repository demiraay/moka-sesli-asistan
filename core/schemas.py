from typing import List, Dict, Optional, TypedDict, Any
import json

class HandoffContext(TypedDict):
    required: bool
    reason: str
    missing_info: List[str]
    share_contact_details: bool
    share_location: bool

class AgentResponseContext(TypedDict):
    message_facts: List[str]
    units: List[Dict[str, Any]]
    alternatives: List[Dict[str, Any]]
    price_info: Optional[Dict[str, Any]]
    next_questions: List[str]
    handoff: HandoffContext

class ResponseBuilder:
    def __init__(self):
        self.context: AgentResponseContext = {
            "message_facts": [],
            "units": [],
            "alternatives": [],
            "price_info": None,
            "next_questions": [],
            "handoff": {
                "required": False,
                "reason": "",
                "missing_info": [],
                "share_contact_details": False,
                "share_location": False,
            }
        }

    def add_fact(self, fact: str):
        self.context["message_facts"].append(fact)

    def set_units(self, units: List[Dict[str, Any]]):
        self.context["units"] = units

    def set_alternatives(self, alternatives: List[Dict[str, Any]]):
        self.context["alternatives"] = alternatives

    def set_price(self, price_info: Dict[str, Any]):
        self.context["price_info"] = price_info

    def add_question(self, question: str):
        self.context["next_questions"].append(question)

    def trigger_handoff(
        self,
        reason: str,
        missing_info: List[str] = None,
        share_contact_details: bool = False,
        share_location: bool = False,
    ):
        if missing_info is None:
            missing_info = []
        self.context["handoff"] = {
            "required": True,
            "reason": reason,
            "missing_info": missing_info,
            "share_contact_details": share_contact_details,
            "share_location": share_location,
        }

    def build(self) -> AgentResponseContext:
        return self.context

    def to_json(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, indent=2)

from typing import List, Dict, Optional, TypedDict, Any
import json

class HandoffContext(TypedDict):
    required: bool
    reason: str
    missing_info: List[str]
    share_contact_details: bool

class AgentResponseContext(TypedDict):
    message_facts: List[str]
    settlement: Optional[Dict[str, Any]]
    settlements: List[Dict[str, Any]]
    transactions: List[Dict[str, Any]]
    device: Optional[Dict[str, Any]]
    kb_steps: List[str]
    plan_info: Optional[Dict[str, Any]]
    payment_link: Optional[Dict[str, Any]]
    offer: Optional[Dict[str, Any]]
    next_questions: List[str]
    handoff: HandoffContext

class ResponseBuilder:
    def __init__(self):
        self.context: AgentResponseContext = {
            "message_facts": [],
            "settlement": None,
            "settlements": [],
            "transactions": [],
            "device": None,
            "kb_steps": [],
            "plan_info": None,
            "payment_link": None,
            "offer": None,
            "next_questions": [],
            "handoff": {
                "required": False,
                "reason": "",
                "missing_info": [],
                "share_contact_details": False,
            }
        }

    def add_fact(self, fact: str):
        self.context["message_facts"].append(fact)

    def set_settlement(self, settlement: Dict[str, Any]):
        self.context["settlement"] = settlement

    def set_settlements(self, settlements: List[Dict[str, Any]]):
        self.context["settlements"] = settlements

    def set_transactions(self, transactions: List[Dict[str, Any]]):
        self.context["transactions"] = transactions

    def set_device(self, device: Dict[str, Any]):
        self.context["device"] = device

    def set_kb_steps(self, steps: List[str]):
        self.context["kb_steps"] = steps

    def set_plan_info(self, plan_info: Dict[str, Any]):
        self.context["plan_info"] = plan_info

    def set_payment_link(self, link: Dict[str, Any]):
        self.context["payment_link"] = link

    def set_offer(self, offer: Dict[str, Any]):
        self.context["offer"] = offer

    def add_question(self, question: str):
        self.context["next_questions"].append(question)

    def trigger_handoff(
        self,
        reason: str,
        missing_info: List[str] = None,
        share_contact_details: bool = False,
    ):
        if missing_info is None:
            missing_info = []
        self.context["handoff"] = {
            "required": True,
            "reason": reason,
            "missing_info": missing_info,
            "share_contact_details": share_contact_details,
        }

    def build(self) -> AgentResponseContext:
        return self.context

    def to_json(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, indent=2)

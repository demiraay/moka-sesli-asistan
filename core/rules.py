from typing import Dict, Any
from core.config import Config

class RuleEngine:
    def __init__(self):
        self.config = Config()

    def get_policies(self) -> Dict[str, Any]:
        """Returns a snapshot of active support policies based on rules.json."""
        support = self.config.get_support_rules()
        security = self.config.get_security_rules()
        upsell = self.config.get_upsell_rules()

        return {
            "never_invent_amounts": support.get('never_invent_amounts', True),
            "ground_amounts_in_tool_data": support.get('always_ground_amounts_in_tool_data', True),
            "no_fee_negotiation": support.get('no_fee_negotiation_by_ai', True),
            "resolve_before_offer": support.get('resolve_before_offer', True),
            "never_request_full_card": security.get('never_request_full_card_number', True),
            "max_offers_per_call": upsell.get('max_offers_per_call', 1),
        }

from typing import Dict
from core.config import Config

class RuleEngine:
    def __init__(self):
        self.config = Config()

    def get_policies(self) -> Dict[str, bool]:
        """Returns a snapshot of active policies based on rules.json."""
        pricing = self.config.get_pricing_rules()
        stock = self.config.rules.get('stock_rules', {})

        return {
            "allow_negotiation": pricing.get('negotiation_allowed', False),
            "custom_discount": pricing.get('custom_discount_allowed', False),
            "require_stock_check": stock.get('must_check_inventory', True),
            "no_guessing": stock.get('no_guessing', True),
            "price_is_final": pricing.get('price_is_list_price_only', True)
        }

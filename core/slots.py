import re
from typing import Dict, Any
from core.config import Config

class SlotMapper:
    """Regex pre-extraction of support-domain slots from merchant utterances.

    Feeds the router as a semantic backup: amounts ("1.250 TL"), days
    ("dün", "salı günü"), terminal ids ("TRM-4451") and card last-4 digits
    ("4832 ile biten kart").
    """

    _AMOUNT = re.compile(
        r'(\d{1,3}(?:\.\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:tl|lira|₺)', re.IGNORECASE
    )
    _TERMINAL = re.compile(r'\btrm[- ]?(\d{3,5})\b', re.IGNORECASE)
    _LAST4 = re.compile(
        r'(?:(\d{4})\s*ile\s*biten|son(?:u|\s+d[oö]rt\s+hane(?:si)?)?\s*(\d{4}))',
        re.IGNORECASE,
    )
    _DAYS = {
        'bugün': 'bugün', 'bugun': 'bugün',
        'dün': 'dün', 'dun': 'dün',
        'evvelsi gün': 'D-2', 'önceki gün': 'D-2',
    }
    _WEEKDAYS = {
        'pazartesi': 0, 'salı': 1, 'sali': 1, 'çarşamba': 2, 'carsamba': 2,
        'perşembe': 3, 'persembe': 3, 'cuma': 4, 'cumartesi': 5, 'pazar': 6,
    }

    def __init__(self):
        self.config = Config()

    def extract(self, text: str) -> Dict[str, Any]:
        """Extracts support slots from text."""
        text_lower = (text or "").lower()
        slots: Dict[str, Any] = {}

        # 1. Amount: "1.250 TL", "1250 lira", "500,50 TL"
        amount_match = self._AMOUNT.search(text_lower)
        if amount_match:
            whole = amount_match.group(1).replace('.', '')
            frac = amount_match.group(2)
            try:
                slots['amount_try'] = float(f"{whole}.{frac}") if frac else float(whole)
            except ValueError:
                pass

        # 2. Day reference: bugün/dün/evvelsi gün, or a weekday name
        for phrase, value in self._DAYS.items():
            if phrase in text_lower:
                slots['date'] = value
                break
        if 'date' not in slots:
            for name, _weekday in self._WEEKDAYS.items():
                if re.search(rf'\b{name}(\s+günü)?\b', text_lower):
                    slots['date'] = name
                    break

        # 3. Terminal id: "TRM-4451", "trm 4451"
        terminal_match = self._TERMINAL.search(text_lower)
        if terminal_match:
            slots['terminal_id'] = f"TRM-{terminal_match.group(1)}"

        # 4. Card last4: "4832 ile biten", "sonu 4832"
        last4_match = self._LAST4.search(text_lower)
        if last4_match:
            slots['card_last4'] = last4_match.group(1) or last4_match.group(2)

        return slots

if __name__ == "__main__":
    mapper = SlotMapper()
    tests = [
        "Dün 1.250 TL çektim ama hesapta göremiyorum",
        "TRM-4451 numaralı cihazım açılmıyor",
        "4832 ile biten karttan çekilen para nerede?",
        "Salı günü 500,50 TL'lik işlem iade oldu mu?",
    ]
    for t in tests:
        print(f"Text: {t}\nSlots: {mapper.extract(t)}\n")

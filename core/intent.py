import re
from typing import List

class IntentParser:
    """Regex intent hints for the support domain.

    These do not decide routing (the router LLM does); they feed the prompt
    context and analytics. 'anger' additionally grounds the handoff decision.
    """

    def __init__(self):
        self.patterns = {
            'settlement': [
                r'hakedi[şs]', r'param.*yat', r'ne zaman yat', r'yatmad[ıi]',
                r'yatan para', r'öde(me|nmedi).*gün', r'val[öo]r', r'iban',
            ],
            'transaction': [
                r'i[şs]lem', r'çektim', r'görem[iü]yorum', r'iade', r'iptal',
                r'geçti mi', r'sat[ıi][şs] yapt[ıi]m',
            ],
            'pos_issue': [
                r'cihaz', r'\bpos\b', r'aç[ıi]lm[ıi]yor', r'bağlanm[ıi]yor',
                r'bozuk', r'yazm[ıi]yor', r'ar[ıi]za', r'çal[ıi][şs]m[ıi]yor',
                r'sinyal', r'3d', r'ka[ğg][ıi]t', r'\bfi[şs]\b',
            ],
            'fees': [
                r'komisyon', r'kesinti', r'\boran\b', r'ücret', r'kesil',
            ],
            'statement': [
                r'ekstre', r'döküm', r'hesap özeti', r'rapor',
            ],
            'payment_link': [
                r'link', r'uzaktan (ödeme|tahsilat)',
            ],
            'anger': [
                r'rezalet', r'[şs]ikayet', r'yeter art[ıi]k', r'b[ıi]kt[ıi]m',
                r'kimse ilgilenmiyor', r'mahkeme', r'avukat', r'sabr[ıi]m',
                r'sizi arayaca[ğg][ıi]m dedi', r'kaç gündür',
            ],
            'human_request': [
                r'temsilci', r'yetkili', r'insanla', r'ger[çc]ek biri', r'operatör',
            ],
        }

    def parse(self, text: str) -> List[str]:
        """
        Parses the input text and returns a list of detected intents.
        """
        text = text.lower()
        detected_intents = []

        for intent, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    detected_intents.append(intent)
                    break

        return list(set(detected_intents))

if __name__ == "__main__":
    parser = IntentParser()
    test_sentences = [
        "Param ne zaman yatacak?",
        "Dün 1.250 TL çektim ama göremiyorum",
        "POS cihazım açılmıyor, müşteri bekliyor",
        "Bu komisyon neden bu kadar yüksek?",
        "Yeter artık, sizi şikayet edeceğim! Temsilci bağlayın.",
    ]

    for sent in test_sentences:
        print(f"Text: {sent}\nIntents: {parser.parse(sent)}\n")

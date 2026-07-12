import re
from typing import List, Dict

class IntentParser:
    def __init__(self):
        self.patterns = {
            'availability': [
                r'var mı', r'kaldı mı', r'stok', r'boş', 
                r'satılık', r'elinizde', r'mevcut', r'daire var',
                r'hangi.*daire', r'hangi.*ev'
            ],
            'price': [
                r'fiyat', r'kaç para', r'ne kadar', r'kaç tl', 
                r'pahalı mı', r'bütçe', r'ödeyeceğim', r'kaça'
            ],
            'location': [
                r'nerede', r'konum', r'adres', r'hangi ilçe', 
                r'uzak mı', r'yakın mı', r'ulaşım'
            ],
            'sunlight': [
                r'güneş', r'cephe', r'karanlık', r'aydınlık', 
                r'kuzey', r'güney', r'doğu', r'batı'
            ],
            'payment_plan': [
                r'ödeme', r'taksit', r'peşin', r'vade', 
                r'kredi', r'banka', r'senet'
            ],
            'visit': [
                r'ziyaret', r'görmek', r'randevu', r'gelmek istiyorum', 
                r'bakmak istiyorum', r'yerinde görmek', r'ofis'
            ],
            'callback': [
                r'arayın', r'ulaşın', r'numaram', r'telefon', 
                r'beni ara', r'dönüş yap'
            ]
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
        
        return list(set(detected_intents)) # Return unique intents

if __name__ == "__main__":
    parser = IntentParser()
    test_sentences = [
        "3+1 dairelerinizin fiyatı ne kadar?",
        "Elinizde güney cephe daire kaldı mı?",
        "Ofisinizi ziyaret etmek istiyorum, adres nerede?",
        "Taksit imkanınız var mı fiyatlar çok yüksek mi?",
        "Beni acil arayın"
    ]
    
    for sent in test_sentences:
        print(f"Text: {sent}\nIntents: {parser.parse(sent)}\n")

import sys
import os
import unittest

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.intent import IntentParser

class TestIntentParser(unittest.TestCase):
    def setUp(self):
        self.parser = IntentParser()

    def test_single_intent_availability(self):
        text = "Elinizde satılık 3+1 daire var mı?"
        intents = self.parser.parse(text)
        self.assertIn('availability', intents)

    def test_single_intent_price(self):
        text = "Dairelerin fiyatı ne kadar?"
        intents = self.parser.parse(text)
        self.assertIn('price', intents)

    def test_multi_intent(self):
        text = "Fiyatı ne kadar ve elinizde kaldı mı?"
        intents = self.parser.parse(text)
        self.assertIn('price', intents)
        self.assertIn('availability', intents)
    
    def test_visit_intent(self):
        text = "Projeyi yerinde görmek istiyorum."
        intents = self.parser.parse(text)
        self.assertIn('visit', intents)

    def test_complex_sentence(self):
        # "Is it sunny and what are the payment options?"
        text = "Daireler güneş alıyor mu kredi taksit var mı?"
        intents = self.parser.parse(text)
        self.assertIn('sunlight', intents)
        self.assertIn('payment_plan', intents)

    def test_no_intent(self):
        text = "Merhaba nasılsınız?"
        intents = self.parser.parse(text)
        self.assertEqual(intents, [])

if __name__ == '__main__':
    unittest.main()

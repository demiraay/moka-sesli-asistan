import sys
import os
import unittest

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.slots import SlotMapper

class TestSlotMapper(unittest.TestCase):
    def setUp(self):
        self.mapper = SlotMapper()

    def test_flat_type(self):
        self.assertEqual(self.mapper.extract("2+1 daire")['flat_type_id'], 'FT-2P1')
        self.assertEqual(self.mapper.extract("3 artı 1")['flat_type_id'], 'FT-3P1')
        self.assertIn(self.mapper.extract("dubleks")['flat_type_id'], ['FT-DUP', 'FT-5P1']) # Based on data, likely FT-DUP

    def test_floor(self):
        self.assertEqual(self.mapper.extract("5. kat")['floor'], 5)
        self.assertEqual(self.mapper.extract("10 kat")['floor'], 10)
        self.assertEqual(self.mapper.extract("zemin kat")['floor'], 1)

    def test_block(self):
        self.assertEqual(self.mapper.extract("A blok")['block_id'], 'A')
        self.assertEqual(self.mapper.extract("B blok")['block_id'], 'B')

    def test_direction_sunlight(self):
        res = self.mapper.extract("Güney cephe güneş alan daire")
        self.assertEqual(res['direction'], 'South')
        self.assertEqual(res['sun_exposure'], 'high')
        
        res_composite = self.mapper.extract("Kuzey doğu cephesi")
        self.assertEqual(res_composite['direction'], 'North-East')

    def test_combined(self):
        text = "A blok 3. kat 2+1 bakıyorum"
        res = self.mapper.extract(text)
        self.assertEqual(res['block_id'], 'A')
        self.assertEqual(res['floor'], 3)
        self.assertEqual(res['flat_type_id'], 'FT-2P1')

if __name__ == '__main__':
    unittest.main()

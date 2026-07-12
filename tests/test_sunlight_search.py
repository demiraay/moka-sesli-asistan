import sys
import os
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.inventory import InventoryManager

class TestSunlightSearch(unittest.TestCase):
    def test_sun_exposure_filtering(self):
        manager = InventoryManager()
        
        # Criteria: Available AND High Sun Exposure
        criteria = {
            'status': 'available',
            'sun_exposure': 'high'
        }
        
        results = manager.search(criteria)
        exact = results['exact_matches']
        
        print(f"Found {len(exact)} units with high sun exposure.")
        
        for unit in exact:
            # Verify inventory status
            self.assertEqual(unit['status'], 'available')
            # Verify joined data
            self.assertEqual(unit['sunlight']['sun_exposure'], 'high')
            
    def test_dark_unit_exclusion(self):
        manager = InventoryManager()
        # Find a unit we know is 'none' (dark) if any, or verify we don't return them for 'high' query
        
        criteria = {'sun_exposure': 'high'}
        results = manager.search(criteria)
        exact = results['exact_matches']
        
        for unit in exact:
            # Look up raw sunlight data to ensure no mismatch
            sun_data = next(s for s in manager.config.sunlight if s['inventory_id'] == unit['inventory_id'])
            self.assertNotEqual(sun_data['sun_exposure'], 'none')

if __name__ == '__main__':
    unittest.main()

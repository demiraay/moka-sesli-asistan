import sys
import os
import unittest
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.schemas import ResponseBuilder

class TestResponseSchema(unittest.TestCase):
    def test_build_structure(self):
        builder = ResponseBuilder()
        builder.add_fact("Flat A-1 is available.")
        builder.set_units([{"id": "A-1", "status": "available"}])
        builder.trigger_handoff("User requested visit")
        
        result = builder.build()
        
        self.assertEqual(result['message_facts'][0], "Flat A-1 is available.")
        self.assertEqual(result['units'][0]['id'], "A-1")
        self.assertTrue(result['handoff']['required'])
        self.assertEqual(result['handoff']['reason'], "User requested visit")

    def test_serialization(self):
        builder = ResponseBuilder()
        builder.add_fact("Price is 10M")
        json_str = builder.to_json()
        
        # Verify valid JSON
        data = json.loads(json_str)
        self.assertIn("message_facts", data)
        self.assertEqual(data["message_facts"][0], "Price is 10M")

if __name__ == '__main__':
    unittest.main()

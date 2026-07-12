import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator

class TestJsonFix(unittest.TestCase):
    def test_single_quote_parsing(self):
        orchestrator = AgentOrchestrator()
        
        # Simulate LLM returning Python dict string (single quotes)
        # This causes json.loads to fail, but ast.literal_eval should succeed
        bad_json = "{'tool': 'search_inventory', 'args': {'status': 'available'}}"
        
        # Mock the Router call
        orchestrator.llm_client.generate = MagicMock(return_value=bad_json)
        
        # We expect process_turn to fail at the 2nd LLM call (Response Generation) 
        # because we only mocked the first one. 
        # But if it reaches the 2nd call, it means Router parsing succeeded!
        # logic: process_turn -> router(mocked) -> parse(success) -> execution -> response(fail)
        
        try:
             orchestrator.process_turn("dummy input")
        except Exception as e:
            # We assume it fails later in the pipeline or if we didn't mock the 2nd call properly.
            # actually let's mock side_effect to be safe.
            pass
            
        orchestrator.llm_client.generate.side_effect = [
            bad_json,                 # Router response
            "Final Agent Response"    # Final response
        ]
        
        result = orchestrator.process_turn("dummy input")
        
        print(f"Router Decision: {result['router_decision']}")
        
        self.assertEqual(result['router_decision']['tool'], 'search_inventory')
        self.assertEqual(result['router_decision']['args']['status'], 'available')

if __name__ == "__main__":
    unittest.main()

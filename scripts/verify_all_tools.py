import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator

def run_verification():
    print("--- Verifying All Tools ---\n")
    
    orch = AgentOrchestrator()
    
    test_cases = [
        {
            "name": "1. SEARCH INVENTORY (Available 3+1)",
            "input": "3+1 boş daire var mı?",
            "router_json": "{'tool': 'search_inventory', 'args': {'flat_type_id': 'FT-3P1', 'status': 'available'}}",
            "expected_tool": "search_inventory"
        },
        {
            "name": "2. SEARCH INVENTORY (Sunlight Filter)",
            "input": "Güneş alan daireler hangileri?",
            "router_json": "{'tool': 'search_inventory', 'args': {'sun_exposure': 'high', 'status': 'available'}}",
            "expected_tool": "search_inventory"
        },
        {
            "name": "3. CHECK PRICE (Specific Unit)",
            "input": "INV-0001 fiyatı ne kadar?",
            "router_json": "{'tool': 'check_price', 'args': {'inventory_id': 'INV-0001'}}",
            "expected_tool": "check_price"
        },
        {
            "name": "4. CHECK PRICE (General)",
            "input": "Fiyatlar ne alemde?",
            "router_json": "{'tool': 'check_price', 'args': {}}",
            "expected_tool": "check_price"
        },
        {
            "name": "5. HANDOFF (Visit Request)",
            "input": "Projeyi yerinde görmek istiyorum.",
            "router_json": "{'tool': 'trigger_handoff', 'args': {'reason': 'User requested visit'}}",
            "expected_tool": "trigger_handoff"
        },
        {
            "name": "6. GENERAL ANSWER (Greeting)",
            "input": "Selamlar kolay gelsin.",
            "router_json": "{'tool': 'answer_general', 'args': {'category': 'greeting'}}",
            "expected_tool": "answer_general"
        }
    ]
    
    for case in test_cases:
        print(f"Testing: {case['name']}")
        
        # Mock LLM: 1st call = Router, 2nd call = Final Response
        orch.llm_client.generate = MagicMock(side_effect=[
            case['router_json'],
            "Final generated response."
        ])
        
        try:
            result = orch.process_turn(case['input'])
            decision = result['router_decision']
            context = result['context']
            
            print(f"Router Decision: {decision}")
            
            # Additional Checks
            if case['expected_tool'] == 'search_inventory':
                print(f"Facts Generated: {len(context['message_facts'])}")
                print(f"Units Found: {len(context['units'])}")
                
            elif case['expected_tool'] == 'trigger_handoff':
                print(f"Handoff Required: {context['handoff']['required']}")
                print(f"Handoff Reason: {context['handoff']['reason']}")
                
            elif case['expected_tool'] == 'check_price':
                print(f"Price Info: {context['price_info']}")
                
            print("✅ Success")
            
        except Exception as e:
            print(f"❌ Failed: {e}")
            
        print("-" * 40)

if __name__ == "__main__":
    run_verification()

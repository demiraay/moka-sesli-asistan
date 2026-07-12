import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.orchestrator import AgentOrchestrator

def packet_check():
    print("--- 3+1 Hallucination Check ---")
    orch = AgentOrchestrator()
    start = time.time()
    
    # Force the query that was failing
    q = "3+1 daire var mı?"
    print(f"Q: {q}")
    
    # Run
    res = orch.process_turn(q)
    
    # Check Context Limiting
    units = res['context'].get('units', [])
    print(f"Units in Context: {len(units)}")
    if len(units) > 5:
        print("FAIL: Context overflow")
    else:
        print("PASS: Context limited")
        
    # Check Response
    print("Response snippet:", res['agent_response'][:100])
    if "data" in res['agent_response'] and "yok" in res['agent_response']:
        print("FAIL: Still hallucinating")
    else:
        print("PASS: Seemingly correct")
        
    print(f"Time: {time.time() - start:.2f}s")

if __name__ == "__main__":
    packet_check()

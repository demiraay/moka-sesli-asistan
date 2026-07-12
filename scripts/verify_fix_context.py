import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import AgentOrchestrator

def run_verify():
    print("--- Verifying Context Limit & Response ---\n")
    orch = AgentOrchestrator()
    
    query = "3+1 daire var mı?"
    print(f"User: {query}")
    print("Thinking...")
    
    start = time.time()
    result = orch.process_turn(query)
    duration = time.time() - start
    
    # Check 1: Tool Selection
    print(f"\n[1] Router: {result['router_decision']}")
    
    # Check 2: Context Limiting
    context = result['context']
    units = context.get('units', [])
    price_info = context.get('price_info')
    
    print(f"\n[2] Context Units Sent to LLM: {len(units)}")
    if len(units) <= 5:
        print("✅ PASS: Context Limited to <= 5 units")
    else:
        print(f"❌ FAIL: Context Overflow (Sent {len(units)})")
        
    print(f"Price Summary: {price_info}")
    
    # Check 3: Final Response
    response = result['agent_response']
    print(f"\n[3] Agent Response:\n{response}")
    
    # Heuristic check for hallucination
    if "data" in response.lower() and "unavailable" in response.lower():
         print("\n⚠️ WARNING: Agent might be hallucinating availability issues.")
    elif len(units) > 0 and "var" in response.lower() or "mevcut" in response.lower() or "bulunuyor" in response.lower():
         print("\n✅ PASS: Agent acknowledges units.")
    
    print(f"\nTime: {duration:.2f}s")

if __name__ == "__main__":
    run_verify()

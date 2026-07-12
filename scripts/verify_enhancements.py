import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.inventory import InventoryManager
from core.prompts import SystemPromptBuilder

def run_verification():
    print("--- Verifying Enhancements ---\n")
    
    # 1. Verify Prompt Construction
    print("1. Checking System Prompt Injection...")
    builder = SystemPromptBuilder()
    prompt = builder.build_system_prompt()
    
    if "Indoor Pool" in prompt and "Gym" in prompt:
        print("✅ Social Facilities injected.")
    else:
        print("❌ Social Facilities missing.")
        
    if "Lansman İndirimi" in prompt:
        print("✅ Campaigns injected.")
    else:
        print("❌ Campaigns missing.")

    print("\n2. Checking Price Filter Logic...")
    manager = InventoryManager()
    
    # Test Max Price: 9.000.000 TRY
    # We know simple units are around 8M, duplexes are 20M+
    criteria = {
        'status': 'available',
        'max_price': 9000000
    }
    
    results = manager.search(criteria)
    exact = results['exact_matches']
    
    print(f"Found {len(exact)} units under 9M TRY.")
    
    # Verify no unit is above 9M
    fail_count = 0
    for u in exact:
        if u['price']['list_price_try'] > 9000000:
            fail_count += 1
            print(f"FAILED: Found unit {u['inventory_id']} with price {u['price']['list_price_try']}")
            
    if fail_count == 0 and len(exact) > 0:
        print("✅ Price Filter logic works.")
    elif len(exact) == 0:
        print("⚠️ Warning: No units found under 9M (might be correct but check data).")
    else:
        print("❌ Price Filter Failed.")

if __name__ == "__main__":
    run_verification()

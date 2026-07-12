import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.inventory import InventoryManager
from core.tools import TOOLS_SCHEMA

def run_verification():
    print("--- Verifying Tool Robustness & Sorting ---\n")
    
    # 1. Check Schema Descriptions
    print("1. Checking Schema Definitions...")
    search_tool = next(t for t in TOOLS_SCHEMA if t['name'] == 'search_inventory')
    if "EXAMPLES" in search_tool['description']:
        print("✅ Examples embedded in description.")
    else:
        print("❌ Examples missing.")
        
    if "sort_by" in search_tool['parameters']['properties']:
        print("✅ sort_by parameter defined.")
    else:
        print("❌ sort_by parameter missing.")

    # 2. Check Sorting Logic
    print("\n2. Checking Sorting Logic...")
    manager = InventoryManager()
    
    # Test 1: Cheapest First
    criteria = {'status': 'available', 'sort_by': 'price_asc'}
    results = manager.search(criteria)
    matches = results['exact_matches']
    
    print(f"Found {len(matches)} units.")
    if len(matches) > 1:
        first_price = matches[0]['price']['list_price_try']
        last_price = matches[-1]['price']['list_price_try']
        print(f"First Price: {first_price}, Last Price: {last_price}")
        
        if first_price <= last_price:
            print("✅ Sort ASC success.")
        else:
            print("❌ Sort ASC failed.")
            
    # Test 2: Most Expensive First
    criteria_desc = {'status': 'available', 'sort_by': 'price_desc'}
    results_desc = manager.search(criteria_desc)
    matches_desc = results_desc['exact_matches']
    
    if len(matches_desc) > 1:
        first_price = matches_desc[0]['price']['list_price_try']
        last_price = matches_desc[-1]['price']['list_price_try']
        print(f"First Price: {first_price}, Last Price: {last_price}")
        
        if first_price >= last_price:
            print("✅ Sort DESC success.")
        else:
            print("❌ Sort DESC failed.")

if __name__ == "__main__":
    run_verification()

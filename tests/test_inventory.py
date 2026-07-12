import sys
import os
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.inventory import InventoryManager

def test_search():
    print("--- Testing Inventory Search ---")
    manager = InventoryManager()
    
    # Test 1: Exact Match
    print("\n1. Testing Exact Match (Available Unit)...")
    criteria = {"block_id": "A", "floor": "1", "flat_type_id": "FT-2P1"} # Adjust based on real data if needed
    result = manager.search(criteria)
    
    exact = result['exact_matches']
    print(f"Found {len(exact)} exact matches.")
    if exact:
        print(f"First match: {exact[0]['inventory_id']} - Status: {exact[0]['status']}")
        print(f"Price: {exact[0].get('price', {}).get('list_price_try')}")
        print(f"Sunlight: {exact[0].get('sunlight', {}).get('sun_exposure')}")
    else:
        print("No exact matches found.")

    # Test 2: Alternatives
    print("\n2. Testing Alternatives (Force Sold/Reserved or non-existent)...")
    # Finding a sold unit's criteria or using a non-existent floor to trigger alternatives
    # Let's try to search for something that might be scarce
    criteria_scarce = {"block_id": "A", "floor": "20", "flat_type_id": "FT-4P1"} # Floor 20 likely doesn't exist
    result_alt = manager.search(criteria_scarce)
    
    print(f"Found {len(result_alt['exact_matches'])} exact matches.")
    print(f"Found {len(result_alt['alternatives'])} alternatives.")
    for alt in result_alt['alternatives']:
        print(f"Alternative: {alt['inventory_id']} - Block: {alt['block_id']} Floor: {alt['floor']} Type: {alt['flat_type_id']}")

    # Test 3: Status Check
    print("\n3. Testing Status Check...")
    # Using a known ID from manual inspection or previous result
    if exact:
        inv_id = exact[0]['inventory_id']
        status = manager.check_status(inv_id)
        print(f"Status of {inv_id}: {status}")

if __name__ == "__main__":
    test_search()

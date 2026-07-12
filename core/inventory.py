from typing import List, Dict, Optional, Any
from core.config import Config

class InventoryManager:
    def __init__(self):
        self.config = Config()

    def search(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for inventory search.
        Returns exact matches and alternatives.
        """
        # 1. Exact Matches (status='available')
        exact_matches = self._get_exact_matches(criteria)
        
        # Enrich matches to have price data available for sorting
        enriched_matches = [self.enrich_details(m) for m in exact_matches]
        
        # Sort if requested
        sort_by = criteria.get('sort_by')
        if sort_by == 'price_asc':
            enriched_matches.sort(key=lambda x: x.get('price', {}).get('list_price_try', float('inf')))
        elif sort_by == 'price_desc':
            enriched_matches.sort(key=lambda x: x.get('price', {}).get('list_price_try', 0), reverse=True)
        
        # 2. Alternatives (if exact matches are few or none)
        alternatives = []
        if len(enriched_matches) < 3:
            alternatives = self._get_alternatives(criteria, exclude_ids=[m['inventory_id'] for m in exact_matches])
            # Enrich alternatives
            alternatives = [self.enrich_details(m) for m in alternatives]
            
        return {
            "exact_matches": enriched_matches,
            "alternatives": alternatives,
            "criteria_used": criteria
        }

    def _get_exact_matches(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        matches = []
        for item in self.config.inventory:
            if item.get('status') != 'available':
                continue
            
            if self._matches_criteria(item, criteria):
                matches.append(item)
        return matches

    def _matches_criteria(self, item: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
        inv_id = item['inventory_id']
        
        for key, value in criteria.items():
            if value is None:
                continue
            
            # Skip sort_by as it is handled in search() after filtering
            if key == 'sort_by':
                continue
            
            # Handle standard fields present in inventory.json
            if key in item:
                if str(item[key]) != str(value):
                    return False
            
            # Handle 'sun_exposure' from sunlight.json
            elif key == 'sun_exposure':
                sun_info = next((s for s in self.config.sunlight if s['inventory_id'] == inv_id), None)
                if not sun_info or sun_info.get('sun_exposure') != value:
                    return False
            
            # Handle Price Filtering
            elif key == 'min_price' or key == 'max_price':
                price_info = next((p for p in self.config.prices if p['inventory_id'] == inv_id), None)
                if not price_info:
                    return False
                
                price = price_info.get('list_price_try', 0)
                
                if key == 'min_price' and price < float(value):
                    return False
                if key == 'max_price' and price > float(value):
                    return False

            else:
                # If key is completely unknown/unsupported, fail the match
                return False
                
        return True

    def _get_alternatives(self, criteria: Dict[str, Any], exclude_ids: List[str]) -> List[Dict[str, Any]]:
        alternatives = []
        
        # Strategy 1: Same Block, Different Floor
        if 'block_id' in criteria:
            alt_criteria = criteria.copy()
            if 'floor' in alt_criteria:
                del alt_criteria['floor'] # Remove floor constraint
            
            candidates = self._get_candidates(alt_criteria, exclude_ids)
            alternatives.extend(candidates[:3]) # Limit
            exclude_ids.extend([c['inventory_id'] for c in candidates])

        # Strategy 2: Same Flat Type, Different Block
        if len(alternatives) < 3 and 'flat_type_id' in criteria:
            alt_criteria = criteria.copy()
            if 'block_id' in alt_criteria:
                del alt_criteria['block_id'] # Remove block constraint
            if 'floor' in alt_criteria:
                del alt_criteria['floor']

            candidates = self._get_candidates(alt_criteria, exclude_ids)
            alternatives.extend(candidates[:3])
            exclude_ids.extend([c['inventory_id'] for c in candidates])
            
        return alternatives[:5] # Max 5 alternatives

    def _get_candidates(self, criteria: Dict[str, Any], exclude_ids: List[str]) -> List[Dict[str, Any]]:
        candidates = []
        for item in self.config.inventory:
            if item.get('status') != 'available':
                continue
            if item['inventory_id'] in exclude_ids:
                continue
            
            if self._matches_criteria(item, criteria):
                candidates.append(item)
        return candidates

    def enrich_details(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Enriches inventory item with price and metadata."""
        enriched = item.copy()
        inv_id = item['inventory_id']
        
        # Add Price
        price_info = next((p for p in self.config.prices if p['inventory_id'] == inv_id), None)
        if price_info:
            enriched['price'] = price_info
            
        # Add Sunlight Info
        sun_info = next((s for s in self.config.sunlight if s['inventory_id'] == inv_id), None)
        if sun_info:
            enriched['sunlight'] = sun_info

        # Add Flat Type Details
        flat_type = next((f for f in self.config.flats if f['flat_type_id'] == item['flat_type_id']), None)
        if flat_type:
            enriched['flat_details'] = flat_type
            
        return enriched

    def check_status(self, inventory_id: str) -> str:
        """Returns the status of a specific inventory item."""
        item = next((i for i in self.config.inventory if i['inventory_id'] == inventory_id), None)
        if item:
            return item.get('status', 'unknown')
        return 'not_found'

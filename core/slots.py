import re
from typing import Dict, Any, Optional
from core.config import Config

class SlotMapper:
    def __init__(self):
        self.config = Config()
        self.flat_type_map = self._build_flat_type_map()
        self.block_pattern = self._build_block_pattern()

    def _build_block_pattern(self) -> re.Pattern:
        """blocks.json'daki gercek blok harflerinden regex kurar; veri yoksa a-z kabul eder."""
        letters = sorted(
            {
                str(block.get('block_id', '')).strip().lower()
                for block in self.config.blocks
                if len(str(block.get('block_id', '')).strip()) == 1
            }
        )
        letter_class = ''.join(letters) if letters else 'a-z'
        return re.compile(rf'\b([{letter_class}])\s*blok')

    def _build_flat_type_map(self) -> Dict[str, str]:
        """Builds a mapping from standard labels (e.g., '2+1') to IDs."""
        mapping = {}
        for flat in self.config.flats:
            label = flat['label'].lower() # e.g., "2+1" or "5+1 duplex"
            flat_id = flat['flat_type_id']
            
            # Direct mapping
            mapping[label] = flat_id
            
            # Variations
            if 'duplex' in label or 'dubleks' in label:
                mapping['dubleks'] = flat_id
                mapping['duplex'] = flat_id
            
            # Extract basic "N+M" pattern
            match = re.search(r'(\d\+\d)', label)
            if match:
                mapping[match.group(1)] = flat_id
                
        return mapping

    def extract(self, text: str) -> Dict[str, Any]:
        """Extracts slots from text and maps them to internal values."""
        text = text.lower()
        slots = {}

        # 1. Flat Type Extraction
        # Matches: "2+1", "3 artı 1", "dubleks"
        flat_type_match = re.search(r'(\d)\s*(\+|artı)\s*(\d)', text)
        if flat_type_match:
            key = f"{flat_type_match.group(1)}+{flat_type_match.group(3)}"
            if key in self.flat_type_map:
                slots['flat_type_id'] = self.flat_type_map[key]
        elif 'dubleks' in text or 'duplex' in text:
             # Find the duplex ID
             if 'dubleks' in self.flat_type_map:
                 slots['flat_type_id'] = self.flat_type_map['dubleks']

        # 2. Floor Extraction
        # Matches: "3. kat", "5 kat", "zemin", "giriş"
        floor_match = re.search(r'(\d+)\.?\s*kat', text)
        if floor_match:
            slots['floor'] = int(floor_match.group(1))
        elif 'zemin' in text or 'giriş' in text:
            # Envanterde katlar 1'den basliyor; zemin/giris bu projede 1. kata denk geliyor.
            slots['floor'] = 1

        # 3. Block Extraction
        # Matches: "A blok", "B blok" — harf listesi blocks.json'dan gelir
        block_match = self.block_pattern.search(text)
        if block_match:
            slots['block_id'] = block_match.group(1).upper()

        # 4. Direction Extraction — once bilesik yonler ("kuzey doğu"), sonra tekli yonler
        if 'kuzey doğu' in text or 'kuzeydoğu' in text: slots['direction'] = 'North-East'
        elif 'güney batı' in text or 'güneybatı' in text: slots['direction'] = 'South-West'
        elif 'kuzey batı' in text or 'kuzeybatı' in text: slots['direction'] = 'North-West'
        elif 'güney doğu' in text or 'güneydoğu' in text: slots['direction'] = 'South-East'
        elif 'kuzey' in text: slots['direction'] = 'North'
        elif 'güney' in text: slots['direction'] = 'South'
        elif 'doğu' in text: slots['direction'] = 'East'
        elif 'batı' in text: slots['direction'] = 'West'

        # 5. Sunlight
        if 'güneş alan' in text or 'aydınlık' in text:
            slots['sun_exposure'] = 'high'
        elif 'karanlık' in text or 'güneş almayan' in text:
            slots['sun_exposure'] = 'none' # or low

        return slots

if __name__ == "__main__":
    mapper = SlotMapper()
    tests = [
        "A blok 3. kat 2+1 daire var mı?",
        "Güney cephe güneş alan bir yer istiyorum",
        "Zemin kat dubleks fiyatı nedir?",
        "5. katta 3 artı 1 bakıyorum"
    ]
    for t in tests:
        print(f"Text: {t}\nSlots: {mapper.extract(t)}\n")

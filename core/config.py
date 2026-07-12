import os
import json
from typing import Dict, Any, Optional

from dotenv import load_dotenv

class Config:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        load_dotenv()

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, 'data')
        
        # Data stores
        self.projects: list = []
        self.blocks: list = []
        self.flats: list = []
        self.inventory: list = []
        self.prices: list = []
        self.campaigns: list = []
        self.rules: dict = {}
        self.handoff_rules: dict = {}
        self.sunlight: list = []
        
        # LLM Settings
        # Mode: 0 = Ollama (Local), 1 = OpenAI (Cloud)
        self.llm_mode = int(os.getenv('LLM_MODE', '0'))
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.ollama_model = os.getenv('OLLAMA_MODEL', 'gpt-oss:120b-cloud')
        self.openai_model = os.getenv('OPENAI_MODEL', 'gpt-4o')

        # Voice I/O settings
        self.whisper_model = os.getenv('WHISPER_MODEL', 'large')
        self.whisper_language = os.getenv('WHISPER_LANGUAGE', 'tr')
        self.whisper_device = os.getenv('WHISPER_DEVICE', 'cpu')
        self.voice_output_dir = os.getenv(
            'VOICE_OUTPUT_DIR',
            os.path.join(self.base_dir, 'voice_output'),
        )

        self.elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY', '')
        self.elevenlabs_voice_id = os.getenv('ELEVENLABS_VOICE_ID', '')
        self.elevenlabs_model_id = os.getenv('ELEVENLABS_MODEL_ID', 'eleven_multilingual_v2')
        self.elevenlabs_output_format = os.getenv('ELEVENLABS_OUTPUT_FORMAT', 'mp3_44100_128')
        self.elevenlabs_base_url = os.getenv('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io/v1')

        self.load_data()
        self._initialized = True

    def get_llm_profile(self, profile: str = "default") -> Dict[str, Any]:
        return {
            "mode": self.llm_mode,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "openai_api_key": self.openai_api_key,
            "openai_model": self.openai_model,
        }

    def load_data(self):
        """Loads all JSON data from the data directory."""
        self.projects = self._load_json_file('projects.json', [])
        self.blocks = self._load_json_file('blocks.json', [])
        self.flats = self._load_json_file('flats.json', [])
        self.inventory = self._load_json_file('inventory.json', [])
        self.prices = self._load_json_file('prices.json', [])
        self.campaigns = self._load_json_file('campaigns.json', [])
        if not self.campaigns:
             self.campaigns = self._load_json_file('campaign.json', [])

        self.rules = self._load_json_file('rules.json', {})
        self.handoff_rules = self._load_json_file('handoff_rules.json', {})
        self.sunlight = self._load_json_file('sunlight.json', [])

    def _load_json_file(self, filename: str, default: Any) -> Any:
        file_path = os.path.join(self.data_dir, filename)
        if not os.path.exists(file_path):
            print(f"Warning: Data file {filename} not found.")
            return default
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {filename}: {e}")
            return default

    def get_project_name(self) -> str:
        if self.projects:
            return self.projects[0].get('name', 'Unknown Project')
        return 'Unknown Project'

    def get_pricing_rules(self) -> dict:
        return self.rules.get('pricing_rules', {})

    def get_handoff_conditions(self) -> list:
        return self.handoff_rules.get('handoff_conditions', [])

    def get_project_details(self) -> dict:
        """Returns the first project's details including description and facilities."""
        if self.projects:
            return self.projects[0]
        return {}

    def get_active_campaigns(self) -> list:
        """Returns a list of active campaigns."""
        return [c for c in self.campaigns if c.get('applicable') is not False]

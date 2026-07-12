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

        # Data stores (mock Moka backend)
        self.projects: list = []
        self.merchants: list = []
        self.transactions: list = []
        self.settlements: list = []
        self.pos_devices: list = []
        self.commission_plans: list = []
        self.support_kb: list = []
        self.rules: dict = {}
        self.handoff_rules: dict = {}

        # LLM Settings
        # Mode: 0 = Ollama (Local), 1 = OpenAI (Cloud), 2 = Groq (Cloud, free tier)
        self.llm_mode = int(os.getenv('LLM_MODE', '0'))
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.ollama_model = os.getenv('OLLAMA_MODEL', 'gpt-oss:120b-cloud')
        self.openai_model = os.getenv('OPENAI_MODEL', 'gpt-4o')
        self.groq_api_key = os.getenv('GROQ_API_KEY', '')
        self.groq_base_url = os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
        self.groq_model = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.groq_router_model = os.getenv('GROQ_ROUTER_MODEL', 'llama-3.1-8b-instant')

        # Voice I/O settings
        self.whisper_model = os.getenv('WHISPER_MODEL', 'base')
        self.whisper_language = os.getenv('WHISPER_LANGUAGE', 'tr')
        self.whisper_device = os.getenv('WHISPER_DEVICE', 'cpu')
        self.voice_output_dir = os.getenv(
            'VOICE_OUTPUT_DIR',
            os.path.join(self.base_dir, 'voice_output'),
        )

        self.elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY', '')
        self.elevenlabs_voice_id = os.getenv('ELEVENLABS_VOICE_ID', '')
        self.elevenlabs_model_id = os.getenv('ELEVENLABS_MODEL_ID', 'eleven_flash_v2_5')
        self.elevenlabs_output_format = os.getenv('ELEVENLABS_OUTPUT_FORMAT', 'mp3_22050_32')
        self.elevenlabs_base_url = os.getenv('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io/v1')

        self.load_data()
        self._initialized = True

    def get_llm_profile(self, profile: str = "default") -> Dict[str, Any]:
        """Returns LLM settings for a task profile.

        The "router" profile uses a smaller/faster model where available:
        tool selection is an easy structured task and this halves turn latency.
        """
        groq_model = self.groq_model
        if profile == "router" and self.groq_router_model:
            groq_model = self.groq_router_model
        return {
            "mode": self.llm_mode,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "openai_api_key": self.openai_api_key,
            "openai_model": self.openai_model,
            "groq_api_key": self.groq_api_key,
            "groq_base_url": self.groq_base_url,
            "groq_model": groq_model,
        }

    def load_data(self):
        """Loads all JSON data (mock Moka backend) from the data directory."""
        self.projects = self._load_json_file('projects.json', [])
        self.merchants = self._load_json_file('merchants.json', [])
        self.transactions = self._load_json_file('transactions.json', [])
        self.settlements = self._load_json_file('settlements.json', [])
        self.pos_devices = self._load_json_file('pos_devices.json', [])
        self.commission_plans = self._load_json_file('commission_plans.json', [])
        self.support_kb = self._load_json_file('support_kb.json', [])
        self.rules = self._load_json_file('rules.json', {})
        self.handoff_rules = self._load_json_file('handoff_rules.json', {})

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
            return self.projects[0].get('name', 'Moka Sesli Asistan')
        return 'Moka Sesli Asistan'

    def get_assistant_name(self) -> str:
        if self.projects:
            return self.projects[0].get('assistant_name', 'Ada')
        return 'Ada'

    def get_project_details(self) -> dict:
        """Returns company/assistant details (branding, products, support line)."""
        if self.projects:
            return self.projects[0]
        return {}

    def get_support_rules(self) -> dict:
        return self.rules.get('support_rules', {})

    def get_security_rules(self) -> dict:
        return self.rules.get('security_rules', {})

    def get_payout_rules(self) -> dict:
        return self.rules.get('payout_rules', {})

    def get_upsell_rules(self) -> dict:
        return self.rules.get('upsell_rules', {})

    def get_handoff_conditions(self) -> list:
        return self.handoff_rules.get('handoff_conditions', [])

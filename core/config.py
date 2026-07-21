import os
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv


class Config:
    """Ortam ayarlari + is verisine erisim.

    Eskiden data/*.json dosyalarini bellege yukluyordu. Artik tum is verisi
    SQLite'ta (data/moka.sqlite3); asagidaki veri ozellikleri repository'ye
    delege eden TEMBEL uyumluluk katmanidir. Yeni kod dogrudan
    MerchantRepository kullanmali.
    """

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
        self.business_db_path = os.path.join(self.data_dir, 'moka.sqlite3')

        self._repository = None

        # LLM Settings
        # Mode: 0 = Ollama (Local), 1 = OpenAI (Cloud), 2 = Groq (Cloud, free tier)
        self.llm_mode = int(os.getenv('LLM_MODE', '0'))
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        # Varsayilan, bu kurulumda YUKLU olan modeldir. .env silinse/tasinsa
        # bile "model not found" yerine calisir hale gelir. Baska model icin
        # .env'de OLLAMA_MODEL degistirin.
        self.ollama_model = os.getenv('OLLAMA_MODEL', 'gemma4:31b-cloud')
        self.openai_model = os.getenv('OPENAI_MODEL', 'gpt-4o')
        self.groq_api_key = os.getenv('GROQ_API_KEY', '')
        # Demo gunu sigortasi: birincil anahtar kota/ariza yerse otomatik gecis.
        self.groq_api_key_fallback = os.getenv('GROQ_API_KEY_FALLBACK', '')
        self.groq_base_url = os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
        # 120B: canli testte Turkce kalitesi 70B llama'dan belirgin iyi
        # (llama yabanci kelime karistiriyordu: "realizado", "erfolgreich").
        self.groq_model = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')
        # Router ayri modelde kosar: hem hizli hem de Groq free tier'da her model
        # AYRI dakikalik token kovasina sahip — cevap LLM'inin kotasini yemez.
        self.groq_router_model = os.getenv('GROQ_ROUTER_MODEL', 'openai/gpt-oss-20b')

        # Voice I/O settings
        self.whisper_model = os.getenv('WHISPER_MODEL', 'base')
        self.whisper_language = os.getenv('WHISPER_LANGUAGE', 'tr')
        self.whisper_device = os.getenv('WHISPER_DEVICE', 'cpu')
        self.voice_output_dir = os.getenv(
            'VOICE_OUTPUT_DIR',
            os.path.join(self.base_dir, 'voice_output'),
        )
        # Goreli yol Flask'ta app klasorune gore cozulur ve ses dosyalari 404
        # olur; her zaman proje kokune gore mutlaklastir.
        if not os.path.isabs(self.voice_output_dir):
            self.voice_output_dir = os.path.join(self.base_dir, self.voice_output_dir)

        self.elevenlabs_api_key = os.getenv('ELEVENLABS_API_KEY', '')
        self.elevenlabs_voice_id = os.getenv('ELEVENLABS_VOICE_ID', '')
        self.elevenlabs_model_id = os.getenv('ELEVENLABS_MODEL_ID', 'eleven_flash_v2_5')
        self.elevenlabs_output_format = os.getenv('ELEVENLABS_OUTPUT_FORMAT', 'mp3_22050_32')
        self.elevenlabs_base_url = os.getenv('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io/v1')

        self._initialized = True

    # ------------------------------------------------------------ repository

    @property
    def repository(self):
        """Is verisi repository'si (tembel kurulur).

        Import zamaninda kurulmaz: DB yoksa uygulamanin tamami degil, yalnizca
        veriye dokunan yol hata verir ve mesaj seed komutunu soyler.
        """
        if self._repository is None:
            from core.repository import MerchantRepository
            self._repository = MerchantRepository(self.business_db_path)
        return self._repository

    def reload_data(self) -> None:
        """Repository'yi bosaltir; bir sonraki erisimde yeniden acilir."""
        self._repository = None

    # ------------------------------------------- veri ozellikleri (uyumluluk)

    @property
    def merchants(self) -> List[Dict[str, Any]]:
        return self.repository.list_merchants()

    @property
    def transactions(self) -> List[Dict[str, Any]]:
        return self.repository.find_transactions_all()

    @property
    def settlements(self) -> List[Dict[str, Any]]:
        return self.repository.list_all_settlements()

    @property
    def pos_devices(self) -> List[Dict[str, Any]]:
        return self.repository.list_all_devices()

    @property
    def commission_plans(self) -> List[Dict[str, Any]]:
        return self.repository.list_plans()

    @property
    def support_kb(self) -> List[Dict[str, Any]]:
        return self.repository.list_kb_articles()

    @property
    def projects(self) -> List[Dict[str, Any]]:
        project = self.repository.get_config("project", {})
        return [project] if project else []

    @property
    def rules(self) -> Dict[str, Any]:
        return self.repository.get_config("rules", {}) or {}

    @property
    def handoff_rules(self) -> Dict[str, Any]:
        return self.repository.get_config("handoff_rules", {}) or {}

    # ---------------------------------------------------------------- getters

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
            "groq_api_key_fallback": self.groq_api_key_fallback,
            "groq_base_url": self.groq_base_url,
            "groq_model": groq_model,
        }

    def get_project_name(self) -> str:
        return self._project().get('name', 'Moka Sesli Asistan')

    def get_assistant_name(self) -> str:
        return self._project().get('assistant_name', 'Ada')

    def get_project_details(self) -> dict:
        """Returns company/assistant details (branding, products, support line)."""
        return self._project()

    def _project(self) -> Dict[str, Any]:
        try:
            return self.repository.get_config("project", {}) or {}
        except Exception:
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

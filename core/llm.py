import json
import os
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from core.config import Config


def is_llm_error(text: Any) -> bool:
    """LLM cagrisi basarisiz oldugunda donen 'Error...' metinlerini yakalar."""
    return isinstance(text, str) and text.strip().startswith("Error")


class LLMClient:
    def __init__(self):
        self.config = Config()

    def generate(self, system_prompt: str, user_prompt: str, json_mode: bool = False, profile: str = "default") -> str:
        """
        Generates a response from the configured LLM.
        """
        profile_settings = self.config.get_llm_profile(profile)
        if profile_settings["mode"] == 0:
            return self._call_ollama(system_prompt, user_prompt, json_mode, profile_settings)
        elif profile_settings["mode"] == 1:
            return self._call_openai(system_prompt, user_prompt, json_mode, profile_settings)
        else:
            return "Error: Invalid LLM_MODE configured."

    def _call_ollama(self, system_prompt: str, user_prompt: str, json_mode: bool, profile_settings: Dict[str, Any]) -> str:
        url = f"{profile_settings['ollama_base_url']}/api/chat"

        payload = {
            "model": profile_settings["ollama_model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False
        }
        
        if json_mode:
            payload["format"] = "json"

        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=90) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get('message', {}).get('content', '')
        except urllib.error.URLError as e:
            return f"Error: Could not connect to Ollama. {e}"
        except Exception as e:
            return f"Error calling Ollama: {str(e)}"

    def _call_openai(self, system_prompt: str, user_prompt: str, json_mode: bool, profile_settings: Dict[str, Any]) -> str:
        if not profile_settings["openai_api_key"]:
            return "Error: OPENAI_API_KEY not found in environment."

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {profile_settings['openai_api_key']}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": profile_settings["openai_model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
        
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result['choices'][0]['message']['content']
        except Exception as e:
            return f"Error calling OpenAI: {str(e)}"

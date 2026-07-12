import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any
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

        Modes: 0 = Ollama (local), 1 = OpenAI, 2 = Groq (OpenAI-compatible, free tier).
        The "router" profile may map to a smaller/faster model (see Config).
        """
        profile_settings = self.config.get_llm_profile(profile)
        mode = profile_settings["mode"]
        if mode == 0:
            return self._call_ollama(system_prompt, user_prompt, json_mode, profile_settings)
        if mode == 1:
            return self._call_openai_compatible(
                "https://api.openai.com/v1",
                profile_settings["openai_api_key"],
                profile_settings["openai_model"],
                system_prompt, user_prompt, json_mode,
            )
        if mode == 2:
            return self._call_groq(system_prompt, user_prompt, json_mode, profile_settings)
        return "Error: Invalid LLM_MODE configured."

    def _call_groq(self, system_prompt: str, user_prompt: str, json_mode: bool,
                   profile_settings: Dict[str, Any]) -> str:
        """Groq with one retry on rate limit / network error, then Ollama fallback."""
        result = self._call_openai_compatible(
            profile_settings["groq_base_url"],
            profile_settings["groq_api_key"],
            profile_settings["groq_model"],
            system_prompt, user_prompt, json_mode,
        )
        if is_llm_error(result):
            time.sleep(1.5)
            result = self._call_openai_compatible(
                profile_settings["groq_base_url"],
                profile_settings["groq_api_key"],
                profile_settings["groq_model"],
                system_prompt, user_prompt, json_mode,
            )
        if is_llm_error(result) and profile_settings.get("ollama_base_url"):
            fallback = self._call_ollama(system_prompt, user_prompt, json_mode, profile_settings)
            if not is_llm_error(fallback):
                return fallback
        return result

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

    def _call_openai_compatible(self, base_url: str, api_key: str, model: str,
                                system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if not api_key:
            return "Error: API key not found in environment."

        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Cloudflare, ciplak Python-urllib UA'sini 403'luyor (Groq'ta dogrulandi).
            "User-Agent": "moka-voice-agent/1.0",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload).encode('utf-8')
        for attempt in (0, 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=90) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['choices'][0]['message']['content']
            except urllib.error.HTTPError as e:
                # 429: saglayicinin soyledigi kadar bekle (Retry-After), bir kez dene.
                if e.code == 429 and attempt == 0:
                    retry_after = e.headers.get('retry-after') or e.headers.get('Retry-After')
                    try:
                        wait = min(float(retry_after), 8.0) if retry_after else 3.0
                    except ValueError:
                        wait = 3.0
                    time.sleep(wait)
                    continue
                return f"Error calling {url}: HTTP Error {e.code}: {e.reason}"
            except Exception as e:
                return f"Error calling {url}: {str(e)}"
        return f"Error calling {url}: rate limited"


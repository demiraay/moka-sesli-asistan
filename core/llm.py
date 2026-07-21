import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from core.config import Config
from core.errors import LLMError


def is_llm_error(text: Any) -> bool:
    """LLM cagrisi basarisiz oldugunda donen 'Error...' metinlerini yakalar.

    GERIYE UYUM: generate() hala string dondurur. Yeni kod chat() kullanmali ve
    LLMError yakalamali (bkz. core/errors.py).
    """
    return isinstance(text, str) and text.strip().startswith("Error")


@dataclass(frozen=True)
class ToolCall:
    """Modelin cagirmak istedigi arac. 'arguments' zaten parse edilmis gelir."""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass(frozen=True)
class LLMResponse:
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    model: str = ""


def _parse_tool_calls(message: Dict[str, Any]) -> List[ToolCall]:
    """OpenAI-uyumlu tool_calls dizisini ToolCall listesine cevirir.

    Savunmaci: 'arguments' string olarak gelir ve model bozuk JSON uretebilir.
    Bozuksa argumanlar bos kalir; agent loop bunu tool result olarak modele
    geri besler ve model duzeltme sansi bulur (cagri olmez).
    """
    calls: List[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = function.get("name") or ""
        if not name:
            continue
        raw_args = function.get("arguments")
        if isinstance(raw_args, dict):      # bazi saglayicilar dict dondurur
            arguments, raw_text = raw_args, json.dumps(raw_args, ensure_ascii=False)
        else:
            raw_text = raw_args if isinstance(raw_args, str) else ""
            try:
                parsed = json.loads(raw_text) if raw_text.strip() else {}
                arguments = parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                arguments = {}
        calls.append(ToolCall(
            id=str(raw.get("id") or f"call_{index}"),
            name=name,
            arguments=arguments,
            raw_arguments=raw_text,
        ))
    return calls


def _wire_messages(messages: List[Dict[str, Any]], *, arguments_as_json: bool
                   ) -> List[Dict[str, Any]]:
    """Mesaj listesini saglayicinin bekledigi bicime cevirir.

    Ic bicimde tool_calls[].function.arguments her zaman DICT'tir. Fark:
      - OpenAI / Groq : arguments JSON STRING olmali, tool mesaji
                        tool_call_id + name tasir.
      - Ollama        : arguments NESNE olmali (string gonderilirse
                        400 "Value looks like object, but can't find closing '}'"),
                        tool mesaji tool_name tasir.
    """
    wired: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")

        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments")
                if arguments_as_json and not isinstance(arguments, str):
                    function["arguments"] = json.dumps(arguments or {}, ensure_ascii=False)
                elif not arguments_as_json and isinstance(arguments, str):
                    try:
                        function["arguments"] = json.loads(arguments or "{}")
                    except ValueError:
                        function["arguments"] = {}
                entry = {"function": function}
                if arguments_as_json:
                    entry["id"] = call.get("id", "")
                    entry["type"] = "function"
                calls.append(entry)
            wired.append({"role": "assistant",
                          "content": message.get("content") or "",
                          "tool_calls": calls})
            continue

        if role == "tool":
            if arguments_as_json:
                wired.append({"role": "tool",
                              "tool_call_id": message.get("tool_call_id", ""),
                              "name": message.get("name", ""),
                              "content": message.get("content", "")})
            else:
                wired.append({"role": "tool",
                              "tool_name": message.get("name", ""),
                              "content": message.get("content", "")})
            continue

        wired.append(message)
    return wired


class LLMClient:
    def __init__(self):
        self.config = Config()

    # ------------------------------------------------------------------ chat

    def chat(self, messages: List[Dict[str, Any]], *,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: str = "auto",
             json_mode: bool = False,
             profile: str = "default",
             timeout: int = 25,
             max_tokens: Optional[int] = None) -> LLMResponse:
        """Cok mesajli sohbet; tool calling destekler.

        generate()'ten farki: mesaj listesi alir, tam 'message' nesnesini
        (content + tool_calls + usage) dondurur ve HATA DURUMUNDA LLMError
        FIRLATIR (string dondurmez).
        """
        settings = self.config.get_llm_profile(profile)
        mode = settings["mode"]

        if mode == 0:
            return self._call_ollama(messages, tools, tool_choice, json_mode,
                                     settings, timeout, max_tokens)
        if mode == 1:
            return self._call_openai_compatible(
                "https://api.openai.com/v1", settings["openai_api_key"],
                settings["openai_model"], messages, tools, tool_choice,
                json_mode, timeout, max_tokens)
        if mode == 2:
            return self._call_groq(messages, tools, tool_choice, json_mode,
                                   settings, timeout, max_tokens)
        raise LLMError(f"Invalid LLM_MODE configured: {mode}")

    # -------------------------------------------------------------- generate

    def generate(self, system_prompt: str, user_prompt: str,
                 json_mode: bool = False, profile: str = "default") -> str:
        """GERIYE UYUM sarmalayicisi: chat()'i cagirir, duz metin dondurur.

        Mevcut cagiranlar (briefing, cevap uretimi, testler) bu sozlesmeye bagli:
        hata durumunda exception degil "Error ..." stringi beklerler.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            # Timeout 90: eski davranis. Sesli akistaki agent loop kendi (kisa)
            # timeout'unu chat() uzerinden acikca gecer.
            response = self.chat(messages, json_mode=json_mode, profile=profile,
                                 timeout=90)
        except LLMError as error:
            return f"Error calling LLM: {error}"
        return response.content or ""

    # -------------------------------------------------------------- stream

    def stream(self, system_prompt: str, user_prompt: str, *,
               profile: str = "default", timeout: int = 600):
        """Cevabi PARCA PARCA uretir (generator).

        Yalnizca metin uretimi icin: planlama fazi arac cagirir, akmaz.
        Hata durumunda LLMError firlatir; cagiran o ana kadar akan metni
        elinde tutar ve kendi yedek metnine gecebilir.
        """
        settings = self.config.get_llm_profile(profile)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        mode = settings["mode"]

        if mode == 0:
            yield from self._stream_ollama(messages, settings, timeout)
            return
        if mode == 1:
            yield from self._stream_openai_compatible(
                "https://api.openai.com/v1", settings["openai_api_key"],
                settings["openai_model"], messages, timeout)
            return
        if mode == 2:
            keys = [settings["groq_api_key"]]
            if settings.get("groq_api_key_fallback"):
                keys.append(settings["groq_api_key_fallback"])
            last_error: Optional[LLMError] = None
            for key in keys:
                if not key:
                    continue
                try:
                    yield from self._stream_openai_compatible(
                        settings["groq_base_url"], key, settings["groq_model"],
                        messages, timeout)
                    return
                except LLMError as error:
                    # Akis BASLADIYSA yedek anahtara gecmek metni bastan
                    # yazdirir; bu yuzden yalnizca hic parca gelmediyse denenir.
                    if error.status == 0:
                        raise
                    last_error = error
            raise last_error or LLMError("no Groq API key configured", provider="groq")
        raise LLMError(f"Invalid LLM_MODE configured: {mode}")

    def _stream_ollama(self, messages, settings, timeout):
        url = f"{settings['ollama_base_url']}/api/chat"
        payload = {"model": settings["ollama_model"],
                   "messages": _wire_messages(messages, arguments_as_json=False),
                   "stream": True}
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        emitted = False
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    try:
                        chunk = json.loads(raw_line.decode("utf-8"))
                    except ValueError:
                        continue
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        emitted = True
                        yield piece
                    if chunk.get("done"):
                        return
        except Exception as error:
            # status=0: "akis basladiktan sonra koptu" isareti; cagiran yedek
            # saglayiciya gecmemeli, elindeki kismi metni kullanmali.
            raise LLMError(f"Ollama stream: {error}", provider="ollama",
                           status=0 if emitted else None) from error

    def _stream_openai_compatible(self, base_url, api_key, model, messages, timeout):
        if not api_key:
            raise LLMError("API key not found in environment.")
        url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {"model": model,
                   "messages": _wire_messages(messages, arguments_as_json=True),
                   "stream": True}
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json",
                     "User-Agent": "moka-voice-agent/1.0"})
        emitted = False
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or [{}]
                    piece = (choices[0].get("delta") or {}).get("content") or ""
                    if piece:
                        emitted = True
                        yield piece
        except Exception as error:
            raise LLMError(f"{url} stream: {error}", provider=base_url,
                           status=0 if emitted else None) from error

    # ------------------------------------------------------------- providers

    def _call_groq(self, messages, tools, tool_choice, json_mode, settings,
                   timeout, max_tokens) -> LLMResponse:
        """Groq: birincil anahtar -> yedek anahtar -> Ollama fallback."""
        keys = [settings["groq_api_key"]]
        if settings.get("groq_api_key_fallback"):
            keys.append(settings["groq_api_key_fallback"])

        last_error: Optional[LLMError] = None
        for key in keys:
            if not key:
                continue
            try:
                return self._call_openai_compatible(
                    settings["groq_base_url"], key, settings["groq_model"],
                    messages, tools, tool_choice, json_mode, timeout, max_tokens)
            except LLMError as error:
                last_error = error
                time.sleep(1.0)  # yedege gecmeden kisa nefes

        if settings.get("ollama_base_url"):
            try:
                return self._call_ollama(messages, tools, tool_choice, json_mode,
                                         settings, timeout, max_tokens)
            except LLMError:
                pass  # asil hata Groq'unki; onu firlat

        raise last_error or LLMError("no Groq API key configured", provider="groq")

    def _call_ollama(self, messages, tools, tool_choice, json_mode, settings,
                     timeout, max_tokens) -> LLMResponse:
        url = f"{settings['ollama_base_url']}/api/chat"
        payload: Dict[str, Any] = {
            "model": settings["ollama_model"],
            "messages": _wire_messages(messages, arguments_as_json=False),
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload.setdefault("options", {})["num_predict"] = max_tokens

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # HTTPError, URLError'in ALT SINIFIDIR; ayri yakalanmazsa sunucunun
            # dondurdugu 400/500 "baglanti kurulamadi" gibi gorunur.
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise LLMError(f"Ollama HTTP {error.code}: {error.reason} {detail}",
                           status=error.code, provider="ollama") from error
        except urllib.error.URLError as error:
            raise LLMError(f"Could not connect to Ollama. {error}",
                           retryable=True, provider="ollama") from error
        except Exception as error:
            raise LLMError(f"calling Ollama: {error}", provider="ollama") from error

        message = result.get("message") or {}
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=_parse_tool_calls(message),
            finish_reason=result.get("done_reason") or "stop",
            usage={"prompt_tokens": result.get("prompt_eval_count", 0),
                   "completion_tokens": result.get("eval_count", 0)},
            model=result.get("model", ""),
        )

    def _call_openai_compatible(self, base_url: str, api_key: str, model: str,
                                messages, tools, tool_choice, json_mode,
                                timeout, max_tokens) -> LLMResponse:
        if not api_key:
            raise LLMError("API key not found in environment.")

        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Cloudflare, ciplak Python-urllib UA'sini 403'luyor (Groq'ta dogrulandi).
            "User-Agent": "moka-voice-agent/1.0",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": _wire_messages(messages, arguments_as_json=True),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens

        for attempt in (0, 1):
            # messages/tools her cagirida degisebilir; encode'u DONGU ICINDE tut.
            data = json.dumps(payload).encode("utf-8")
            try:
                request = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as error:
                # 429: saglayicinin soyledigi kadar bekle (Retry-After), bir kez dene.
                if error.code == 429 and attempt == 0:
                    retry_after = (error.headers.get("retry-after")
                                   or error.headers.get("Retry-After"))
                    try:
                        wait = min(float(retry_after), 8.0) if retry_after else 3.0
                    except (TypeError, ValueError):
                        wait = 3.0
                    time.sleep(wait)
                    continue
                raise LLMError(f"{url}: HTTP Error {error.code}: {error.reason}",
                               status=error.code, retryable=error.code in (429, 500, 502, 503),
                               provider=base_url) from error
            except Exception as error:
                raise LLMError(f"{url}: {error}", provider=base_url) from error
        else:
            raise LLMError(f"{url}: rate limited", status=429, retryable=True,
                           provider=base_url)

        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"{url}: beklenmeyen yanit bicimi", provider=base_url) from error

        return LLMResponse(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(message),
            finish_reason=result["choices"][0].get("finish_reason", ""),
            usage=result.get("usage") or {},
            model=result.get("model", model),
        )

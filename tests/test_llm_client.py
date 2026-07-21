"""LLMClient.chat() tool calling katmani ve generate() geriye uyumu."""

import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.errors import LLMError
from core.llm import LLMClient, is_llm_error


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _completion(message: dict, finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "model": "openai/gpt-oss-20b",
    }


def _tool_call(name: str, arguments, call_id: str = "call_1") -> dict:
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


class TestChatToolCalling(unittest.TestCase):
    def setUp(self):
        Config._instance = None
        os.environ["LLM_MODE"] = "2"
        os.environ["GROQ_API_KEY"] = "test-key"
        os.environ["GROQ_API_KEY_FALLBACK"] = ""
        self.client = LLMClient()
        self.messages = [{"role": "user", "content": "Param ne zaman yatacak?"}]

    def test_parses_tool_calls_into_objects(self):
        payload = _completion(
            {"role": "assistant", "content": None,
             "tool_calls": [_tool_call("get_settlement_status", {"period": "latest"})]},
            finish_reason="tool_calls")

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            response = self.client.chat(self.messages, tools=[{"type": "function"}])

        self.assertEqual(len(response.tool_calls), 1)
        call = response.tool_calls[0]
        self.assertEqual(call.name, "get_settlement_status")
        self.assertEqual(call.arguments, {"period": "latest"})
        self.assertEqual(call.id, "call_1")
        self.assertEqual(response.finish_reason, "tool_calls")
        self.assertEqual(response.usage["prompt_tokens"], 100)

    def test_plain_content_response_has_no_tool_calls(self):
        payload = _completion({"role": "assistant", "content": "Merhaba Mehmet Bey."})

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            response = self.client.chat(self.messages)

        self.assertEqual(response.content, "Merhaba Mehmet Bey.")
        self.assertEqual(response.tool_calls, [])

    def test_broken_arguments_json_does_not_crash(self):
        """Model bozuk JSON uretirse argumanlar bos kalir, cagri olmez.

        Agent loop bunu tool result olarak modele geri besler.
        """
        payload = _completion(
            {"role": "assistant",
             "tool_calls": [_tool_call("find_transaction", '{"amount_try": 125')]},
            finish_reason="tool_calls")

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            response = self.client.chat(self.messages, tools=[{"type": "function"}])

        self.assertEqual(response.tool_calls[0].arguments, {})
        self.assertEqual(response.tool_calls[0].raw_arguments, '{"amount_try": 125')

    def test_tool_call_without_name_is_skipped(self):
        payload = _completion(
            {"role": "assistant",
             "tool_calls": [{"id": "x", "function": {"arguments": "{}"}}]},
            finish_reason="tool_calls")

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            response = self.client.chat(self.messages, tools=[{"type": "function"}])

        self.assertEqual(response.tool_calls, [])

    def test_tools_and_tool_choice_reach_the_payload(self):
        captured = {}

        def _capture(request, timeout=None):
            captured.update(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse(_completion({"content": "ok"}))

        tools = [{"type": "function", "function": {"name": "answer_general"}}]
        with patch("urllib.request.urlopen", side_effect=_capture):
            self.client.chat(self.messages, tools=tools, tool_choice="required",
                             max_tokens=200)

        self.assertEqual(captured["tools"], tools)
        self.assertEqual(captured["tool_choice"], "required")
        self.assertEqual(captured["max_tokens"], 200)
        self.assertEqual(captured["messages"], self.messages)


class TestChatErrorModel(unittest.TestCase):
    def setUp(self):
        Config._instance = None
        os.environ["LLM_MODE"] = "2"
        os.environ["GROQ_API_KEY"] = "test-key"
        os.environ["GROQ_API_KEY_FALLBACK"] = ""
        os.environ["OLLAMA_BASE_URL"] = ""      # Ollama fallback'i kapat
        self.client = LLMClient()
        self.messages = [{"role": "user", "content": "test"}]
        # _call_groq anahtarlar arasi 1sn bekliyor; testte gercek beklemeye gerek yok.
        sleep_patch = patch("time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def _http_error(self, code: int):
        return urllib.error.HTTPError("http://x", code, "Server Error", {}, None)

    def test_http_error_raises_llm_error(self):
        with patch("urllib.request.urlopen", side_effect=self._http_error(500)):
            with self.assertRaises(LLMError) as caught:
                self.client.chat(self.messages)
        self.assertEqual(caught.exception.status, 500)

    def test_generate_still_returns_error_string(self):
        """GERIYE UYUM: 94 mevcut test bu sozlesmeye bagli."""
        with patch("urllib.request.urlopen", side_effect=self._http_error(500)):
            result = self.client.generate("sistem", "kullanici")

        self.assertIsInstance(result, str)
        self.assertTrue(is_llm_error(result))

    def test_malformed_response_body_raises(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeHTTPResponse({"unexpected": True})):
            with self.assertRaises(LLMError):
                self.client.chat(self.messages)

    def test_missing_api_key_raises(self):
        os.environ["GROQ_API_KEY"] = ""
        Config._instance = None
        with self.assertRaises(LLMError):
            LLMClient().chat(self.messages)


class TestKeyFallback(unittest.TestCase):
    def setUp(self):
        Config._instance = None
        os.environ["LLM_MODE"] = "2"
        os.environ["GROQ_API_KEY"] = "primary-key"
        os.environ["GROQ_API_KEY_FALLBACK"] = "backup-key"
        os.environ["OLLAMA_BASE_URL"] = ""
        self.client = LLMClient()

    def test_falls_back_to_second_key_after_failure(self):
        seen_keys = []

        def _respond(request, timeout=None):
            seen_keys.append(request.headers.get("Authorization"))
            if len(seen_keys) == 1:
                raise urllib.error.HTTPError("http://x", 500, "boom", {}, None)
            return _FakeHTTPResponse(_completion({"content": "ikinci anahtar"}))

        with patch("urllib.request.urlopen", side_effect=_respond), \
             patch("time.sleep"):
            response = self.client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(response.content, "ikinci anahtar")
        self.assertEqual(seen_keys, ["Bearer primary-key", "Bearer backup-key"])

    def test_rate_limit_retries_same_key_once(self):
        attempts = []

        def _respond(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(
                    "http://x", 429, "slow down", {"retry-after": "2"}, None)
            return _FakeHTTPResponse(_completion({"content": "sonunda"}))

        with patch("urllib.request.urlopen", side_effect=_respond), \
             patch("time.sleep") as slept:
            response = self.client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(response.content, "sonunda")
        self.assertEqual(len(attempts), 2)
        slept.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()

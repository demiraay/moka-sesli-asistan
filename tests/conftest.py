"""Testler icin ortak yardimcilar."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import LLMResponse, ToolCall


def stub_llm_script(llm_client, script):
    """Bir LLM istemcisine senaryo yukler (hem chat hem generate).

    'script' eski bicimi korur: JSON gibi gorunen ogeler ARAC KARARI, digerleri
    cevap metnidir. Ornek:
        stub_llm_script(orch.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, nasil yardimci olabilirim?",
        ])

    Boylece agent loop'a geciste senaryolarin govdesi degismek zorunda kalmadi.
    """
    tool_batches = []
    replies = []
    for item in script:
        text = item if isinstance(item, str) else json.dumps(item)
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            replies.append(text)
            continue
        if isinstance(payload, dict) and payload.get("tool"):
            batch = [(payload["tool"], payload.get("args") or {})]
            if payload.get("card"):
                batch.append(("update_customer_card", payload["card"]))
            tool_batches.append(batch)
        else:
            replies.append(text)

    state = {"tools": list(tool_batches), "replies": list(replies)}

    def chat(messages, *, tools=None, tool_choice="auto", json_mode=False,
             profile="default", timeout=25, max_tokens=None):
        if not tools or not state["tools"]:
            return LLMResponse(content="ok", tool_calls=[], finish_reason="stop")
        batch = state["tools"].pop(0)
        return LLMResponse(
            content=None, finish_reason="tool_calls",
            tool_calls=[ToolCall(id=f"c{index}", name=name, arguments=args)
                        for index, (name, args) in enumerate(batch)])

    def generate(system_prompt, user_prompt, json_mode=False, profile="default"):
        if json_mode:       # AGENT_ENABLED=0 geri donus yolu
            if state["tools"]:
                batch = state["tools"].pop(0)
                name, args = batch[0]
                card = dict(batch[1][1]) if len(batch) > 1 else {}
            else:
                name, args, card = "answer_general", {}, {}
            return json.dumps({"tool": name, "args": args, "card": card},
                              ensure_ascii=False)
        return state["replies"].pop(0) if state["replies"] else "ok"

    def stream(system_prompt, user_prompt, *, profile="default", timeout=600):
        """Akan yol: cevabi kucuk parcalara bolerek verir.

        Bu sahte OLMAZSA akis testleri gercek aga cikip timeout yer.
        """
        text = state["replies"].pop(0) if state["replies"] else "ok"
        for index in range(0, len(text), 9):
            yield text[index:index + 9]

    llm_client.chat = chat
    llm_client.generate = generate
    llm_client.stream = stream
    return llm_client

"""Cok adimli agent loop: zincirleme, idempotency guard'lari, terminal kosullar."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import agent as agent_module
from core import tools
from core.agent import MAX_ITERATIONS, ToolPlanner, trim_transcript
from core.errors import LLMError
from core.llm import LLMResponse, ToolCall
from core.schemas import ResponseBuilder
from core.tools.context import ToolContext


class ScriptedLLM:
    """Sirayla verilen arac gruplarini dondurur; bitince duz cevap verir."""

    def __init__(self, batches, error=None):
        self.batches = list(batches)
        self.error = error
        self.calls = 0

    def chat(self, messages, *, tools=None, tool_choice="auto", json_mode=False,
             profile="default", timeout=25, max_tokens=None):
        self.calls += 1
        if self.error:
            raise self.error
        if not self.batches:
            return LLMResponse(content="bitti", tool_calls=[], finish_reason="stop")
        batch = self.batches.pop(0)
        return LLMResponse(
            content=None, finish_reason="tool_calls",
            tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)
                        for i, (name, args) in enumerate(batch)])


def make_context(**overrides):
    ctx = ToolContext(
        repo=MagicMock(), store=MagicMock(), builder=ResponseBuilder(),
        merchant={"merchant_id": "M-1001", "business_name": "Test"},
        user_id="u-test", user_profile={}, config=MagicMock())
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


class RecordingRunner:
    """run_tool yerine gecer; cagrilari kaydeder."""

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, name, args, ctx):
        self.calls.append((name, args))
        return self.results.get(name, f"{name} calisti")


class TestChaining(unittest.TestCase):
    def test_two_tools_chain_across_iterations(self):
        llm = ScriptedLLM([
            [("find_transaction", {})],
            [("find_transaction", {"amount_try": 1250})],
        ])
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[{"role": "user", "content": "x"}],
            ctx=make_context())

        self.assertEqual(plan.iterations, 2)
        self.assertEqual(plan.tool_names, ["find_transaction", "find_transaction"])
        self.assertEqual(plan.stop_reason, "done")
        self.assertEqual(len(runner.calls), 2)

    def test_first_iteration_forces_a_tool_call(self):
        """Model bos konusup cikmasin diye ilk turda tool_choice='required'."""
        seen = {}

        class Spy(ScriptedLLM):
            def chat(self, messages, *, tools=None, tool_choice="auto", **kwargs):
                seen.setdefault("first", tool_choice)
                return super().chat(messages, tools=tools, tool_choice=tool_choice, **kwargs)

        ToolPlanner(Spy([[("answer_general", {})]]), RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())
        self.assertEqual(seen["first"], "required")

    def test_no_tool_calls_ends_immediately(self):
        plan = ToolPlanner(ScriptedLLM([]), RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())
        self.assertEqual(plan.stop_reason, "done")
        self.assertEqual(plan.executed, [])


class TestIdempotencyGuards(unittest.TestCase):
    def test_pure_tool_repeat_is_served_from_cache(self):
        """Ayni saf arac + ayni argumanlar: handler IKINCI kez calismaz."""
        llm = ScriptedLLM([
            [("get_settlement_status", {"period": "latest"})],
            [("get_settlement_status", {"period": "latest"})],
        ])
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(len(runner.calls), 1, "saf arac iki kez calismamali")
        self.assertTrue(plan.executed[1].cached)

    def test_side_effect_repeat_is_suppressed_not_cached(self):
        """Yan etkili arac tekrarinda handler calismaz ve modele uyari doner."""
        llm = ScriptedLLM([
            [("create_payment_link", {"amount_try": 500})],
            [("create_payment_link", {"amount_try": 500})],
        ])
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(plan.executed[1].suppressed)
        self.assertIn("ZATEN CALISTIRILDI", plan.executed[1].result)

    def test_once_per_call_tool_blocked_on_second_turn(self):
        """send_statement gorusme boyu tek sefer: ikinci TURDA da calismaz."""
        profile = {}
        runner = RecordingRunner()

        for _ in range(2):
            ToolPlanner(ScriptedLLM([[("send_statement", {"period": "this_month"})]]),
                        runner).run(system_prompt="s", messages=[],
                                    ctx=make_context(user_profile=profile))

        self.assertEqual(len(runner.calls), 1, "ekstre iki kez gonderilmemeli")

    def test_side_effect_cap_per_turn(self):
        llm = ScriptedLLM([
            [("create_payment_link", {"amount_try": 1})],
            [("create_payment_link", {"amount_try": 2})],
            [("create_payment_link", {"amount_try": 3})],
        ])
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(len(runner.calls), agent_module.MAX_SIDE_EFFECTS_PER_TURN)
        self.assertTrue(plan.executed[-1].suppressed)

    def test_different_args_are_not_deduplicated(self):
        llm = ScriptedLLM([
            [("find_transaction", {"amount_try": 100})],
            [("find_transaction", {"amount_try": 200})],
        ])
        runner = RecordingRunner()
        ToolPlanner(llm, runner).run(system_prompt="s", messages=[], ctx=make_context())
        self.assertEqual(len(runner.calls), 2)


class TestTerminalConditions(unittest.TestCase):
    def test_handoff_stops_the_loop(self):
        llm = ScriptedLLM([
            [("trigger_handoff", {"reason": "ofkeli"})],
            [("recommend_offer", {"trigger": "volume_growth"})],
        ])
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertTrue(plan.handoff_triggered)
        self.assertEqual(plan.stop_reason, "handoff")
        self.assertEqual([name for name, _ in runner.calls], ["trigger_handoff"],
                         "devirden sonra baska arac calismamali")

    def test_max_iterations_caps_the_loop(self):
        llm = ScriptedLLM([[("find_transaction", {"amount_try": i})]
                           for i in range(MAX_ITERATIONS + 5)])
        plan = ToolPlanner(llm, RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(plan.stop_reason, "max_iterations")
        self.assertEqual(plan.iterations, MAX_ITERATIONS)

    def test_deadline_is_disabled_by_default(self):
        """Varsayilan 0 = sinirsiz: planlama ortasinda kesilmez."""
        self.assertEqual(agent_module.PLAN_DEADLINE_S, 0)

    def test_deadline_stops_the_loop_when_configured(self):
        original = agent_module.PLAN_DEADLINE_S
        # 1e-9: monotonic cozunurlugunun altinda, yani ilk kontrolde asilmis olur.
        agent_module.PLAN_DEADLINE_S = 1e-9          # acikca ayarlanirsa keser
        try:
            llm = ScriptedLLM([[("find_transaction", {"amount_try": i})] for i in range(3)])
            plan = ToolPlanner(llm, RecordingRunner()).run(
                system_prompt="s", messages=[], ctx=make_context())
            self.assertEqual(plan.stop_reason, "deadline")
            self.assertEqual(plan.iterations, 1)
        finally:
            agent_module.PLAN_DEADLINE_S = original


class TestErrorHandling(unittest.TestCase):
    def test_llm_error_does_not_crash(self):
        plan = ToolPlanner(ScriptedLLM([], error=LLMError("kota doldu")),
                           RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(plan.stop_reason, "llm_error")
        self.assertIn("kota doldu", plan.llm_error)
        self.assertEqual(plan.executed, [])

    def test_unknown_tool_is_reported_back_to_the_model(self):
        plan = ToolPlanner(ScriptedLLM([[("olmayan_arac", {})]]),
                           RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())

        record = plan.executed[0]
        self.assertEqual(record.error, "unknown_tool")
        self.assertIn("diye bir arac yok", record.result)

    def test_handler_error_keeps_the_loop_alive(self):
        """Handler patlarsa hata TOOL RESULT olur; model tekrar deneyebilir."""
        def failing_runner(name, args, ctx):
            if name == "find_transaction":
                return "HATA: 'find_transaction' calistirilamadi (RuntimeError)."
            return "ok"

        llm = ScriptedLLM([
            [("find_transaction", {"amount_try": 1})],
            [("get_settlement_status", {"period": "latest"})],
        ])
        plan = ToolPlanner(llm, failing_runner).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(plan.tool_names, ["find_transaction", "get_settlement_status"])
        self.assertIsNotNone(plan.executed[0].error)
        self.assertEqual(plan.stop_reason, "done")

    def test_failed_tool_result_is_not_cached(self):
        """Hata onbellege girerse model asla tekrar deneyemez."""
        attempts = []

        def flaky(name, args, ctx):
            attempts.append(name)
            return "HATA: gecici sorun"

        llm = ScriptedLLM([
            [("find_transaction", {"amount_try": 1})],
            [("find_transaction", {"amount_try": 1})],
        ])
        ToolPlanner(llm, flaky).run(system_prompt="s", messages=[], ctx=make_context())
        self.assertEqual(len(attempts), 2, "basarisiz arac tekrar denenebilmeli")


class TestUsageAccounting(unittest.TestCase):
    def test_token_usage_is_accumulated(self):
        class UsageLLM(ScriptedLLM):
            def chat(self, messages, **kwargs):
                response = super().chat(messages, **kwargs)
                return LLMResponse(content=response.content,
                                   tool_calls=response.tool_calls,
                                   finish_reason=response.finish_reason,
                                   usage={"prompt_tokens": 100, "completion_tokens": 10})

        llm = UsageLLM([[("find_transaction", {"amount_try": 1})],
                        [("find_transaction", {"amount_try": 2})]])
        plan = ToolPlanner(llm, RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())

        # 2 arac turu + 1 kapanis turu
        self.assertEqual(plan.usage["prompt_tokens"], 300)


class TestTranscriptTrimming(unittest.TestCase):
    """Kirpma TUR sinirinda yapilir; mesaj ICERIGI kirpilmaz."""

    def _transcript(self, turns):
        messages = []
        for index in range(turns):
            messages.append({"role": "user", "content": f"soru {index}"})
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": f"c{index}", "type": "function",
                                             "function": {"name": "find_transaction",
                                                          "arguments": {}}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{index}",
                             "name": "find_transaction", "content": "sonuc"})
            messages.append({"role": "assistant", "content": f"cevap {index}"})
        return messages

    def test_short_transcript_is_untouched(self):
        transcript = self._transcript(3)
        self.assertEqual(trim_transcript(transcript, max_turns=10), transcript)

    def test_trimming_starts_at_a_user_message(self):
        """Yetim 'tool' mesaji birakilirsa saglayici hata verir."""
        trimmed = trim_transcript(self._transcript(10), max_turns=3)
        self.assertEqual(trimmed[0]["role"], "user")

    def test_trimming_keeps_the_last_n_turns(self):
        trimmed = trim_transcript(self._transcript(10), max_turns=3)
        user_messages = [m for m in trimmed if m["role"] == "user"]
        self.assertEqual(len(user_messages), 3)
        self.assertEqual(user_messages[-1]["content"], "soru 9")

    def test_tool_calls_and_results_survive_trimming(self):
        """Asil kazanim: onceki turun ARAC SONUCLARI baglamda kalir."""
        trimmed = trim_transcript(self._transcript(10), max_turns=2)
        self.assertTrue(any(m["role"] == "tool" for m in trimmed))
        self.assertTrue(any(m.get("tool_calls") for m in trimmed))

    def test_message_content_is_never_truncated(self):
        long_text = "x" * 5000
        transcript = [{"role": "user", "content": long_text}]
        self.assertEqual(trim_transcript(transcript, max_turns=5)[0]["content"], long_text)

    def test_zero_means_unlimited(self):
        transcript = self._transcript(50)
        self.assertEqual(len(trim_transcript(transcript, max_turns=0)), len(transcript))


class TestPlanMessages(unittest.TestCase):
    """Planlayici, urettigi transkript parcasini geri vermeli."""

    def test_plan_returns_tool_calls_and_results(self):
        llm = ScriptedLLM([[("find_transaction", {"amount_try": 1250})]])
        plan = ToolPlanner(llm, RecordingRunner({"find_transaction": "1 islem bulundu"})).run(
            system_prompt="s", messages=[{"role": "user", "content": "x"}],
            ctx=make_context())

        roles = [m["role"] for m in plan.messages]
        self.assertEqual(roles, ["assistant", "tool"])
        self.assertEqual(plan.messages[0]["tool_calls"][0]["function"]["name"],
                         "find_transaction")
        self.assertEqual(plan.messages[1]["content"], "1 islem bulundu")

    def test_nudge_is_not_persisted(self):
        """Sentetik durtme mesaji transkripte SIZMAMALI."""
        class IgnoresToolChoice:
            def __init__(self): self.calls = 0
            def chat(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="duz yazi", tool_calls=[], finish_reason="stop")
                if self.calls == 2:
                    return LLMResponse(content=None, finish_reason="tool_calls",
                                       tool_calls=[ToolCall(id="c0", name="answer_general",
                                                            arguments={})])
                return LLMResponse(content="bitti", tool_calls=[], finish_reason="stop")

        plan = ToolPlanner(IgnoresToolChoice(), RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())
        contents = [str(m.get("content") or "") for m in plan.messages]
        self.assertFalse(any("ARAC CAGIR" in c for c in contents))


class TestCustomerCardTool(unittest.TestCase):
    def test_card_merge_preserves_unmentioned_fields(self):
        """Onceki surumde kart TAMAMEN eziliyordu.

        Model yalnizca {"mood": "gergin"} donerse issue/tutar/terminal
        sessizce siliniyordu.
        """
        ctx = make_context(user_profile={
            "card": {"issue": "hakedis gecikmesi", "amount_mentioned_try": 1250,
                     "terminal_id": "TRM-4451"}})
        tools.REGISTRY["update_customer_card"].fn(ctx, {"mood": "gergin"})

        card = ctx.user_profile["card"]
        self.assertEqual(card["issue"], "hakedis gecikmesi")
        self.assertEqual(card["amount_mentioned_try"], 1250)
        self.assertEqual(card["terminal_id"], "TRM-4451")
        self.assertEqual(card["mood"], "gergin")
        self.assertEqual(card["changed"], ["mood"])

    def test_card_reports_changed_fields_only(self):
        ctx = make_context(user_profile={"card": {"issue": "eski sorun"}})
        tools.REGISTRY["update_customer_card"].fn(ctx, {"issue": "eski sorun"})
        self.assertEqual(ctx.user_profile["card"]["changed"], [])

    def test_empty_values_do_not_erase(self):
        ctx = make_context(user_profile={"card": {"issue": "hakedis"}})
        tools.REGISTRY["update_customer_card"].fn(ctx, {"issue": "", "mood": None})
        self.assertEqual(ctx.user_profile["card"]["issue"], "hakedis")


if __name__ == "__main__":
    unittest.main()


class TestProviderCompatibility(unittest.TestCase):
    """Canli Ollama denemesinde bulunan uyumluluk hatalarinin regresyonu."""

    def test_ollama_receives_arguments_as_object(self):
        """Ollama 'arguments' NESNE bekler.

        JSON string gonderilirse HTTP 400 doner:
        "Value looks like object, but can't find closing '}' symbol".
        """
        from core.llm import _wire_messages

        messages = [{
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c0", "type": "function",
                            "function": {"name": "find_transaction",
                                         "arguments": {"amount_try": 1250}}}],
        }]
        wired = _wire_messages(messages, arguments_as_json=False)
        arguments = wired[0]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(arguments, dict)
        self.assertEqual(arguments["amount_try"], 1250)

    def test_openai_receives_arguments_as_json_string(self):
        from core.llm import _wire_messages

        messages = [{
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c0", "type": "function",
                            "function": {"name": "find_transaction",
                                         "arguments": {"amount_try": 1250}}}],
        }]
        wired = _wire_messages(messages, arguments_as_json=True)
        call = wired[0]["tool_calls"][0]
        self.assertIsInstance(call["function"]["arguments"], str)
        self.assertEqual(json.loads(call["function"]["arguments"]), {"amount_try": 1250})
        self.assertEqual(call["id"], "c0")

    def test_tool_message_shape_per_provider(self):
        from core.llm import _wire_messages

        messages = [{"role": "tool", "tool_call_id": "c0",
                     "name": "find_transaction", "content": "1 islem"}]

        ollama = _wire_messages(messages, arguments_as_json=False)[0]
        self.assertEqual(ollama["tool_name"], "find_transaction")
        self.assertNotIn("tool_call_id", ollama)

        openai = _wire_messages(messages, arguments_as_json=True)[0]
        self.assertEqual(openai["tool_call_id"], "c0")
        self.assertEqual(openai["name"], "find_transaction")

    def test_plain_messages_pass_through_untouched(self):
        from core.llm import _wire_messages

        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        self.assertEqual(_wire_messages(messages, arguments_as_json=True), messages)


class TestToolChoiceFallback(unittest.TestCase):
    """tool_choice='required' her saglayicida uygulanmiyor (Ollama yok sayiyor)."""

    def test_model_is_nudged_when_it_answers_without_tools(self):
        class IgnoresToolChoice:
            """Ilk cagride duz yazi doner; ikincide araci cagirir."""

            def __init__(self):
                self.calls = 0
                self.snapshots = []

            def chat(self, messages, **kwargs):
                self.calls += 1
                self.snapshots.append(list(messages))
                if self.calls == 1:      # tool_choice='required' yok sayilir
                    return LLMResponse(content="Elbette, size sunu onerebilirim...",
                                       tool_calls=[], finish_reason="stop")
                if self.calls == 2:      # durtme sonrasi araci cagirir
                    return LLMResponse(
                        content=None, finish_reason="tool_calls",
                        tool_calls=[ToolCall(id="c0", name="recommend_offer",
                                             arguments={"trigger": "social_selling"})])
                return LLMResponse(content="bitti", tool_calls=[], finish_reason="stop")

        llm = IgnoresToolChoice()
        runner = RecordingRunner()
        plan = ToolPlanner(llm, runner).run(
            system_prompt="s", messages=[{"role": "user", "content": "x"}],
            ctx=make_context())

        self.assertEqual(plan.tool_names, ["recommend_offer"],
                         "durtme sonrasi arac calismaliydi")
        # Durtme IKINCI cagrinin mesajlarinda olmali (ucuncude arac sonucu var).
        nudge = llm.snapshots[1][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("ARAC CAGIR", nudge["content"])

    def test_nudge_happens_only_once(self):
        """Model israrla arac cagirmiyorsa sonsuz durtme olmamali."""
        class NeverCallsTools:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, **kwargs):
                self.calls += 1
                return LLMResponse(content="duz yazi", tool_calls=[], finish_reason="stop")

        llm = NeverCallsTools()
        plan = ToolPlanner(llm, RecordingRunner()).run(
            system_prompt="s", messages=[], ctx=make_context())

        self.assertEqual(llm.calls, 2, "en fazla bir kez durtulmeli")
        self.assertEqual(plan.stop_reason, "done")
        self.assertEqual(plan.executed, [])


class TestDormantMerchantsAccessControl(unittest.TestCase):
    """find_dormant_merchants TUM musteri tabanini gorur — kimin cagirabildigi kritik."""

    def _context(self, **overrides):
        repo = MagicMock()
        repo.list_dormant_merchants.return_value = [
            {"merchant_id": "M-1007", "business_name": "Yildiz Cafe",
             "owner_name": "Ayse Yildiz", "drop_pct": 88.6, "lost_volume_try": 62333},
            {"merchant_id": "M-1011", "business_name": "Taze Manav",
             "owner_name": "Ali Taze", "drop_pct": 89.4, "lost_volume_try": 33667},
        ]
        return make_context(repo=repo, **overrides)

    def test_merchant_on_the_phone_cannot_list_other_merchants(self):
        """Hattaki isletme rakiplerinin ciro verisini OGRENEMEZ."""
        ctx = self._context(channel="voice", user_id="call-abc123")
        result = tools.REGISTRY["find_dormant_merchants"].fn(ctx, {})

        self.assertIn("REDDEDILDI", result)
        self.assertNotIn("Yildiz Cafe", result)
        facts = " ".join(ctx.builder.build()["message_facts"])
        self.assertNotIn("Yildiz Cafe", facts)
        self.assertNotIn("62", facts)
        ctx.repo.list_dormant_merchants.assert_not_called()

    def test_whatsapp_merchant_cannot_list_other_merchants(self):
        ctx = self._context(channel="whatsapp", user_id="+905321112233")
        result = tools.REGISTRY["find_dormant_merchants"].fn(ctx, {})
        self.assertIn("REDDEDILDI", result)
        ctx.repo.list_dormant_merchants.assert_not_called()

    def test_panel_test_chat_is_NOT_internal(self):
        """Panel 'Test Sohbeti' bir ISLETME SIMULATORUDUR, operator konsolu degil.

        Demo sirasinda o ekrandan baska isletmelerin verisi sorulamamali.
        """
        ctx = self._context(channel="panel", user_id="panel-test")
        result = tools.REGISTRY["find_dormant_merchants"].fn(ctx, {})
        self.assertIn("REDDEDILDI", result)
        ctx.repo.list_dormant_merchants.assert_not_called()

    def test_ops_console_can_list_them(self):
        ctx = self._context(channel="ops", user_id="ops-console")
        result = tools.REGISTRY["find_dormant_merchants"].fn(ctx, {})

        self.assertIn("2 uyuyan isletme", result)
        self.assertIn("Yildiz Cafe", result)
        facts = " ".join(ctx.builder.build()["message_facts"])
        self.assertIn("Yildiz Cafe", facts)
        self.assertIn("Taze Manav", facts)

    def test_limit_is_clamped(self):
        ctx = self._context(channel="ops", user_id="ops-console")
        tools.REGISTRY["find_dormant_merchants"].fn(ctx, {"limit": 999})
        # Liste 2 kayitli; tavan kirilmadan calismali
        self.assertIn("Taze Manav", " ".join(ctx.builder.build()["message_facts"]))

    def test_empty_result_is_reported_honestly(self):
        ctx = self._context(channel="ops", user_id="ops-console")
        ctx.repo.list_dormant_merchants.return_value = []
        result = tools.REGISTRY["find_dormant_merchants"].fn(ctx, {})
        self.assertIn("yok", result.lower())


class TestCardMirroring(unittest.TestCase):
    """Kart, modelin YAPISAL arac argumanlarindan otomatik doldurulur.

    Boylece basit turlarda ayrica update_customer_card cagirmaya gerek kalmaz —
    paralel arac cagirmayan modellerde bu tam bir LLM turu tasarrufudur.
    """

    def test_transaction_args_land_in_the_card(self):
        ctx = make_context(user_profile={})
        tools.mirror_args_to_card(ctx, "find_transaction",
                                  {"amount_try": 1250.0, "date": "dün", "card_last4": "4832"})
        card = ctx.user_profile["card"]
        self.assertEqual(card["amount_mentioned_try"], 1250.0)
        self.assertEqual(card["date_mentioned"], "dün")
        self.assertEqual(card["card_last4"], "4832")

    def test_symptom_becomes_the_issue(self):
        ctx = make_context(user_profile={})
        tools.mirror_args_to_card(ctx, "troubleshoot_pos", {"symptom": "cihaz açılmıyor"})
        self.assertEqual(ctx.user_profile["card"]["issue"], "cihaz açılmıyor")

    def test_existing_card_fields_are_preserved(self):
        ctx = make_context(user_profile={"card": {"mood": "gergin", "issue": "eski"}})
        tools.mirror_args_to_card(ctx, "find_transaction", {"amount_try": 500})
        card = ctx.user_profile["card"]
        self.assertEqual(card["mood"], "gergin")       # dokunulmadi
        self.assertEqual(card["issue"], "eski")
        self.assertEqual(card["amount_mentioned_try"], 500)

    def test_empty_values_do_not_overwrite(self):
        ctx = make_context(user_profile={"card": {"card_last4": "4832"}})
        tools.mirror_args_to_card(ctx, "find_transaction", {"card_last4": "", "amount_try": None})
        self.assertEqual(ctx.user_profile["card"]["card_last4"], "4832")

    def test_card_tool_itself_is_not_mirrored(self):
        """update_customer_card kendi mantigini calistirir; cift islem olmasin."""
        ctx = make_context(user_profile={})
        tools.mirror_args_to_card(ctx, "update_customer_card", {"terminal_id": "TRM-1"})
        self.assertNotIn("card", ctx.user_profile)

    def test_unrelated_args_are_ignored(self):
        ctx = make_context(user_profile={})
        tools.mirror_args_to_card(ctx, "get_settlement_status", {"period": "latest"})
        self.assertNotIn("card", ctx.user_profile)


class TestStatementChannelGuard(unittest.TestCase):
    """Ekstre kanali MUSTERININ secimidir; model bilmeden gondermemeli.

    Canli denemede gorulen: Ada "e-posta mi SMS mi?" diye SORARKEN ekstre
    coktan gonderilmisti — sunulan secim gercek degildi.
    """

    def _context(self):
        return make_context(user_profile={},
                            merchant={"merchant_id": "M-1001", "business_name": "Test",
                                      "email": "a@b.com"})

    def test_missing_channel_does_not_send(self):
        ctx = self._context()
        result = tools.REGISTRY["send_statement"].fn(ctx, {"period": "this_month"})

        self.assertIn("GONDERILMEDI", result)
        ctx.store.enqueue_outbound_message.assert_not_called()
        facts = " ".join(ctx.builder.build()["message_facts"])
        self.assertIn("HENÜZ GÖNDERİLMEDİ", facts)

    def test_invalid_channel_does_not_send(self):
        ctx = self._context()
        result = tools.REGISTRY["send_statement"].fn(ctx, {"channel": "guvercin"})
        self.assertIn("GONDERILMEDI", result)
        ctx.store.enqueue_outbound_message.assert_not_called()

    def test_email_channel_sends_and_says_email(self):
        """SMTP kapaliyken (varsayilan) gonderim SIMULE edilir."""
        ctx = self._context()
        result = tools.REGISTRY["send_statement"].fn(ctx, {"channel": "email"})
        self.assertNotIn("GONDERILMEDI", result)
        ctx.store.enqueue_outbound_message.assert_called_once()
        self.assertIn("e-posta", " ".join(ctx.builder.build()["message_facts"]))

    def test_sms_channel_says_sms_not_email(self):
        ctx = self._context()
        tools.REGISTRY["send_statement"].fn(ctx, {"channel": "sms"})
        facts = " ".join(ctx.builder.build()["message_facts"])
        self.assertIn("kısa mesaj", facts)
        self.assertNotIn("e-posta", facts)

    def test_channel_is_required_in_the_schema(self):
        schema = tools.REGISTRY["send_statement"].parameters
        self.assertIn("channel", schema.get("required", []))


class TestSearchInsteadOfAsking(unittest.TestCase):
    """Ajan musteriye VERI SORMAZ, arar.

    Canli hata: musteri "44 bin 104 ne?" diye sordu, ajan ona "bu tutarin
    hangi tarihte gerceklestigini paylasabilir misiniz?" diye geri sordu.
    Oysa veri sistemdeydi.
    """

    def _context(self, transactions=None, settlements=None):
        repo = MagicMock()
        repo.find_transactions.return_value = transactions if transactions is not None else []
        repo.list_settlements.return_value = settlements or []
        repo.get_settlement_for_transaction.return_value = None
        return make_context(repo=repo)

    def test_empty_search_tells_the_model_not_to_interrogate(self):
        ctx = self._context()
        result = tools.REGISTRY["find_transaction"].fn(ctx, {"amount_try": 44104})
        self.assertIn("Eslesen islem yok", result)
        self.assertIn("VERI SORMA", result)

    def test_amount_matching_a_settlement_is_pointed_out(self):
        """Musterinin soyledigi tutar bir HAKEDIS toplami olabilir."""
        ctx = self._context(settlements=[
            {"batch_id": "SET-9012", "net_try": 44104.0, "gross_try": 45230.0}])
        result = tools.REGISTRY["find_transaction"].fn(ctx, {"amount_try": 44104})

        self.assertIn("HAKEDIS", result)
        self.assertIn("SET-9012", result)
        self.assertIn("get_settlement_status", result,
                      "modele bir sonraki adim soylenmedi")

    def test_gross_amount_also_matches(self):
        ctx = self._context(settlements=[
            {"batch_id": "SET-9012", "net_try": 44104.0, "gross_try": 45230.0}])
        result = tools.REGISTRY["find_transaction"].fn(ctx, {"amount_try": 45230})
        self.assertIn("SET-9012", result)

    def test_unrelated_amount_gets_no_settlement_hint(self):
        ctx = self._context(settlements=[
            {"batch_id": "SET-9012", "net_try": 44104.0, "gross_try": 45230.0}])
        result = tools.REGISTRY["find_transaction"].fn(ctx, {"amount_try": 7})
        self.assertNotIn("HAKEDIS", result)

    def test_search_without_amount_gets_no_hint(self):
        ctx = self._context(settlements=[
            {"batch_id": "SET-9012", "net_try": 44104.0, "gross_try": 45230.0}])
        result = tools.REGISTRY["find_transaction"].fn(ctx, {"date": "dün"})
        self.assertNotIn("HAKEDIS", result)

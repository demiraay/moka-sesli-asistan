"""Cok adimli agent loop (native tool calling).

Onceki mimari tek atimliktik: router LLM bir arac secer, if/elif ile calistirilir,
sonuc ikinci LLM'e verilirdi. Model ikinci bir arac cagiramaz, aracin sonucunu
gorup fikir degistiremez, eksik argumani duzeltemezdi.

IKI FAZLI TASARIM
  Faz A (planlama, kucuk model): sirali tool-calling dongusu. Modelin duz yazisi
        ATILIR; tek isi arac cagirmak.
  Faz B (kompozisyon, buyuk model): araci olmayan tek cagri; ResponseBuilder
        ciktisi bugunku gibi "CONTEXT FROM TOOLS" olarak gider.

Neden ikiye bolundu:
  - Groq free tier'da her model AYRI dakikalik token kovasi kullanir; darbogaz
    olan buyuk model kovasi loop yuzunden buyumez.
  - Turkce cevap kalitesi buyuk modelde kalir.
  - prompts.py, _make_speech_friendly ve context_json sozlesmesi hic degismez.

SIRALI: gpt-oss modelleri PARALEL tool call desteklemez (Groq dokumantasyonu),
bu yuzden dongu tur basina tek arac calistirir ve sonucu geri besler.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core import tools
from core.errors import LLMError
from core.tools.context import ToolContext
from core.tools.registry import SIDE_EFFECT, TERMINAL


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_ITERATIONS = _int_env("AGENT_MAX_ITERATIONS", 4)

# Planlayiciya giden gecmis penceresi (TUR sayisi; bir tur = kullanici mesaji +
# arac cagrilari + arac sonuclari + asistanin cevabi).
#
# Modern modellerde baglam bol; agresif kirpma bilgi kaybettirmekten baska ise
# yaramiyordu. Sinir yine de var cunku Groq ucretsiz katmanda DAKIKALIK TOKEN
# butcesi (TPM) baglam penceresinden once dolar. 0 = sinirsiz.
MAX_TRANSCRIPT_TURNS = _int_env("AGENT_MAX_TRANSCRIPT_TURNS", 24)

# SURE SINIRI VARSAYILAN OLARAK KAPALI.
#
# Sure siniri planlamayi ORTASINDA keser: model araci cagirmis ama sonucu
# degerlendirememis olur, asistan da yarim baglamla cevap yazar. Gosterimde
# bunun maliyeti (yanlis/eksik cevap) beklemenin maliyetinden buyuk.
# Ihtiyac halinde AGENT_PLAN_DEADLINE_S ile saniye cinsinden acilir.
PLAN_DEADLINE_S = _float_env("AGENT_PLAN_DEADLINE_S", 0)   # 0 = sinirsiz

# Bu bir DAVRANIS siniri degil, olu baglantiya karsi sigorta: yanit hic
# gelmezse cagrinin sonsuza kadar asili kalmasini onler.
PLAN_TIMEOUT_S = _int_env("AGENT_PLAN_TIMEOUT_S", 600)
# Arac sonucu tavani. Ozetler zaten kisa yazilir; bu yalnizca kacak bir
# ciktinin baglami sisirmesine karsi emniyet subabidir.
MAX_TOOL_RESULT_CHARS = _int_env("AGENT_MAX_TOOL_RESULT_CHARS", 4000)
# Tur basina en fazla kac yan etkili arac calisabilir (cifte kayit sigortasi).
MAX_SIDE_EFFECTS_PER_TURN = _int_env("AGENT_MAX_SIDE_EFFECTS", 2)


@dataclass
class ExecutedTool:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    result: str = ""
    error: Optional[str] = None
    suppressed: bool = False
    cached: bool = False


@dataclass
class PlanResult:
    executed: List[ExecutedTool] = field(default_factory=list)
    handoff_triggered: bool = False
    iterations: int = 0
    stop_reason: str = "done"     # done|handoff|max_iterations|deadline|llm_error|loop_detected
    llm_error: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    # Bu turda uretilen transkript parcasi: asistanin arac cagrilari + arac
    # sonuclari. Bir sonraki turda planlayiciya AYNEN geri verilir, boylece
    # model "gecen sefer ne ogrendigini" duz yazidan cikarmak zorunda kalmaz.
    messages: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def tool_names(self) -> List[str]:
        return [item.name for item in self.executed]


class ToolPlanner:
    """Faz A: modelin araclari kendi secip zincirledigi dongu."""

    def __init__(self, llm_client, run_tool: Callable[[str, Dict[str, Any], ToolContext], str],
                 profile: str = "router"):
        self.llm_client = llm_client
        self.run_tool = run_tool
        self.profile = profile

    def run(self, *, system_prompt: str, messages: List[Dict[str, Any]],
            ctx: ToolContext, on_tool=None) -> PlanResult:
        conversation: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt}, *messages]
        schema = tools.openai_tools_schema()
        plan = PlanResult()
        seen: Dict[str, str] = {}
        deadline = (time.monotonic() + PLAN_DEADLINE_S) if PLAN_DEADLINE_S > 0 else None
        nudged = False
        ctx.user_profile["_side_effect_count"] = 0

        for iteration in range(MAX_ITERATIONS):
            # Ilk iterasyonda arac ZORUNLU: model bos konusup cikmasin.
            tool_choice = "required" if iteration == 0 else "auto"
            try:
                response = self.llm_client.chat(
                    conversation, tools=schema, tool_choice=tool_choice,
                    profile=self.profile, timeout=PLAN_TIMEOUT_S)
            except LLMError as error:
                plan.iterations = iteration
                plan.stop_reason = "llm_error"
                plan.llm_error = str(error)
                return plan

            for key, value in (response.usage or {}).items():
                if isinstance(value, int):
                    plan.usage[key] = plan.usage.get(key, 0) + value

            if not response.tool_calls:
                # ILK turda hic arac cagrilmadiysa model soruyu kendi bilgisinden
                # cevaplamaya calisiyor demektir — uydurma riski. tool_choice
                # "required" her saglayicida UYGULANMIYOR (Ollama yok sayiyor),
                # bu yuzden garanti prompt seviyesinde bir kez daha zorlanir.
                if not plan.executed and not nudged:
                    nudged = True
                    conversation.append({
                        "role": "user",
                        "content": ("Once bir ARAC CAGIR. Duz yazi cevap yazma; "
                                    "hangi araci cagiracagina karar ver. Uygun "
                                    "ozel arac yoksa answer_general cagir."),
                    })
                    continue

                # Modelin "isim bitti" demesi bir ITERASYON DEGILDIR; sayac
                # yalnizca arac calistiran turlari sayar.
                plan.stop_reason = "done"
                return plan

            plan.iterations = iteration + 1

            # SAGLAYICI-BAGIMSIZ bicim: 'arguments' burada DICT olarak durur.
            # OpenAI/Groq JSON string bekler, Ollama nesne bekler; donusumu
            # LLMClient yapar (bkz. core/llm.py _wire_messages).
            assistant_message = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {"id": call.id, "type": "function",
                     "function": {"name": call.name, "arguments": call.arguments}}
                    for call in response.tool_calls
                ],
            }
            conversation.append(assistant_message)
            plan.messages.append(assistant_message)

            repeated = False
            for call in response.tool_calls:
                # Arac CALISMADAN once haber ver: planlama fazi uzun surdugunde
                # kullanici sessizce beklemesin, ne yapildigini gorsun.
                if on_tool is not None:
                    on_tool(call.name)
                record = self._execute(call, ctx, seen)
                plan.executed.append(record)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": record.result[:MAX_TOOL_RESULT_CHARS],
                }
                conversation.append(tool_message)
                plan.messages.append(tool_message)
                spec = tools.get(call.name)
                if spec is not None and spec.kind == TERMINAL:
                    plan.handoff_triggered = True
                if record.cached or record.suppressed:
                    repeated = True

            if plan.handoff_triggered:
                plan.stop_reason = "handoff"
                return plan
            if repeated and all(item.cached or item.suppressed
                                for item in plan.executed[-len(response.tool_calls):]):
                # Model ayni araci ayni argumanlarla tekrarliyor: ilerleme yok.
                plan.stop_reason = "loop_detected"
                return plan
            if deadline is not None and time.monotonic() > deadline:
                plan.stop_reason = "deadline"
                return plan

        plan.stop_reason = "max_iterations"
        return plan

    # ------------------------------------------------------------- execution

    def _execute(self, call, ctx: ToolContext, seen: Dict[str, str]) -> ExecutedTool:
        spec = tools.get(call.name)
        if spec is None:
            available = ", ".join(tools.tool_names())
            return ExecutedTool(
                name=call.name, args=call.arguments, error="unknown_tool",
                result=f"HATA: '{call.name}' diye bir arac yok. Mevcut araclar: {available}")

        args = tools.coerce_args(spec, call.arguments)
        key = f"{call.name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"

        # 1) TUR ICI tekrar: ayni arac + ayni argumanlar.
        if spec.once_per_turn and key in seen:
            if spec.kind != SIDE_EFFECT and spec.kind != TERMINAL:
                return ExecutedTool(name=call.name, args=args, cached=True,
                                    result=seen[key])
            return ExecutedTool(
                name=call.name, args=args, suppressed=True,
                result=("BU TURDA ZATEN CALISTIRILDI, tekrar edilmedi. "
                        f"Onceki sonuc: {seen[key]}"))

        # 2) GORUSME BOYU tek sefer (ekstre, devir): loop iki kez gondermesin.
        if spec.once_per_call:
            guard = f"_once_{call.name}"
            if ctx.user_profile.get(guard):
                return ExecutedTool(
                    name=call.name, args=args, suppressed=True,
                    result=("BU GORUSMEDE ZATEN CALISTIRILDI. Tekrar cagirma, "
                            "mevcut konuya devam et."))
            ctx.user_profile[guard] = True

        # 3) Yan etkili araclar icin tur basina tavan.
        if spec.kind == SIDE_EFFECT:
            count = ctx.user_profile.get("_side_effect_count", 0)
            if count >= MAX_SIDE_EFFECTS_PER_TURN:
                return ExecutedTool(
                    name=call.name, args=args, suppressed=True,
                    result=("Bu turda yeterince islem yapildi; baska yan etkili "
                            "arac cagirma, cevabini yaz."))
            ctx.user_profile["_side_effect_count"] = count + 1

        # 4) Calistir. Hata dongu ICINDE kalir: modele geri beslenir, cagri olmez.
        summary = self.run_tool(call.name, args, ctx)
        record = ExecutedTool(name=call.name, args=args, result=summary)
        if summary.startswith("HATA:"):
            record.error = summary
        else:
            seen[key] = summary
        return record


def trim_transcript(transcript: List[Dict[str, Any]],
                    max_turns: int = MAX_TRANSCRIPT_TURNS) -> List[Dict[str, Any]]:
    """Transkripti son `max_turns` kullanici turuna kirpar.

    Kirpma TUR sinirinda yapilir, mesaj sayisinda degil: bir 'tool' mesaji
    kendisini doguran 'assistant' mesajindan koparilirsa saglayici hata verir
    (yetim tool_call_id). Bu yuzden her zaman bir kullanici mesajindan baslanir.

    Mesaj ICERIGI kirpilmaz — modern baglam pencereleri buna elverisli ve
    kirpma tam da hatirlanmasi gereken rakami kesiyordu.
    """
    if max_turns <= 0:
        return list(transcript)

    user_indexes = [index for index, message in enumerate(transcript)
                    if message.get("role") == "user"]
    if len(user_indexes) <= max_turns:
        return list(transcript)
    return list(transcript[user_indexes[-max_turns]:])

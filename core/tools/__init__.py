"""Arac katmani: kayit defteri, calisma baglami ve alan araclari.

handlers modulu import edildiginde @tool dekoratorleri REGISTRY'yi doldurur;
bu yuzden asagidaki import yan etkisi ZORUNLUDUR.
"""

from core.tools.context import ToolContext
from core.tools.registry import (
    PURE,
    REGISTRY,
    SIDE_EFFECT,
    TERMINAL,
    ToolSpec,
    coerce_args,
    get,
    build_planner_system_prompt,
    build_router_system_prompt,
    openai_tools_schema,
    panel_tool_labels,
    tool_guide,
    tool,
    tool_names,
)
from core.tools import handlers as _handlers  # noqa: F401  (REGISTRY'yi doldurur)
from core.tools.handlers import mirror_args_to_card

__all__ = [
    "ToolContext", "ToolSpec", "REGISTRY", "PURE", "SIDE_EFFECT", "TERMINAL",
    "tool", "get", "tool_names", "coerce_args", "openai_tools_schema",
    "panel_tool_labels", "build_router_system_prompt",
    "build_planner_system_prompt", "tool_guide", "mirror_args_to_card",
]

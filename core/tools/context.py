"""Arac calisma baglami.

Handler'lar orchestrator'a degil bu nesneye bagimlidir; boylece agent loop
iceriden cagirabilir ve testlerde kolayca sahte veriyle kurulabilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolContext:
    repo: Any                       # MerchantRepository
    store: Any                      # AdminStore
    builder: Any                    # ResponseBuilder — panel/DB sozlesmesi
    merchant: Optional[Dict[str, Any]]
    user_id: str = "default_user"
    channel: str = "default"
    user_profile: Dict[str, Any] = field(default_factory=dict)
    config: Any = None

    @property
    def merchant_id(self) -> str:
        return (self.merchant or {}).get("merchant_id", "")

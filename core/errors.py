"""Cekirdek hata tipleri.

Eski model tum LLM hatalarini "Error ..." ile baslayan STRING olarak donduruyordu
(bkz. core/llm.is_llm_error). Agent loop'ta arac sonuclari da string oldugu icin
bu iki dunya cakisiyor: "Error" ile baslayan mesru bir arac ciktisi LLM hatasi
saniliyor. Bu yuzden yeni chat() katmani exception firlatir.

generate() geriye uyum icin exception'i eski string bicimine cevirmeye devam eder.
"""

from __future__ import annotations

from typing import Optional


class LLMError(RuntimeError):
    """LLM saglayicisina ulasilamadi veya saglayici hata dondurdu."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 retryable: bool = False, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.provider = provider


class ToolExecutionError(RuntimeError):
    """Bir arac handler'i calisirken patladi.

    Agent loop bunu YAKALAR ve modele tool result olarak geri besler; cagri olmez.
    """

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"{tool_name}: {message}")
        self.tool_name = tool_name

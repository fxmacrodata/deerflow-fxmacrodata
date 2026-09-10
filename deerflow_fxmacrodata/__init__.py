"""Native DeerFlow extension with complete public FXMacroData tool coverage."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from deerflow_extension_api import AgentScope, ExtensionRegistry, MiddlewarePlacement, Placement, extension

from .tools import FXMacroDataMiddleware


class _Contributor:
    def __init__(self, timeout: float):
        self.timeout = timeout

    def contribute_middlewares(self, app_store, ctx):
        return (MiddlewarePlacement(FXMacroDataMiddleware(self.timeout), Placement.STANDARD, AgentScope.BOTH),)


@extension(api="0.2.0", name="fxmacrodata")
def install(registry: ExtensionRegistry, config: Mapping[str, Any]) -> None:
    """Install tools into native lead and subagent middleware assembly."""
    if set(config) - {"timeout_seconds"}:
        raise ValueError("FXMacroData accepts only timeout_seconds in extension configuration; use process environment credentials.")
    try:
        timeout = float(config.get("timeout_seconds", 30))
        if isinstance(config.get("timeout_seconds"), bool) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError("FXMacroData timeout_seconds must be a finite number from 1 to 120.") from None
    registry.middlewares(_Contributor(timeout))


__all__ = ["install"]

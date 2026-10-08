"""Adapter registry. Adapters are discovered here and are independently replaceable."""

from __future__ import annotations

from functools import lru_cache

from .base import Adapter, AdapterError, AdapterInfo, ApplyResult, InspectResult, OperationSpec


@lru_cache(maxsize=1)
def _registry() -> dict[str, Adapter]:
    from .blender import BlenderAdapter
    from .codefiles import CodeAdapter
    from .layered2d import LayeredImageAdapter
    from .office.document import DocumentAdapter
    from .office.presentation import PresentationAdapter
    from .office.spreadsheet import SpreadsheetAdapter

    adapters: list[Adapter] = [BlenderAdapter(), LayeredImageAdapter(), CodeAdapter(),
                               SpreadsheetAdapter(), DocumentAdapter(), PresentationAdapter()]
    return {a.name: a for a in adapters}


def registry() -> dict[str, Adapter]:
    return _registry()


def get_adapter(name: str) -> Adapter:
    try:
        return _registry()[name]
    except KeyError:
        raise AdapterError(f"unknown adapter: {name}") from None


__all__ = ["Adapter", "AdapterError", "AdapterInfo", "ApplyResult", "InspectResult",
           "OperationSpec", "registry", "get_adapter"]

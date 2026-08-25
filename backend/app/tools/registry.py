from collections.abc import Callable
from typing import Any


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict[str, Any]] = []

    def register(self, name: str, schema: dict[str, Any]):
        def decorator(func: Callable):
            self._tools[name] = func
            self._schemas.append(schema)
            return func

        return decorator

    def get_tool(self, name: str) -> Callable | None:
        return self._tools.get(name)

    def get_schemas(self) -> list[dict[str, Any]]:
        return self._schemas


registry = ToolRegistry()

from typing import Callable, Dict, Any

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._schemas: list[Dict[str, Any]] = []

    def register(self, name: str, schema: Dict[str, Any]):
        def decorator(func: Callable):
            self._tools[name] = func
            self._schemas.append(schema)
            return func
        return decorator

    def get_tool(self, name: str) -> Callable:
        return self._tools.get(name)

    def get_schemas(self) -> list[Dict[str, Any]]:
        return self._schemas

registry = ToolRegistry()

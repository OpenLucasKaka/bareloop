from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any, get_args, get_origin

from bareloop.tools.models import ToolDefinition

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _infer_json_type(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    if isinstance(annotation, str):
        cleaned = annotation.strip().lower()
        if cleaned in ("int", "integer"):
            return {"type": "integer"}
        if cleaned in ("bool", "boolean"):
            return {"type": "boolean"}
        if cleaned in ("float", "number"):
            return {"type": "number"}
        if cleaned in ("str", "string"):
            return {"type": "string"}
        if cleaned.startswith("list") or cleaned.startswith("sequence"):
            return {"type": "array"}
        if cleaned.startswith("dict") or cleaned.startswith("mapping"):
            return {"type": "object"}

    origin = get_origin(annotation)
    if origin is not None:
        if issubclass(origin, Sequence) and not issubclass(origin, (str, bytes)):
            args = get_args(annotation)
            item_schema = _infer_json_type(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": item_schema}
        if issubclass(origin, Mapping):
            return {"type": "object"}
        if origin is list:
            args = get_args(annotation)
            item_schema = _infer_json_type(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": item_schema}
        if origin is dict:
            return {"type": "object"}

    if isinstance(annotation, type):
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is str:
            return {"type": "string"}
        if annotation in _TYPE_MAP:
            return {"type": _TYPE_MAP[annotation]}
        for base_type, json_type in _TYPE_MAP.items():
            if issubclass(annotation, base_type):
                return {"type": json_type}

    return {"type": "string"}


def _build_parameters_schema(
    func: Callable[..., Any],
    param_descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    import typing

    try:
        type_hints = typing.get_type_hints(func)
    except Exception:
        type_hints = {}

    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    descriptions = param_descriptions or {}

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        annotation = type_hints.get(name, param.annotation)
        schema = _infer_json_type(annotation)
        if name in descriptions:
            schema["description"] = descriptions[name]
        properties[name] = schema

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
    param_descriptions: dict[str, str] | None = None,
    allow_subagent: bool = False,
    supports_background: bool = False,
) -> Any:
    """Decorator to register a function as a BareLoop ToolDefinition.

    Usage:
        @tool
        def fetch_weather(city: str) -> str:
            '''Get the current weather.'''
            return f"Sunny in {city}"

        @tool(name="custom_calc", description="Calculate formula")
        def calc(formula: str) -> float:
            return eval(formula)
    """

    def decorator(fn: Callable[..., Any]) -> Any:
        tool_name = name or fn.__name__
        tool_desc = description or inspect.getdoc(fn) or f"Tool '{tool_name}'"
        params = (
            parameters
            if parameters is not None
            else _build_parameters_schema(fn, param_descriptions)
        )

        tool_def = ToolDefinition(
            name=tool_name,
            description=tool_desc,
            parameters=params,
            handler=fn,
            allow_subagent=allow_subagent,
            supports_background=supports_background,
        )
        fn.__tool_definition__ = tool_def
        return fn

    if func is not None:
        return decorator(func)
    return decorator


def hook(event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a function or method as a BareLoop lifecycle hook handler.

    Usage:
        @hook("PreToolUse")
        def audit_tool(block: dict) -> None:
            print("Running tool:", block["name"])
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.__hook_event__ = event_name
        return fn

    return decorator


class Plugin:
    """Base class for BareLoop plugins.

    Subclasses can bundle custom tools, lifecycle hooks, and initialization logic.
    """

    name: str = "base_plugin"
    description: str = ""

    def get_tools(self) -> list[ToolDefinition]:
        """Collect all tools declared on this plugin instance."""
        tools: list[ToolDefinition] = []
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(self, attr_name)
            except Exception:
                continue
            if hasattr(attr, "__tool_definition__"):
                defn = attr.__tool_definition__
                if isinstance(defn, ToolDefinition):
                    tools.append(defn)
        return tools

    def get_hooks(self) -> dict[str, list[Callable[..., Any]]]:
        """Collect all hook callbacks declared on this plugin instance."""
        hooks: dict[str, list[Callable[..., Any]]] = {}
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(self, attr_name)
            except Exception:
                continue
            if hasattr(attr, "__hook_event__"):
                event = attr.__hook_event__
                hooks.setdefault(event, []).append(attr)
        return hooks

    def setup(self, agent: Any) -> None:
        """Lifecycle hook called when the plugin is registered with an Agent."""
        pass


def discover_plugins(group: str = "bareloop.plugins") -> list[Plugin]:
    """Discover installed third-party plugins via setuptools / importlib entry points."""
    plugins: list[Plugin] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group=group)
        for ep in eps:
            loaded = ep.load()
            if isinstance(loaded, type) and issubclass(loaded, Plugin):
                plugins.append(loaded())
            elif isinstance(loaded, Plugin):
                plugins.append(loaded)
    except Exception:
        pass
    return plugins

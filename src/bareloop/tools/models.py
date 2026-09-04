from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ToolHandler = Callable[..., Any]

_JSON_SCHEMA_KEYWORDS = frozenset(
    {
        "$anchor",
        "$comment",
        "$defs",
        "$dynamicAnchor",
        "$dynamicRef",
        "$id",
        "$ref",
        "$schema",
        "$vocabulary",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "contains",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "default",
        "dependentRequired",
        "dependentSchemas",
        "deprecated",
        "description",
        "else",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxContains",
        "maximum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "minContains",
        "minimum",
        "minItems",
        "minLength",
        "minProperties",
        "multipleOf",
        "not",
        "oneOf",
        "pattern",
        "patternProperties",
        "prefixItems",
        "properties",
        "propertyNames",
        "readOnly",
        "required",
        "then",
        "title",
        "type",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
        "writeOnly",
    }
)
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "dependentSchemas", "patternProperties", "properties"}
)


def _check_json_schema_keywords(schema: Any, path: str = "$") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, Mapping):
        return

    unknown = schema.keys() - _JSON_SCHEMA_KEYWORDS
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"Invalid JSON Schema at {path}: unknown keyword(s): {fields}")

    for keyword in _SCHEMA_VALUE_KEYWORDS:
        if keyword in schema:
            _check_json_schema_keywords(schema[keyword], f"{path}.{keyword}")

    for keyword in _SCHEMA_ARRAY_KEYWORDS:
        for index, child in enumerate(schema.get(keyword, ())):
            _check_json_schema_keywords(child, f"{path}.{keyword}[{index}]")

    for keyword in _SCHEMA_MAP_KEYWORDS:
        for name, child in schema.get(keyword, {}).items():
            _check_json_schema_keywords(child, f"{path}.{keyword}.{name}")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler
    allow_subagent: bool = False
    supports_background: bool = False

    def __post_init__(self) -> None:
        schema = dict(self.parameters)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(
                f"Invalid JSON Schema for tool {self.name!r}: {error.message}"
            ) from error
        _check_json_schema_keywords(schema)

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }

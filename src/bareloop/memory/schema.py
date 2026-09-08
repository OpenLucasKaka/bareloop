"""OpenAI contracts for memory decisions and memory consolidation."""

MEMORY_TYPES = ["user", "feedback", "project", "reference"]
PERSISTENT_MEMORY_BASES = [
    "stable_user_preference",
    "explicit_user_constraint",
    "confirmed_feedback",
    "durable_project_decision",
    "verified_project_insight",
    "requested_reference",
]

_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "message_id": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["message_id", "quote"],
    "additionalProperties": False,
}

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["create", "update"]},
        "target_name": {
            "type": ["string", "null"],
            "description": "Exact existing memory name for update; null for create.",
        },
        "name": {"type": "string"},
        "type": {"type": "string", "enum": MEMORY_TYPES},
        "basis": {"type": "string", "enum": PERSISTENT_MEMORY_BASES},
        "description": {"type": "string"},
        "body": {"type": "string"},
        "evidence": {"type": "array", "minItems": 1, "items": _EVIDENCE_SCHEMA},
    },
    "required": [
        "operation",
        "target_name",
        "name",
        "type",
        "basis",
        "description",
        "body",
        "evidence",
    ],
    "additionalProperties": False,
}

MEMORY_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_memories",
        "description": "Return only durable, evidence-backed memory changes.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "maxItems": 3,
                    "items": _DECISION_SCHEMA,
                }
            },
            "required": ["decisions"],
            "additionalProperties": False,
        },
    },
}

MEMORY_CONSOLIDATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_consolidation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "maxItems": 30,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": MEMORY_TYPES},
                            "description": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["name", "type", "description", "body"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["memories"],
            "additionalProperties": False,
        },
    },
}

# Compatibility for the current consolidation caller; later pipeline wiring uses
# ``MEMORY_CONSOLIDATION_RESPONSE_FORMAT`` directly.
tool_schema = MEMORY_CONSOLIDATION_RESPONSE_FORMAT

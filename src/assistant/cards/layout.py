"""Validation schemas for the primitives and styling words drawn by the renderer."""

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

BINDING = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "map": {"type": "object"}},
    "required": ["path"],
    "additionalProperties": False,
}
TEXT = {"anyOf": [{"type": "string"}, BINDING]}
NUMBER = {"anyOf": [{"type": "number"}, BINDING]}
ARRAY = {"anyOf": [{"type": "array"}, BINDING]}
VALUE: dict[str, Any] = {}
REFERENCE = {"type": "string", "minLength": 1}
CHILDREN = {
    "anyOf": [
        {"type": "array", "items": REFERENCE},
        {
            "type": "object",
            "properties": {
                "componentId": REFERENCE,
                "path": {"type": "string"},
                "start": {"type": "integer", "minimum": 0},
            },
            "required": ["componentId", "path"],
            "additionalProperties": False,
        },
    ]
}
PROPERTIES: dict[str, dict[str, Any]] = {
    "Card": {"child": REFERENCE, "variant": {"enum": ["feature"]}},
    "Column": {"children": CHILDREN},
    "Row": {"children": CHILDREN},
    "List": {"children": CHILDREN, "variant": {"enum": ["ranked"]}},
    "Divider": {},
    "Text": {
        "text": TEXT,
        "variant": {
            "enum": [
                "h1",
                "h2",
                "h3",
                "h4",
                "body",
                "caption",
                "eyebrow",
                "quote",
                "pill",
                "badge",
                "code",
            ]
        },
        "format": {"enum": ["time", "ago", "datetime"]},
        "map": {"type": "object"},
    },
    "Metric": {
        "value": NUMBER,
        "unit": TEXT,
        "label": TEXT,
        "delta": NUMBER,
        "deltaPercent": NUMBER,
    },
    "Sparkline": {"values": ARRAY},
    "Link": {
        "child": REFERENCE,
        **{key: TEXT for key in ("task", "chat", "file", "folder", "url")},
    },
    "Figure": {"url": TEXT, "description": TEXT, "caption": TEXT},
    "WeatherGlyph": {"condition": TEXT, "temperature": NUMBER},
    "Diff": {"hunks": TEXT},
    "Icon": {"name": TEXT, "map": {"type": "object"}},
    "Image": {
        "url": TEXT,
        "description": TEXT,
        "fit": {"enum": ["contain", "cover", "fill", "none", "scaleDown"]},
        "variant": {
            "enum": ["icon", "avatar", "smallFeature", "mediumFeature", "largeFeature", "header"]
        },
    },
    "Video": {"url": TEXT, "posterUrl": TEXT},
    "Button": {
        "child": REFERENCE,
        "variant": {"enum": ["primary", "borderless"]},
        "action": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "properties": {"name": REFERENCE, "context": {"type": "object"}},
                    "required": ["name"],
                    "additionalProperties": False,
                }
            },
            "required": ["event"],
            "additionalProperties": False,
        },
    },
    "CheckBox": {"label": TEXT, "value": {"anyOf": [{"type": "boolean"}, BINDING]}},
    "TextField": {
        "label": TEXT,
        "value": VALUE,
        "placeholder": TEXT,
        "variant": {"enum": ["shortText", "longText", "number", "obscured"]},
    },
    "ChoicePicker": {
        "label": TEXT,
        "value": ARRAY,
        "options": ARRAY,
        "variant": {"enum": ["mutuallyExclusive", "multipleSelection"]},
        "displayStyle": {"enum": ["checkbox", "chips"]},
    },
    "Slider": {
        "label": TEXT,
        "value": NUMBER,
        "min": {"type": "number"},
        "max": {"type": "number"},
        "steps": {"type": "number", "exclusiveMinimum": 0},
    },
    "DateTimeInput": {
        "label": TEXT,
        "value": TEXT,
        "enableDate": {"type": "boolean"},
        "enableTime": {"type": "boolean"},
        "min": TEXT,
        "max": TEXT,
    },
    "Table": {
        **{key: BINDING for key in ("columns", "rows", "cells", "key")},
        **{key: VALUE for key in ("win", "pick")},
        **{key: REFERENCE for key in ("header", "lead", "cell")},
    },
}
REQUIRED = {
    "Card": ["child"],
    "Column": ["children"],
    "Row": ["children"],
    "List": ["children"],
    "Text": ["text"],
    "Button": ["child", "action"],
    "Icon": ["name"],
    "Image": ["url"],
    "Video": ["url"],
    "Link": ["child"],
    "Slider": ["max"],
    "ChoicePicker": ["options"],
}


def primitive_schema(kind: str) -> dict:
    """The supported properties of one renderer primitive."""
    properties = {
        "id": REFERENCE,
        "component": {"const": kind},
        "grow": {"type": "boolean"},
        "when": VALUE,
        "accessibility": {"type": "object"},
        **PROPERTIES[kind],
    }
    if kind in {"Column", "Row", "List", "Card", "Text", "Metric", "Icon", "Sparkline"}:
        properties["tone"] = {
            "anyOf": [{"enum": ["neutral", "muted", "accent", "positive", "negative"]}, BINDING]
        }
    if kind in {"Column", "Row", "List", "Card"}:
        properties["marker"] = TEXT
    if kind in {"Text", "Divider"}:
        properties["emphasis"] = {"enum": ["strong"]}
    if kind in {"Metric", "Sparkline", "Icon", "Figure", "WeatherGlyph"}:
        properties["size"] = {"enum": ["sm", "md", "lg"]}
    if kind in {"Column", "Row", "List"}:
        properties.update(
            {
                "gap": {"enum": ["none", "xs", "sm", "md", "lg"]},
                "align": {"enum": ["start", "center", "end", "stretch"]},
                "justify": {"enum": ["start", "center", "end", "between"]},
            }
        )
    if kind == "Metric":
        properties["align"] = {"enum": ["start", "end"]}
    return {
        "type": "object",
        "properties": properties,
        "required": ["id", "component", *REQUIRED.get(kind, [])],
        "additionalProperties": False,
    }


def validate_primitive(node: dict) -> None:
    """Reject properties the renderer cannot draw or interpret."""
    kind = node["component"]
    if kind not in PROPERTIES:
        return
    try:
        Draft202012Validator(primitive_schema(kind)).validate(node)
    except ValidationError as exc:
        raise ValueError(f"layout {node['id']!r}: {exc.message}") from exc

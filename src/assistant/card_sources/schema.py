"""Retained source configuration and bound instance parameters."""

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator

SOURCE_KEY = "_sources"
MAX_OUTPUT = 100_000
TIMEOUT = 30.0


class SourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal["get_weather", "get_quotes"] | None = None
    code: str | None = Field(default=None, max_length=60_000)
    files: dict[str, str] = Field(default_factory=dict)
    args: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: float | None = Field(default=None, ge=1, le=86400)
    secret_names: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def executable(self):
        if bool(self.tool) == bool(self.code):
            raise ValueError("Declare exactly one tool or code source")
        if self.tool and (self.files or self.secret_names):
            raise ValueError("Tool sources do not accept code files or Secrets")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in self.secret_names):
            raise ValueError("Secret names must be environment variable names")
        for name in self.files:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or any(p in {"", ".", ".."} for p in name.split("/"))
                or "\\" in name
                or "\x00" in name
                or name == "source.py"
            ):
                raise ValueError(
                    "Source files must be safe paths below scripts, excluding source.py"
                )
        if len(json.dumps(self.files).encode()) > 60_000:
            raise ValueError("Source files exceed the code size limit")
        return self


class CardSource(SourceDefinition):
    parameters: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any]
    required: list[str] = Field(default_factory=list)
    path: str = ""
    secrets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def target(self):
        if self.path and (not self.path.startswith("/_cards/") or self.path.count("/") != 2):
            raise ValueError("A source target must be the root or one nested Card")
        if not self.fields or set(self.fields) & {SOURCE_KEY, "_cards"}:
            raise ValueError("Source fields must be declared and cannot own source metadata")
        if set(self.required) - self.fields.keys():
            raise ValueError("Required source fields must be declared")
        if set(self.secrets) - set(self.secret_names):
            raise ValueError("Secret bindings must be declared by the source")
        contract = {
            "type": "object",
            "properties": self.fields,
            "required": self.required,
            "additionalProperties": False,
        }
        try:
            Draft202012Validator.check_schema(contract)
        except SchemaError as exc:
            raise ValueError(f"Invalid source field contract: {exc.message}") from exc
        pending = [self.fields]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef"} and (
                        not isinstance(item, str) or not item.startswith("#")
                    ):
                        raise ValueError("Source contracts may reference only local schemas")
                    pending.append(item)
            elif isinstance(value, list):
                pending.extend(value)
        bind_arguments(self.args, self.parameters)
        return self

    @property
    def code_version(self) -> str:
        return hashlib.sha256(
            json.dumps([self.code, self.files], sort_keys=True).encode()
        ).hexdigest()


def bind_arguments(value: Any, parameters: dict) -> Any:
    """Resolve explicit parameter references throughout JSON arguments."""
    if isinstance(value, dict):
        if set(value) == {"parameter"}:
            name = value["parameter"]
            if not isinstance(name, str) or name not in parameters:
                raise ValueError(f"Missing source parameter: {name}")
            return parameters[name]
        return {name: bind_arguments(item, parameters) for name, item in value.items()}
    if isinstance(value, list):
        return [bind_arguments(item, parameters) for item in value]
    return value


def sources_from(data: dict) -> dict[str, CardSource]:
    """Strictly decode retained metadata without inferring an executable source."""
    raw = data.get(SOURCE_KEY, {})
    if not isinstance(raw, dict) or len(raw) > 32:
        raise ValueError("Source metadata must contain at most 32 named sources")
    if any(not isinstance(name, str) or not name or len(name) > 200 for name in raw):
        raise ValueError("Invalid source identity")
    sources = {name: CardSource.model_validate(item) for name, item in raw.items()}
    paths = [source.path for source in sources.values()]
    if len(paths) != len(set(paths)) or ("" in paths and len(paths) > 1):
        raise ValueError("Sources must own independent data targets")
    return sources

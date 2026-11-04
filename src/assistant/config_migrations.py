"""One-time adoption of persisted provider settings."""

from pathlib import Path

import yaml

from assistant.observability import log_suppressed
from assistant.yamlio import write_yaml

_SAMPLING = frozenset({"temperature", "top_p", "top_k"})


def _without_sampling(value):
    if not isinstance(value, (dict, list)):
        return value
    options = dict(value)
    cleaned = {key: item for key, item in options.items() if key not in _SAMPLING}
    if isinstance(extra := options.get("extra_body"), dict):
        cleaned["extra_body"] = {key: item for key, item in extra.items() if key not in _SAMPLING}
    return cleaned


def sampling_marker(config_path: Path) -> Path:
    """The file recording that ``config_path`` has been adopted."""
    return config_path.parent / ".migrations" / "ag2-1.3-anthropic-sampling"


def migrate_anthropic_sampling(config_path: Path) -> None:
    """Adopt one config file, recording completion only after a successful read and write."""
    marker = sampling_marker(config_path)
    if marker.exists():
        return
    try:
        data = yaml.safe_load(config_path.read_text())
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("Config must be a YAML mapping")
        updated = dict(data)
        if isinstance(llm := data.get("llm"), dict):
            if (
                isinstance(providers := llm.get("provider_options"), dict)
                and "anthropic" in providers
            ):
                updated["llm"] = {
                    **llm,
                    "provider_options": {
                        **providers,
                        "anthropic": _without_sampling(providers["anthropic"]),
                    },
                }
        if isinstance(store := data.get("llm_configs"), dict):
            if isinstance(entries := store.get("configs"), list):
                updated["llm_configs"] = {
                    **store,
                    "configs": [
                        {
                            **entry,
                            **{
                                key: _without_sampling(entry[key])
                                for key in ("params", "options")
                                if key in entry
                            },
                        }
                        if isinstance(entry, dict) and entry.get("type") == "anthropic"
                        else entry
                        for entry in entries
                    ],
                }
    except FileNotFoundError:
        return
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        log_suppressed("reading config for Anthropic sampling migration", exc)
        return
    try:
        if updated != data:
            write_yaml(config_path, updated)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1\n")
    except OSError as exc:
        log_suppressed("writing config for Anthropic sampling migration", exc)

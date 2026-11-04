"""One-time adoption of persisted provider settings."""

from assistant.paths import Paths
from assistant.yamlio import read_yaml, write_yaml


def migrate_anthropic_sampling(paths: Paths) -> None:
    """Remove unsupported Anthropic sampling settings from Global and Profile configs."""
    marker = paths.root / ".migrations" / "ag2-1.3-anthropic-sampling"
    if marker.exists():
        return
    for path in [paths.config_yaml, *sorted((paths.root / "profiles").glob("*/config.yaml"))]:
        data = read_yaml(path)
        changed = False
        llm = data.get("llm")
        providers = llm.get("provider_options") if isinstance(llm, dict) else None
        options = providers.get("anthropic") if isinstance(providers, dict) else None
        containers = [options]
        store = data.get("llm_configs")
        entries = store.get("configs") if isinstance(store, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("type") == "anthropic":
                containers.extend([entry.get("params"), entry.get("options")])
        for container in containers:
            if not isinstance(container, dict):
                continue
            extra = container.get("extra_body")
            for values in [container, extra] if isinstance(extra, dict) else [container]:
                for key in ("temperature", "top_p", "top_k"):
                    if key in values:
                        del values[key]
                        changed = True
        if changed:
            write_yaml(path, data)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1\n")

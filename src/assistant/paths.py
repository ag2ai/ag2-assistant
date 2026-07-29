"""On-disk layout of an AG2 Assistant install.

``Paths.from_env`` is the only place in the package that reads the environment to
locate files; everything else takes an already-resolved ``Paths``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from assistant.config import read_yaml

_DATA_DIR_NAME = ".ag2assistant"


@dataclass(frozen=True)
class Paths:
    """Where an install keeps its state: the data root and the agent's workspace."""

    root: Path
    workspace: Path
    # ~/.codex/auth.json — the Codex CLI's own login file, outside root, hence a field.
    codex_auth: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str], home: Path) -> "Paths":
        """Resolve the layout from an environment mapping. AG2ASSISTANT_DATA_DIR has
        the highest precedence; otherwise a config.yaml in the boot root may move it."""
        workspace = (
            Path(env["AG2ASSISTANT_WORKSPACE"]).expanduser()
            if env.get("AG2ASSISTANT_WORKSPACE")
            else home / "Documents" / "AG2 Assistant"
        )
        codex = (
            Path(env["AG2ASSISTANT_CODEX_CLI_AUTH"]).expanduser()
            if env.get("AG2ASSISTANT_CODEX_CLI_AUTH")
            else home / ".codex" / "auth.json"
        )
        return cls(root=cls._root(env, home), workspace=workspace, codex_auth=codex)

    @staticmethod
    def _root(env: Mapping[str, str], home: Path) -> Path:
        """Env wins; otherwise a config.yaml in the boot root may set data_dir."""
        if v := env.get("AG2ASSISTANT_DATA_DIR"):
            return Path(v).expanduser()
        boot = home / _DATA_DIR_NAME
        if d := read_yaml(boot / "config.yaml").get("data_dir"):
            return Path(d).expanduser()
        return boot

    @property
    def config_yaml(self) -> Path:
        return self.root / "config.yaml"

    @property
    def secrets_json(self) -> Path:
        return self.root / "secrets.json"

    @property
    def profiles_json(self) -> Path:
        return self.root / "profiles.json"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def google_credentials(self) -> Path:
        return self.root / "google_credentials.json"

    @property
    def google_token(self) -> Path:
        return self.root / "google_token.json"

    @property
    def google_account(self) -> Path:
        return self.root / "google_account.txt"

    def profile_dir(self, pid: str) -> Path:
        """This profile's directory (does not create it)."""
        return self.root / "profiles" / pid

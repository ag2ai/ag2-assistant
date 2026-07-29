"""On-disk layout: Paths is the single source of every path."""

from pathlib import Path

from assistant.config import write_yaml
from assistant.paths import Paths


def test_default_root_is_dot_ag2assistant_under_home(tmp_path):
    paths = Paths.from_env({}, tmp_path)
    assert paths.root == tmp_path / ".ag2assistant"
    assert paths.workspace == tmp_path / "Documents" / "AG2 Assistant"


def test_data_dir_env_wins_over_home(tmp_path):
    paths = Paths.from_env({"AG2ASSISTANT_DATA_DIR": str(tmp_path / "state")}, tmp_path)
    assert paths.root == tmp_path / "state"


def test_config_yaml_can_relocate_the_root(tmp_path):
    boot = tmp_path / ".ag2assistant"
    write_yaml(boot / "config.yaml", {"data_dir": str(tmp_path / "elsewhere")})
    paths = Paths.from_env({}, tmp_path)
    assert paths.root == tmp_path / "elsewhere"


def test_data_dir_env_beats_the_config_yaml_relocation(tmp_path):
    boot = tmp_path / ".ag2assistant"
    write_yaml(boot / "config.yaml", {"data_dir": str(tmp_path / "elsewhere")})
    paths = Paths.from_env({"AG2ASSISTANT_DATA_DIR": str(tmp_path / "env")}, tmp_path)
    assert paths.root == tmp_path / "env"


def test_workspace_env_override(tmp_path):
    paths = Paths.from_env({"AG2ASSISTANT_WORKSPACE": str(tmp_path / "ws")}, tmp_path)
    assert paths.workspace == tmp_path / "ws"


def test_derived_paths_hang_off_the_root(tmp_path):
    paths = Paths(root=tmp_path / "r", workspace=tmp_path / "w", codex_auth=tmp_path / "codex.json")
    assert paths.config_yaml == tmp_path / "r" / "config.yaml"
    assert paths.secrets_json == tmp_path / "r" / "secrets.json"
    assert paths.profiles_json == tmp_path / "r" / "profiles.json"
    assert paths.skills_dir == tmp_path / "r" / "skills"
    assert paths.google_credentials == tmp_path / "r" / "google_credentials.json"
    assert paths.google_token == tmp_path / "r" / "google_token.json"
    assert paths.google_account == tmp_path / "r" / "google_account.txt"
    assert paths.codex_tokens == tmp_path / "r" / "codex_auth.json"
    assert paths.profile_dir("work") == tmp_path / "r" / "profiles" / "work"


def test_our_codex_token_store_is_not_the_codex_cli_login_file(tmp_path):
    """Two different files: our subscription tokens under root, the CLI's own login
    wherever the CLI keeps it."""
    paths = Paths.from_env({}, tmp_path)
    assert paths.codex_tokens == tmp_path / ".ag2assistant" / "codex_auth.json"
    assert paths.codex_auth == tmp_path / ".codex" / "auth.json"
    assert paths.codex_tokens != paths.codex_auth


def test_codex_auth_defaults_under_home_but_env_overrides(tmp_path):
    paths = Paths.from_env({}, tmp_path)
    assert paths.codex_auth == tmp_path / ".codex" / "auth.json"
    other = Paths.from_env({"AG2ASSISTANT_CODEX_CLI_AUTH": str(tmp_path / "a.json")}, tmp_path)
    assert other.codex_auth == tmp_path / "a.json"


def test_paths_is_frozen(tmp_path):
    paths = Paths(root=tmp_path, workspace=tmp_path, codex_auth=tmp_path / "c.json")
    try:
        paths.root = tmp_path / "x"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("Paths must be immutable")


def test_from_env_expands_a_tilde_in_every_override(tmp_path):
    env = {
        "AG2ASSISTANT_DATA_DIR": "~/state",
        "AG2ASSISTANT_WORKSPACE": "~/ws",
        "AG2ASSISTANT_CODEX_CLI_AUTH": "~/auth.json",
    }
    paths = Paths.from_env(env, tmp_path)
    home = Path.home()
    assert paths.root == home / "state"
    assert paths.workspace == home / "ws"
    assert paths.codex_auth == home / "auth.json"

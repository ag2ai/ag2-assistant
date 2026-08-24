"""Checks that the repository's CI configuration agrees with itself."""

import json
import re
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github/workflows"


def _pyproject():
    return tomllib.loads((_ROOT / "pyproject.toml").read_text())


def _workflow(name):
    return yaml.safe_load((_WORKFLOWS / name).read_text())


def _setup_uv_versions(workflow):
    """Every version pinned on an astral-sh/setup-uv step in one parsed workflow."""
    return {
        step["with"]["version"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "astral-sh/setup-uv" in step.get("uses", "")
    }


def test_ruff_pin_agrees_across_ci_pre_commit_and_pyproject():
    ci_version = _workflow("ci.yml")["jobs"]["lint"]["env"]["RUFF_VERSION"]

    pre_commit = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text())
    ruff_repos = [r for r in pre_commit["repos"] if "ruff-pre-commit" in r.get("repo", "")]
    assert len(ruff_repos) == 1, "expected exactly one ruff-pre-commit repo entry"
    hook_version = ruff_repos[0]["rev"].lstrip("v")

    dev_extra = _pyproject()["project"]["optional-dependencies"]["dev"]
    ruff_entries = [d for d in dev_extra if re.match(r"^ruff\b", d)]
    assert len(ruff_entries) == 1, "expected exactly one ruff entry in the dev extra"
    lower_bound = re.search(r">=\s*([0-9][0-9.]*)", ruff_entries[0])
    assert lower_bound, f"no >= lower bound in {ruff_entries[0]!r}"

    assert ci_version == hook_version, (
        f"CI pins ruff {ci_version}, .pre-commit-config.yaml pins {hook_version}"
    )
    assert ci_version == lower_bound.group(1), (
        f"CI pins ruff {ci_version}, the dev extra floors it at {lower_bound.group(1)}"
    )


def test_uv_pin_agrees_across_every_workflow_and_the_dockerfile():
    pinned = {}
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        versions = _setup_uv_versions(_workflow(workflow.name))
        if versions:
            pinned[workflow.name] = versions
    assert pinned, "no setup-uv step found in any workflow"

    dockerfile = (_ROOT / "Dockerfile").read_text()
    image_pins = set(re.findall(r"ghcr\.io/astral-sh/uv:([0-9][0-9.]*)", dockerfile))
    assert len(image_pins) == 1, f"expected one uv image pin in the Dockerfile, got {image_pins}"

    declared = set(image_pins)
    for versions in pinned.values():
        declared |= versions
    assert len(declared) == 1, (
        f"uv is pinned to more than one version: Dockerfile {sorted(image_pins)}, "
        + ", ".join(f"{name} {sorted(v)}" for name, v in pinned.items())
    )


def test_test_matrix_covers_the_published_python_floor():
    requires_python = _pyproject()["project"]["requires-python"]
    floor = re.search(r">=\s*([0-9]+\.[0-9]+)", requires_python)
    assert floor, f"no >= floor in requires-python {requires_python!r}"

    matrix = _workflow("ci.yml")["jobs"]["test"]["strategy"]["matrix"]["python-version"]

    assert floor.group(1) in matrix, (
        f"pyproject.toml publishes {requires_python!r} but the CI test matrix "
        f"{matrix} never runs {floor.group(1)}"
    )


def test_coverage_leg_is_declared_and_is_the_newest_in_the_matrix():
    test_job = _workflow("ci.yml")["jobs"]["test"]
    matrix = test_job["strategy"]["matrix"]["python-version"]
    coverage_leg = test_job["env"]["COVERAGE_PYTHON"]

    assert coverage_leg in matrix, (
        f"coverage is configured for {coverage_leg}, which is not a matrix leg: {matrix}"
    )
    newest = max(matrix, key=lambda v: tuple(int(part) for part in v.split(".")))
    assert coverage_leg == newest, f"coverage runs on {coverage_leg}, newest leg is {newest}"


def _discovered_manifests():
    """Every dependency manifest in the tree, as (ecosystem, dependabot directory)."""
    ecosystems = {"uv.lock": "uv", "package-lock.json": "npm"}
    skip = {".venv", "node_modules", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

    found = set()
    for filename, ecosystem in ecosystems.items():
        for path in _ROOT.rglob(filename):
            if skip & set(path.relative_to(_ROOT).parts):
                continue
            directory = path.parent.relative_to(_ROOT).as_posix()
            found.add((ecosystem, "/" if directory == "." else f"/{directory}"))
    return found


def test_every_dependency_manifest_has_a_dependabot_ecosystem():
    config = yaml.safe_load((_ROOT / ".github/dependabot.yml").read_text())
    configured = {(entry["package-ecosystem"], entry["directory"]) for entry in config["updates"]}

    discovered = _discovered_manifests()
    assert discovered, "no dependency manifest found — the discovery globs are wrong"

    unwatched = discovered - configured
    assert not unwatched, (
        f"manifests exist with no .github/dependabot.yml entry: {sorted(unwatched)}"
    )


def test_dependabot_groups_declare_what_they_apply_to():
    config = yaml.safe_load((_ROOT / ".github/dependabot.yml").read_text())

    for entry in config["updates"]:
        for name, group in entry.get("groups", {}).items():
            assert "applies-to" in group, (
                f"group {name!r} omits applies-to, so it silently covers version updates "
                f"only and will not group security updates"
            )


def test_the_web_manifest_dependabot_watches_is_the_one_the_bundle_is_built_from():
    """The npm entry must point at the directory whose lockfile CI installs from."""
    config = yaml.safe_load((_ROOT / ".github/dependabot.yml").read_text())
    npm_dirs = {e["directory"] for e in config["updates"] if e["package-ecosystem"] == "npm"}

    ci = _workflow("ci.yml")
    cache_paths = {
        step["with"]["cache-dependency-path"]
        for job in ci["jobs"].values()
        for step in job.get("steps", [])
        if "actions/setup-node" in step.get("uses", "")
        and "cache-dependency-path" in step.get("with", {})
    }
    assert cache_paths, "no setup-node step pins a cache-dependency-path"

    for path in cache_paths:
        directory = "/" + str(Path(path).parent)
        assert directory in npm_dirs, (
            f"CI installs from {path} but dependabot watches npm in {sorted(npm_dirs)}"
        )
    assert json.loads((_ROOT / "web/package.json").read_text())["name"]

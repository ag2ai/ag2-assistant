"""Packaging checks for files needed by an installed checkout."""

from pathlib import Path


def test_gateway_static_app_bundle_is_in_package_data():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    package_data = config["tool"]["setuptools"]["package-data"]["assistant.gateway"]

    assert "static/app/index.html" in package_data
    assert "static/app/assets/*" in package_data
    assert "static/app/assets/*.css" in package_data
    assert "static/app/assets/*.js" in package_data

    assert (root / "src/assistant/gateway/static/app/index.html").is_file()
    assert list((root / "src/assistant/gateway/static/app/assets").glob("*.css"))
    assert list((root / "src/assistant/gateway/static/app/assets").glob("*.js"))

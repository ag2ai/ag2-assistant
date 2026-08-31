# Contributing to AG2 Assistant

Thanks for your interest in improving AG2 Assistant! This page is the quick
front door; the full development guidelines live in
[AGENTS.md](AGENTS.md).

## Getting set up

```bash
git clone https://github.com/ag2ai/ag2-assistant.git
cd ag2-assistant
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Before you open a pull request

Run the same checks CI runs. They are advisory — the default branch has no ruleset,
so a red pull request is reported, not blocked:

```bash
ruff check .
ruff format --check .
pytest -m "not integration" -q
```

If you changed anything under `web/`, rebuild and commit the SPA bundle:

```bash
npm --prefix web run build   # regenerates src/assistant/gateway/static/app/
```

See [AGENTS.md](AGENTS.md) for code style, repository layout, the committed-bundle
rule, and testing notes.

## Opening the pull request

- Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md) — explain
  *why* the change is needed and what you did to validate it.
- Keep PRs focused; smaller changes are reviewed faster.
- We welcome AI-assisted contributions, but you remain responsible for what you
  submit. Please read [`.github/AI_POLICY.md`](.github/AI_POLICY.md).

## Reporting bugs and requesting features

Use the [issue templates](https://github.com/ag2ai/ag2-assistant/issues/new/choose).
A minimal reproducible example is the fastest path to a fix.

## Security

Please **do not** report security vulnerabilities in public issues. See
[`.github/SECURITY.md`](.github/SECURITY.md) for how to report them privately.

## License

By contributing, you agree that your contributions will be licensed under the
project's [Apache 2.0](LICENSE) license.

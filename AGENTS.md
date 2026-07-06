# AG2 Assistant

An open-source personal AI assistant built with [AG2](https://github.com/ag2ai/ag2)'s Beta framework (the `ag2` package), in Python.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -m "not integration"   # unit tests (no API key needed)
```

See [README.md](README.md) for usage and [docs/architecture.md](docs/architecture.md) for the system design.

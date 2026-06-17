"""User-adjustable settings persisted to ``~/.agclaw/settings.json``.

Currently just the realtime voice. Kept separate from `config` (which is
env/file/defaults, read-only at runtime) because these are toggled live from the
GUI / tools and must persist across restarts.
"""

import json

from agclaw.config import load_config

# Gemini speech voices (name → short style), per
# https://ai.google.dev/gemini-api/docs/speech-generation#voices
VOICES: dict[str, str] = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm",
    "Fenrir": "Excitable", "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy",
    "Callirrhoe": "Easy-going", "Autonoe": "Bright", "Enceladus": "Breathy",
    "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth",
    "Despina": "Smooth", "Erinome": "Clear", "Algenib": "Gravelly",
    "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Achernar": "Soft",
    "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature",
    "Pulcherrima": "Forward", "Achird": "Friendly", "Zubenelgenubi": "Casual",
    "Vindemiatrix": "Gentle", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}
DEFAULT_VOICE = "Puck"


def _path():
    return load_config().data_dir / "settings.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def get_voice() -> str:
    v = _read().get("voice")
    return v if v in VOICES else DEFAULT_VOICE


def set_voice(name: str) -> bool:
    """Persist the realtime voice. Returns False for an unknown voice."""
    if name not in VOICES:
        return False
    data = _read()
    data["voice"] = name
    _write(data)
    return True

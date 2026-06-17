"""Record a spoken sample for each Gemini voice → static/voices/<name>.wav.

A helper so the voice picker plays previews instantly from disk instead of
calling TTS live on every click. Re-run after changing the sample text or the
voice list. The output dir is gitignored (regenerate locally as needed); the
picker falls back to live /api/voice/preview when a file is absent.

    python scripts/record_voice_samples.py [VoiceName ...]   # all, or just the named ones
"""

import asyncio
import sys
from pathlib import Path

from agclaw.config import load_config
from agclaw.settings import VOICES
from agclaw.voice import synthesize_preview

OUT = Path(__file__).resolve().parents[1] / "src/agclaw/gateway/static/voices"


async def main() -> None:
    names = [a for a in sys.argv[1:] if a in VOICES] or list(VOICES)
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Recording {len(names)} voice(s) → {OUT}")
    ok = 0
    for i, name in enumerate(names, 1):
        try:
            wav = await synthesize_preview(cfg, name)
            (OUT / f"{name}.wav").write_bytes(wav)
            print(f"[{i}/{len(names)}] {name} — {VOICES[name]} ✓ ({len(wav):,} bytes)")
            ok += 1
        except Exception as exc:  # keep going; one bad voice shouldn't stop the rest
            print(f"[{i}/{len(names)}] {name} ✗ {exc}")
    print(f"Done: {ok}/{len(names)} recorded.")


if __name__ == "__main__":
    asyncio.run(main())

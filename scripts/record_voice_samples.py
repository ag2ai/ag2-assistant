"""Record a spoken sample for each voice → static/voices/<name>.wav.

A helper so the voice picker plays previews instantly from disk instead of
calling TTS live on every click. Re-run after changing the sample text or the
voice lists. The output dir is gitignored (regenerate locally as needed); the
picker falls back to live /api/voice/preview when a file is absent.

    python scripts/record_voice_samples.py                       # both providers, all voices
    python scripts/record_voice_samples.py --provider openai     # one provider
    python scripts/record_voice_samples.py --provider gemini Puck Kore   # named voices

Gemini and OpenAI voice names don't collide, so both sets live side by side.
Needs the matching key (GEMINI_API_KEY / OPENAI_API_KEY) for the provider(s) run.
"""

import asyncio
import sys
from pathlib import Path

from agclaw.config import load_config
from agclaw.settings import voices_for
from agclaw.voice import synthesize_preview
from agclaw.voice_providers import names as provider_names

OUT = Path(__file__).resolve().parents[1] / "src/agclaw/gateway/static/voices"


async def _record(cfg, provider: str, names: list[str]) -> tuple[int, int]:
    catalogue = voices_for(provider)
    names = [n for n in names if n in catalogue] or list(catalogue)
    print(f"[{provider}] recording {len(names)} voice(s) → {OUT}")
    ok = 0
    for i, name in enumerate(names, 1):
        try:
            wav = await synthesize_preview(cfg, name, provider=provider)
            (OUT / f"{name}.wav").write_bytes(wav)
            print(f"  [{i}/{len(names)}] {name} — {catalogue[name]} ✓ ({len(wav):,} bytes)")
            ok += 1
        except Exception as exc:  # one bad/unsupported voice shouldn't stop the rest
            print(f"  [{i}/{len(names)}] {name} ✗ {exc}")
    return ok, len(names)


async def main() -> None:
    args = sys.argv[1:]
    provider = "all"
    if "--provider" in args:
        idx = args.index("--provider")
        provider = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    providers = list(provider_names()) if provider == "all" else [provider]

    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    total_ok = total = 0
    for p in providers:
        ok, n = await _record(cfg, p, args)
        total_ok += ok
        total += n
    print(f"Done: {total_ok}/{total} recorded.")


if __name__ == "__main__":
    asyncio.run(main())

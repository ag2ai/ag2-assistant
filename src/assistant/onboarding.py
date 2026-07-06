"""First-run onboarding — a short, skippable interview that seeds the UNIVERSAL
"who the user is" memory.

When AG2 Assistant has no universal profile yet, it asks a handful of questions
(name, location, working hours, preferred answer style) through the same pluggable
HITL `Asker` used everywhere else — so it works identically on the desktop popup or
in Telegram/Discord/Slack. Its answers are identity facts (true in any persona), so
they seed the shared universal store (``root_dir/user.db``) plus the
`AG2ASSISTANT_LOCATION` config — not a per-profile store. The gate is therefore
install-wide: the interview runs ONCE (the first chat in whichever profile), not
once per profile, since every profile reads the same universal document.

Every question is skippable (type "skip", or pick "No preference").
"""

from dataclasses import dataclass
from pathlib import Path

from assistant.hitl.base import Asker, Question
from assistant.memory import PROFILE_PATH, build_profile_store, read_profile

_SKIP_WORDS = {"", "skip", "-", "—", "none", "n/a", "na", "no preference", "pass"}

_STYLE_MAP = {
    "Short & direct": "Prefers short, direct answers.",
    "Detailed & thorough": "Prefers detailed, thorough answers.",
    "Casual & friendly": "Prefers a casual, friendly tone.",
}


@dataclass
class OnboardingStep:
    key: str
    question: Question


STEPS: list[OnboardingStep] = [
    OnboardingStep(
        "name",
        Question(
            text="What should I call you?",
            detail="Your name or nickname. Type 'skip' to skip.",
        ),
    ),
    OnboardingStep(
        "location",
        Question(
            text="Where are you based?",
            detail="City and country — so I can reason about your time and local "
            "context (e.g. the weather). Type 'skip' to skip.",
        ),
    ),
    OnboardingStep(
        "hours",
        Question(
            text="When are your usual working hours?",
            detail="e.g. '9am–6pm, Mon–Fri'. Type 'skip' to skip.",
        ),
    ),
    OnboardingStep(
        "style",
        Question(
            text="How do you like your answers?",
            options=[
                "Short & direct",
                "Detailed & thorough",
                "Casual & friendly",
                "No preference",
            ],
        ),
    ),
]


def _skipped(answer: str | None) -> bool:
    return answer is None or answer.strip().lower() in _SKIP_WORDS


async def needs_onboarding(user_store_path: Path) -> bool:
    """True if the install has no universal profile yet (its `user.db` is empty).

    Install-wide: the interview seeds the shared "who the user is" memory, so it
    runs once (the first chat in whichever profile) — every profile reads the same
    universal document. There is no marker file; the universal store being empty is
    the only gate. `user_store_path` is ``config.root_dir / "user.db"``."""
    profile = await read_profile(user_store_path)
    return not profile.strip()


def _persist_location(location: str, env_path: Path | None = None) -> None:
    """Write AG2ASSISTANT_LOCATION into the process env and the project `.env`."""
    import os

    os.environ["AG2ASSISTANT_LOCATION"] = location
    env_path = env_path or Path(".env")
    line = f"AG2ASSISTANT_LOCATION={location}"
    try:
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            for i, existing in enumerate(lines):
                if existing.startswith("AG2ASSISTANT_LOCATION="):
                    lines[i] = line
                    break
            else:
                lines.append(line)
            env_path.write_text("\n".join(lines) + "\n")
        else:
            env_path.write_text(line + "\n")
    except OSError:
        pass  # env file is best-effort; the in-process var is what matters now


def identity_document(answers: dict[str, str]) -> str:
    """Render collected identity answers as the "# User profile" markdown, or '' if
    every field is empty/skipped.

    The single source of truth for the seeded doc's format, shared by the CLI
    interview (`run_onboarding`) and the web onboarding (`POST /api/identity`), so
    both produce identical documents for the same answers. `style` accepts either
    one of the interview's canned options (mapped via `_STYLE_MAP`) or free text
    (rendered verbatim as an answer-style preference), so the web fields can carry a
    plain phrase like "short and direct".
    """
    name = (answers.get("name") or "").strip()
    location = (answers.get("location") or "").strip()
    hours = (answers.get("hours") or "").strip()
    style = (answers.get("style") or "").strip()

    about, how, when = [], [], []
    if name:
        about.append(f"- Name: {name}")
    if location:
        about.append(f"- Location: {location}")
    if hours:
        when.append(f"- Usual working hours: {hours}")
    if style:
        how.append(f"- {_STYLE_MAP.get(style, f'Prefers answers that are {style}.')}")

    sections: list[str] = []
    if about:
        sections.append("## About the user\n" + "\n".join(about))
    if how:
        sections.append("## How they like things done\n" + "\n".join(how))
    if when:
        sections.append("## When they like things done\n" + "\n".join(when))

    if not sections:
        return ""
    return "# User profile\n_Seeded from onboarding._\n\n" + "\n\n".join(sections) + "\n"


async def run_onboarding(
    asker: Asker,
    user_store_path: Path,
    env_path: Path | None = None,
) -> dict[str, str]:
    """Ask the onboarding questions and seed the UNIVERSAL profile + location config.

    Returns the (non-skipped) answers. Writes to the shared universal store
    (``config.root_dir / "user.db"``, passed as `user_store_path`) since the answers
    are identity facts true across every persona; there is no marker file — an empty
    `user.db` is the only gate (`needs_onboarding`).
    """
    answers: dict[str, str] = {}
    for step in STEPS:
        try:
            ans = await asker.ask(step.question)
        except Exception:
            ans = ""  # a failed/cancelled prompt counts as a skip — keep going
        if not _skipped(ans):
            answers[step.key] = ans.strip()

    if answers.get("location"):
        _persist_location(answers["location"], env_path)

    profile_md = identity_document(answers)
    if profile_md:
        store = build_profile_store(user_store_path)
        existing = ""
        if await store.exists(PROFILE_PATH):
            existing = (await store.read(PROFILE_PATH)).strip()
        # Don't clobber an existing universal doc; append seeded facts above it.
        merged = profile_md if not existing else profile_md + "\n" + existing
        await store.write(PROFILE_PATH, merged)

    return answers

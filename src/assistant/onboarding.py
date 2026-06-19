"""First-run onboarding — a short, skippable interview that seeds the profile.

When AG2 Assistant has no learned profile yet, it asks a handful of questions (name,
location, working hours, preferred answer style) through the same pluggable HITL
`Asker` used everywhere else — so it works identically on the desktop popup or in
Telegram/Discord/Slack. The answers seed the persistent profile and the
`AG2ASSISTANT_LOCATION` config, so the agent starts out knowing the basics instead of
having to learn them slowly.

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


def marker_path() -> Path:
    """File whose existence means onboarding has already been offered."""
    return Path.home() / ".ag2assistant" / "onboarded"


def _skipped(answer: str | None) -> bool:
    return answer is None or answer.strip().lower() in _SKIP_WORDS


async def needs_onboarding(store_path: Path | None = None) -> bool:
    """True if the user hasn't been onboarded and has no learned profile yet."""
    if marker_path().exists():
        return False
    profile = await read_profile(store_path)
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


def build_profile(answers: dict[str, str]) -> str:
    """Render collected answers as a profile markdown document, or '' if empty."""
    name = answers.get("name")
    location = answers.get("location")
    hours = answers.get("hours")
    style = answers.get("style")

    about, how, when = [], [], []
    if name:
        about.append(f"- Name: {name}")
    if location:
        about.append(f"- Location: {location}")
    if hours:
        when.append(f"- Usual working hours: {hours}")
    if style and style in _STYLE_MAP:
        how.append(f"- {_STYLE_MAP[style]}")

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
    store_path: Path | None = None,
    env_path: Path | None = None,
    mark: bool = True,
) -> dict[str, str]:
    """Ask the onboarding questions and seed the profile + location config.

    Returns the (non-skipped) answers. Always writes the onboarding marker (when
    `mark`) so we don't ask again, even if everything was skipped.
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

    profile_md = build_profile(answers)
    if profile_md:
        store = build_profile_store(store_path)
        existing = ""
        if await store.exists(PROFILE_PATH):
            existing = (await store.read(PROFILE_PATH)).strip()
        # Don't clobber a real learned profile; append seeded facts above it.
        merged = profile_md if not existing else profile_md + "\n" + existing
        await store.write(PROFILE_PATH, merged)

    if mark:
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text("done\n")

    return answers

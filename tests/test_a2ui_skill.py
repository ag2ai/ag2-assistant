"""The Card catalog disclosed through a Skill: one resident line, an index read on
demand, and one Card's schema and example fetched only once it is being drawn."""

import pytest
from ag2.exceptions import SkillNotFoundError

from assistant.a2ui import CARD_VOCABULARY, bundled_cards
from assistant.a2ui_skill import (
    A2UI_SKILL,
    PROTOCOL_RESOURCE,
    SKILL_DESCRIPTION_LIMIT,
    a2ui_available,
)
from assistant.agent import (
    BEHAVIOR_GUIDANCE,
    build_skills_plugin,
    build_skills_runtime,
    resolve_a2ui_skill,
)
from assistant.cards import CARDS_DIR, CardStateStore
from assistant.coding.diff import FileDiff
from assistant.coding.surface import build_surface, card_fields
from assistant.config import Config
from assistant.events import A2UISurface
from assistant.gateway.core import Gateway
from assistant.gateway.stream_bridge import StreamBridge
from assistant.profiles import ProfileRegistry
from assistant.skills import SkillStateStore
from assistant.state_store import SUPPRESS_SHARED
from tests.support.cards import a2ui_view, card_detail, protocol, skill_body, write_card
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory


def build_skills_runtime_with_a2ui(config) -> list:
    """The A2UI Skill's catalog entries, as the plugin describes them to the agent."""
    return resolve_a2ui_skill(config).skills


def _resident(config) -> str:
    """The text every turn carries: the skills catalog the plugin injects."""
    plugin = build_skills_plugin(config, build_skills_runtime(config))
    return "\n".join(plugin._system_prompt)


class _TurnAgent(FakeRunMixin):
    """A fake agent that keeps what each of its turns was built with — the prompt it
    was handed and the middleware that would run over its answer."""

    def __init__(self):
        self.tools = []
        self.prompts: list = []
        self.middleware: list = []

    async def ask(self, *msg, stream=None, prompt=None, middleware=(), **kwargs) -> FakeReply:
        self.prompts.append(prompt or [])
        self.middleware.append(tuple(middleware))
        return FakeReply("done")


class _ClientSocket:
    """A client's WebSocket: keeps the frames the bridge sent it."""

    def __init__(self):
        self.sent: list = []

    async def send_json(self, frame) -> None:
        self.sent.append(frame)


async def _started(config, agent) -> Gateway:
    """A started Gateway whose every turn runs on ``agent``."""
    gateway = Gateway(config=config, agent_factory=fake_agent_factory(agent))
    await gateway.start()
    return gateway


def _profile(paths, name: str) -> Config:
    """A profile on ``paths``, its directory made, ready for a Gateway of its own."""
    meta = ProfileRegistry(paths).create_profile(name, "#109e91")
    paths.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    return Config.for_paths(paths).with_profile(meta)


def _turn_middleware(agent) -> tuple:
    """What the agent's last turn was built to run over the model's answer — the only
    place a rich view is parsed, validated or recovered."""
    return agent.middleware[-1]


# --- the resident line -------------------------------------------------------


def test_the_agent_is_told_about_rich_views_by_one_line(config):
    """The whole catalog is gone from the always-present text: one skill entry says
    rich views exist and when to reach for one, and nothing names a Card."""
    resident = _resident(config)

    assert A2UI_SKILL in resident
    for name in bundled_cards():
        assert name not in resident
    assert "createSurface" not in resident
    assert "A2UI Message Schema" not in resident
    assert "**Custom components:**" not in resident


def test_the_resident_line_names_every_case_a_card_is_ready_for(config):
    """The agent is told, on every turn, which questions already have a view."""
    resident = _resident(config)

    for card in bundled_cards().values():
        if card.topic:
            assert card.topic in resident
    assert "the weather" in resident
    assert "never ask" in resident.lower()


def test_a_card_with_no_topic_is_not_advertised_but_is_still_drawable(config):
    """The server-filled coding session names no case: the model is not invited to
    draw it, though it stays in the index a loaded Skill reads."""
    card = bundled_cards()["CodingSession"]

    assert card.topic == ""
    assert "coding agent" not in _resident(config)


def test_the_resident_line_follows_the_cards_this_profile_has(config, paths):
    write_card(paths.root / CARDS_DIR, "Shelf", topic="what is on a shelf")
    assert "what is on a shelf" in _resident(config)

    CardStateStore(paths.root).set_enabled("Shelf", False)
    assert "what is on a shelf" not in _resident(config)


def test_the_resident_line_stays_within_the_skill_description_limit(config):
    for i in range(80):
        write_card(config.workspace_dir / CARDS_DIR, f"Shelf{i}", topic=f"shelf number {i:02d}")

    described = [s for s in build_skills_runtime_with_a2ui(config) if s.name == A2UI_SKILL]
    assert len(described[0].metadata.description) <= SKILL_DESCRIPTION_LIMIT


# --- the body ----------------------------------------------------------------


async def test_the_body_lists_exactly_the_available_cards(config):
    """The index is name + description, one line per Card the profile can draw."""
    body = await skill_body(config)

    for name, card in bundled_cards().items():
        assert f"- **{name}**: {card.description}" in body
    assert 'read_skill_resource(name="rich-views", resource="cards/<CardName>.md")' in body


async def test_the_body_says_how_to_draw_a_card_and_nothing_of_the_protocol(config):
    """The body is about the Cards; the protocol at large is a reference, read only
    to compose views or to draw one from the basic components."""
    body = await skill_body(config)

    assert "<a2ui-json>" in body
    assert "do not restate" in body
    assert PROTOCOL_RESOURCE in body
    assert "## A2UI Message Types" not in body
    assert "callFunction" not in body
    assert "ChoicePicker" not in body


async def test_the_protocol_reference_carries_composition_and_the_basic_components(config):
    reference = await protocol(config)

    assert "## A2UI Message Types" in reference
    assert 'root component="Column"' in reference
    assert "ChoicePicker" in reference
    assert "save_surface" in reference


async def test_the_body_carries_no_cards_worked_example(config):
    """The part that grows with the catalog is deferred: the body has the rules and
    the index, never a Card's own schema or example."""
    body = await skill_body(config)

    assert '"component":"Checklist"' not in body
    assert "Ship the release" not in body
    assert "A2UI Message Schema" not in body


async def test_the_body_reads_the_profiles_own_card(config):
    body = await skill_body(config)
    assert "Shelf" not in body

    write_card(config.workspace_dir / CARDS_DIR, "Shelf")

    assert "- **Shelf**: Use when the user asks what is on their shelf." in await skill_body(config)


# --- one Card's detail -------------------------------------------------------


async def test_a_cards_schema_and_example_are_fetched_one_at_a_time(config):
    """A Card's detail carries its field names and the shape to emit — and nobody
    else's, so fetching one Card costs one Card."""
    detail = await card_detail(config, "Checklist")

    assert '"title"' in detail
    assert '"items"' in detail
    assert '"component":"Checklist","title":"Ship the release"' in detail
    assert "MarketBoard" not in detail


async def test_a_cards_detail_matches_its_file(config):
    write_card(config.workspace_dir / CARDS_DIR, "Shelf", example="Hardbacks")

    detail = await card_detail(config, "Shelf")

    assert "Use when the user asks what is on their shelf." in detail
    assert '"component":"Shelf","title":"Hardbacks"' in detail


async def test_a_card_nobody_has_has_no_detail(config):
    with pytest.raises(FileNotFoundError):
        await card_detail(config, "NoSuchCard")


async def test_a_card_edited_on_disk_is_reflected_without_a_rebuild(config):
    """The body and the detail are rendered per read, so an edit lands on the next
    read of the same runtime — no agent rebuild."""
    path = write_card(config.workspace_dir / CARDS_DIR, "Shelf", description="The first thing.")
    view = a2ui_view(config)
    assert "The first thing." in await skill_body(config, view)

    path.write_text(
        path.read_text().replace("The first thing.", "Something else entirely, and longer.")
    )

    body = await skill_body(config, view)
    assert "Something else entirely, and longer." in body
    assert "The first thing." not in body
    assert "Something else entirely, and longer." in await card_detail(config, "Shelf", view)


async def test_a_card_dropped_in_is_disclosed_without_a_rebuild(config):
    view = a2ui_view(config)
    assert "Shelf" not in await skill_body(config, view)
    with pytest.raises(FileNotFoundError):
        await card_detail(config, "Shelf", view)

    write_card(config.workspace_dir / CARDS_DIR, "Shelf")

    assert "Shelf" in await skill_body(config, view)
    assert "Shelf" in await card_detail(config, "Shelf", view)


# --- a Card that is turned off ----------------------------------------------


async def test_a_disabled_card_is_in_neither_the_index_nor_the_detail(config, paths):
    """Install-wide Disable of a shared Card: gone from the body, and with no
    fetchable detail either."""
    write_card(paths.root / CARDS_DIR, "Shelf")
    view = a2ui_view(config)
    assert "Shelf" in await skill_body(config, view)

    CardStateStore(paths.root).set_enabled("Shelf", False)

    assert "Shelf" not in await skill_body(config, view)
    with pytest.raises(FileNotFoundError):
        await card_detail(config, "Shelf", view)


async def test_a_card_suppressed_in_one_profile_is_still_disclosed_in_another(paths):
    """Per-profile Suppression: off for one profile, offered to the other."""
    registry = ProfileRegistry(paths)
    base = Config.for_paths(paths)
    write_card(paths.root / CARDS_DIR, "Shelf")
    work = base.with_profile(registry.create_profile("Work", "#109e91"))
    personal = base.with_profile(registry.create_profile("Personal", "#109e91"))

    CardStateStore(paths.root).set_suppressed("Shelf", work.data_dir.name, True, SUPPRESS_SHARED)

    assert "Shelf" not in await skill_body(work)
    with pytest.raises(FileNotFoundError):
        await card_detail(work, "Shelf")
    assert "Shelf" in await skill_body(personal)
    assert "Shelf" in await card_detail(personal, "Shelf")


# --- the Skill's own switch --------------------------------------------------


async def test_the_skill_is_disable_able_install_wide(config, paths):
    """It passes the same filter as every other Skill: Disabled install-wide, it is
    absent from the catalog, unreadable, and resolves unavailable for the turn."""
    assert a2ui_available(config) is True
    assert A2UI_SKILL in _resident(config)

    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)

    assert a2ui_available(config) is False
    assert A2UI_SKILL not in _resident(config)
    with pytest.raises(SkillNotFoundError):
        await skill_body(config)

    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, True)

    assert a2ui_available(config) is True
    assert A2UI_SKILL in _resident(config)


async def test_the_skill_is_suppressible_in_one_profile(paths):
    registry = ProfileRegistry(paths)
    base = Config.for_paths(paths)
    work = base.with_profile(registry.create_profile("Work", "#109e91"))
    personal = base.with_profile(registry.create_profile("Personal", "#109e91"))

    SkillStateStore(paths.root).set_suppressed(
        A2UI_SKILL, work.data_dir.name, True, SUPPRESS_SHARED
    )

    assert a2ui_available(work) is False
    assert A2UI_SKILL not in _resident(work)
    with pytest.raises(SkillNotFoundError):
        await skill_body(work)
    assert a2ui_available(personal) is True
    assert A2UI_SKILL in _resident(personal)


# --- what a turn carries ------------------------------------------------


async def test_a_turn_no_longer_carries_the_whole_catalog(paths, tmp_path):
    """The prompt a turn is built with holds no component schema, no Card name and no
    worked example — the catalog is the Skill's now, not the turn's."""
    agent = _TurnAgent()
    gateway = await _started(Config.for_paths(paths, data_dir=tmp_path), agent)

    await gateway.send_message("what is the weather", chat_id="c1")

    prompt = "\n".join(str(part) for part in agent.prompts[0])
    assert BEHAVIOR_GUIDANCE in prompt  # the turn prompt really is what we are reading
    assert "A2UI Message Schema" not in prompt
    assert "createSurface" not in prompt
    for name in bundled_cards():
        assert name not in prompt


async def test_a_turn_carries_the_skill_catalog_the_agent_was_built_with(paths, tmp_path):
    """The per-turn prompt adds to the agent's own, never replaces it: the skills
    catalog — and with it the rich-views line naming every case — reaches the model."""
    config = Config.for_paths(paths, data_dir=tmp_path)
    agent = _TurnAgent()
    agent.system_prompt = ("You are the assistant.", _resident(config))
    gateway = await _started(config, agent)

    await gateway.send_message("what is the weather", chat_id="c1")

    prompt = "\n".join(str(part) for part in agent.prompts[0])
    assert "<available_skills>" in prompt
    assert "the weather" in prompt and A2UI_SKILL in prompt
    assert BEHAVIOR_GUIDANCE in prompt
    assert prompt.count("You are the assistant.") == 1


# --- the turn, with the Skill off --------------------------------------------


async def test_off_leaves_nothing_in_the_turn_that_parses_a_rich_view(paths, tmp_path):
    """Disabled install-wide, a turn carries no A2UI middleware — nothing parses,
    validates or recovers a rich view; enabled again, the next message draws."""
    agent = _TurnAgent()
    gateway = await _started(Config.for_paths(paths, data_dir=tmp_path), agent)

    await gateway.send_message("what is the weather", chat_id="c1")
    assert _turn_middleware(agent) != ()

    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)
    await gateway.send_message("and tomorrow", chat_id="c1")

    assert _turn_middleware(agent) == ()

    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, True)
    await gateway.send_message("and the day after", chat_id="c1")

    assert _turn_middleware(agent) != ()


async def test_a_profile_it_is_suppressed_in_stops_drawing_and_the_other_does_not(paths):
    """Suppressed in one profile, that profile's turns carry no A2UI at all while the
    other profile goes on drawing."""
    work, personal = _profile(paths, "Work"), _profile(paths, "Personal")
    SkillStateStore(paths.root).set_suppressed(
        A2UI_SKILL, work.data_dir.name, True, SUPPRESS_SHARED
    )
    at_work, at_home = _TurnAgent(), _TurnAgent()

    await (await _started(work, at_work)).send_message("what is the weather", chat_id="c1")
    await (await _started(personal, at_home)).send_message("what is the weather", chat_id="c1")

    assert _turn_middleware(at_work) == ()
    assert _turn_middleware(at_home) != ()


async def test_a_card_the_model_drew_is_still_replayed_with_the_skill_off(paths, tmp_path):
    """A Card instance a model drew into a chat before the switch moved still reaches
    the client as the primitives its layout declares."""
    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)
    gateway = await _started(Config.for_paths(paths, data_dir=tmp_path), _TurnAgent())
    await gateway.emit_event(
        "c1",
        A2UISurface(
            "s1",
            component={"id": "root", "component": "Checklist", "title": "Ship it"},
            data={"title": "Ship it", "items": ["Tag the release"]},
            title="Ship it",
        ),
    )
    socket = _ClientSocket()

    bridge = StreamBridge(gateway, socket, "c1")
    await bridge.open()
    bridge.close()

    drawn = [frame["event"] for frame in socket.sent if "event" in frame][0]
    assert drawn["data"]["component"]["component"] == "Card"
    assert {node["component"] for node in drawn["data"]["component"]["_components"]} <= (
        CARD_VOCABULARY
    )


async def test_the_coding_session_is_the_servers_to_draw_whatever_the_switch_says(paths, tmp_path):
    """The switch governs the rich views the agent draws. A Card the server fills — the
    coding session — is the app's own panel for a run, and goes on drawing."""
    SkillStateStore(paths.root).set_enabled(A2UI_SKILL, False)
    gateway = await _started(Config.for_paths(paths, data_dir=tmp_path), _TurnAgent())
    await gateway.emit_event(
        "c1",
        build_surface(
            "cs1",
            card_fields(
                agent_label="Claude Code",
                directory="/repo",
                task="add hello",
                status="done",
                files=[FileDiff("hello.py", "added", "@@ -0,0 +1 @@\n+hi\n", 1, 0)],
            ),
        ),
    )
    socket = _ClientSocket()

    bridge = StreamBridge(gateway, socket, "c1")
    await bridge.open()
    bridge.close()

    drawn = [frame["event"] for frame in socket.sent if "event" in frame]
    assert [event["data"]["component"]["component"] for event in drawn] == ["Card"]

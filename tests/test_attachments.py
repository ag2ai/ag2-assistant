"""Tests for building AG2 multimodal inputs from chat attachments, and for the
gateway threading them into the agent turn."""

from ag2 import ImageInput
from ag2.events import BinaryType, TextInput

from assistant.attachments import build_input
from assistant.gateway.core import Gateway
from tests.support.fakes import FakeRunMixin


def _kind(inp):
    """The BinaryType of a built input (factories return a tagged BinaryInput)."""
    return getattr(inp, "kind", None)


def test_image_by_extension():
    inp = build_input(b"\x89PNG...", "pic.png")
    assert _kind(inp) == BinaryType.IMAGE
    assert inp.media_type == "image/png"


def test_pdf_is_document():
    assert _kind(build_input(b"%PDF-1.4", "report.pdf")) == BinaryType.DOCUMENT


def test_audio_and_video():
    assert _kind(build_input(b"..", "voice.ogg")) == BinaryType.AUDIO
    assert _kind(build_input(b"..", "clip.mp4")) == BinaryType.VIDEO


def test_text_file_is_inlined():
    inp = build_input(b"hello world", "notes.txt")
    assert isinstance(inp, TextInput)
    assert "hello world" in inp.content


def test_unknown_binary_arrives_as_a_note_naming_the_file():
    """It used to be handed over as a document with `application/octet-stream` — a type
    AG2 does not declare and no provider accepts, so the turn failed at the provider.
    A note at least tells the agent a file arrived."""
    inp = build_input(b"\x00\x01", "data.bin")
    assert isinstance(inp, TextInput)
    assert "data.bin" in inp.content


def test_an_m4a_voice_note_is_not_sent_as_audio():
    """`audio/mp4` is in none of the three providers' accepted sets (Anthropic has no
    audio input at all, OpenAI takes WAV and MP3, Gemini's list omits it), and AG2 does
    not declare it either. It reaches the agent as a note instead."""
    inp = build_input(b"..", "voice.m4a")
    assert isinstance(inp, TextInput)
    assert "voice.m4a" in inp.content


def test_a_platform_type_outside_the_valid_set_is_not_forwarded():
    """Telegram's own MIME for a nameless recording is not one AG2 accepts for audio;
    it must not travel to the constructor (and thence to the provider) unchecked."""
    inp = build_input(b"..", "", media_type="audio/mp4")
    assert isinstance(inp, TextInput)
    assert "audio/mp4" in inp.content


def test_our_table_supplies_the_media_type_even_when_the_platform_disagrees():
    """ADR 0010 documents a platform's MIME as unreliable and the extension as strong
    and stable; the value handed to AG2 follows the extension too, not just the kind."""
    inp = build_input(b"\x89PNG", "pic.png", media_type="application/octet-stream")
    assert _kind(inp) == BinaryType.IMAGE
    assert inp.media_type == "image/png"


def test_every_extension_we_route_carries_a_type_ag2_declares():
    """Regression cover for ADR 0010's routing rules: adding validation must not change
    which kind is built for known-good input, nor the type it carries."""
    expected = {
        "a.png": (BinaryType.IMAGE, "image/png"),
        "a.jpg": (BinaryType.IMAGE, "image/jpeg"),
        "a.jpeg": (BinaryType.IMAGE, "image/jpeg"),
        "a.gif": (BinaryType.IMAGE, "image/gif"),
        "a.webp": (BinaryType.IMAGE, "image/webp"),
        "a.mp3": (BinaryType.AUDIO, "audio/mpeg"),
        "a.wav": (BinaryType.AUDIO, "audio/wav"),
        "a.ogg": (BinaryType.AUDIO, "audio/ogg"),
        "a.oga": (BinaryType.AUDIO, "audio/ogg"),
        "a.flac": (BinaryType.AUDIO, "audio/flac"),
        "a.aac": (BinaryType.AUDIO, "audio/aac"),
        "a.mp4": (BinaryType.VIDEO, "video/mp4"),
        "a.webm": (BinaryType.VIDEO, "video/webm"),
        "a.mov": (BinaryType.VIDEO, "video/quicktime"),
        "a.mkv": (BinaryType.VIDEO, "video/x-matroska"),
        "a.mpeg": (BinaryType.VIDEO, "video/mpeg"),
        "a.pdf": (BinaryType.DOCUMENT, "application/pdf"),
    }
    got = {
        name: (_kind(build_input(b"..", name)), build_input(b"..", name).media_type)
        for name in expected
    }
    assert got == expected


def test_mime_overrides_when_no_extension():
    inp = build_input(b"plain text", "noext", media_type="text/plain")
    assert isinstance(inp, TextInput)


def test_image_by_mime_when_no_extension():
    # A pasted screenshot arrives as bytes with an empty/extensionless name but a
    # real image MIME. Extension detection misses; MIME must route it to an image.
    inp = build_input(b"\x89PNG...", "", media_type="image/png")
    assert _kind(inp) == BinaryType.IMAGE
    assert inp.media_type == "image/png"


def test_audio_video_by_mime_when_no_extension():
    assert _kind(build_input(b"..", "", media_type="audio/ogg")) == BinaryType.AUDIO
    assert _kind(build_input(b"..", "", media_type="video/mp4")) == BinaryType.VIDEO


def test_pdf_by_mime_when_no_extension():
    got = build_input(b"%PDF", "", media_type="application/pdf")
    assert _kind(got) == BinaryType.DOCUMENT


def test_extension_wins_over_mime():
    # A real filename extension stays the primary key even if MIME disagrees.
    inp = build_input(b"\x89PNG", "pic.png", media_type="application/octet-stream")
    assert _kind(inp) == BinaryType.IMAGE


def test_empty_data_returns_none():
    assert build_input(b"", "pic.png") is None


# --- gateway passthrough ---


class _CapturingReply:
    def __init__(self, msg):
        self.body = "ok"
        self.captured = msg

    async def ask(self, *msg, **kwargs):
        return _CapturingReply(msg)


class _CapturingAgent(FakeRunMixin):
    def __init__(self):
        self.captured = None

    async def ask(self, *msg, **kwargs):
        self.captured = msg
        return _CapturingReply(msg)


async def test_gateway_passes_attachments_as_positional_inputs():

    gw = Gateway(memory=False, onboard=False)
    agent = _CapturingAgent()
    gw._agent = agent

    img = ImageInput(data=b"img", media_type="image/png")
    await gw.send_message("look at this", chat_id="s1", attachments=[img])

    # The agent received text + the attachment as positional inputs.
    assert agent.captured[0] == "look at this"
    assert agent.captured[1] is img

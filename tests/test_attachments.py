"""Tests for building AG2 multimodal inputs from chat attachments, and for the
gateway threading them into the agent turn."""

from ag2 import ImageInput
from ag2.events import BinaryType, TextInput

from assistant.attachments import build_input
from assistant.gateway.core import Gateway
from tests.conftest import FakeRunMixin


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


def test_unknown_binary_falls_back_to_document():
    assert _kind(build_input(b"\x00\x01", "data.bin")) == BinaryType.DOCUMENT


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

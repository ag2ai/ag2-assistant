"""Boot-path imports: the channel SDKs and the realtime/voice SDKs load on first use."""

import subprocess
import sys

import pytest

from assistant import channels

# Top-level packages that must not be on the ``import assistant.cli`` path.
_DEFERRED = {"telegram", "discord", "slack_bolt", "slack_sdk", "ag2.live"}

_PROBE = """
import sys
import assistant.cli
loaded = {m for m in sys.modules}
hit = sorted(p for p in %r if p in loaded or any(m.startswith(p + ".") for m in loaded))
print(",".join(hit))
"""


def test_cli_import_does_not_load_deferred_sdks(paths):
    out = subprocess.run(
        [sys.executable, "-c", _PROBE % sorted(_DEFERRED)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "AG2ASSISTANT_DATA_DIR": str(paths.root)},
    )
    assert out.stdout.strip() == ""


def test_get_channel_unknown_platform():
    with pytest.raises(ValueError, match="Unknown channel platform"):
        channels.get_channel("carrier-pigeon", token="x")


def test_adapter_classes_still_resolve_on_the_package():
    from assistant.channels.discord import DiscordChannel
    from assistant.channels.slack import SlackChannel
    from assistant.channels.telegram import TelegramChannel

    assert channels.TelegramChannel is TelegramChannel
    assert channels.DiscordChannel is DiscordChannel
    assert channels.SlackChannel is SlackChannel
    with pytest.raises(AttributeError):
        channels.NoSuchChannel  # noqa: B018

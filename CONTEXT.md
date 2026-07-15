# AG2 Assistant

A personal AI assistant that hosts multiple isolated profiles in one install. All
persistent state lives under a single root directory.

## Language

**Root**:
The single directory holding everything the assistant persists: global config,
global skills, and every profile's state. Overridable at launch.
_Avoid_: data dir (as a distinct concept), install dir

**Profile**:
A named, fully isolated runtime (own chats, tasks, memory, files, skills) hosted
inside the Root. One install hosts many profiles.
_Avoid_: account, user, persona

**Files** (profile files):
A profile's working file space — where the agent reads and writes files, including
deliverables it produces. Lives inside the profile; there is no separate visible
"workspace" folder outside the Root.
_Avoid_: workspace (the retired `~/Documents/AG2 Assistant` level)

**Accent**:
A Profile's visual identity — a single color it is themed with, stored as an
`#rrggbb` hex. The user picks it either from the frontend's preset palettes or as
any custom color; the backend only ever stores and validates the hex. The 10-step
color ramp the UI renders from it is a derived, frontend-only detail.
_Avoid_: palette (the frontend's preset catalog + derived ramp, not the domain
concept), color (too generic)

**Global config**:
The Root-level configuration covering everything install-wide: models,
integrations, agent parameters, defaults for all profiles.

**Profile config**:
A profile's configuration overlay: a key present here overrides the Global
config for this profile only. Holds profile-specific choices (focuses, MCP
servers, Project folder). The agent edits its own Profile config, never the
Global config or another profile's overlay.
_Avoid_: settings (the retired per-profile `settings.json`)
_Note_: the Text model and Live model are NOT here — both are install-wide (Global
config). A legacy per-profile `voice_provider` still exists as a fallback, but voice
is now configured install-wide via the Active Live model.

**Global skills**:
Skills installed once at the Root, available to every profile. Only the user
places skills here — the agent never installs or writes into this layer.

**Profile skills**:
Skills installed inside one profile, visible only to it. The default target for
every install and for agent-authored skills. On a name clash, the Profile skill
wins over the Global one.

**Permissions**:
The security policy of folder grants/blocks and allowed commands. Two layers —
install-wide at the Root and per-profile — merged as unions with deny-overrides:
a block from either layer always beats a grant, so a profile can narrow but
never widen the install's boundaries. Edited only by the user, never by the
agent.
_Avoid_: settings, config (permissions are policy, not configuration)

**Project folder**:
A user-chosen folder elsewhere on disk that the assistant may only read (backs the
read-only repo-files access). A read-only source, never assistant state.
_Avoid_: repo folder, source folder

**Archived** (profile state):
A profile flagged out of service: not running, hidden from the main Profiles list,
its channel bindings dropped — but its folder and all state stay intact on disk.
Reversible. A profile can never be archived while it is the last live profile or
the active default (archiving the default reassigns it first).

**Restore** (unarchive):
Return an Archived profile to live: clear the archived flag and boot its runtime,
symmetric with creating a profile. The profile keeps its stored Accent.
_Avoid_: recover (that is the separate mid-session flow when the *active* profile
is archived out from under an open client)

**Delete** (purge):
Permanent, irreversible removal of an Archived profile: erase its folder from disk
and drop its registry entry. Only ever applies to an already-Archived profile —
there is no one-step delete of a live profile. The only operation in the app that
destroys profile state.
_Avoid_: archive (archive keeps the data; delete does not)

## Models

The install-wide, named backends the assistant runs on. Two kinds — one for typed
chat, one for spoken voice — each an independent list with its own single Active
selection, both living in the Global config (shared across every Profile).

**Text model**:
A named configuration for a chat LLM — a provider/type, a model name, and an
optional per-config key. The assistant's "brain" for typed conversation. Shown as
the Text section of Settings → Models; exactly one is Active install-wide.
_Avoid_: LLM config (the `llm_configs` store/implementation name), model (bare —
ambiguous with a Live model)

**Live model**:
A named configuration for realtime voice — a Voice provider, a realtime model, an
optional per-config key, and a chosen Voice. The spoken counterpart of a Text model.
Shown as the Live section of Settings → Models; exactly one is Active install-wide.
_Avoid_: voice model / voice config (collide with Voice, the spoken attribute), live
config (the `live_configs` store name)

**Voice**:
The spoken voice a Live model talks in — one entry from its Voice provider's named
catalogue (e.g. marin, cedar, Puck). An attribute of a Live model, chosen with the
voice picker; not a configuration in its own right.
_Avoid_: voice model, voice config (those are the Live model)

**Voice provider**:
The realtime speech backend a Live model runs on — Gemini or OpenAI. A fixed,
code-defined registry: the user picks from it but cannot add one, unlike a Text
model's open-ended provider/type.
_Avoid_: provider (bare — a Text model has a provider too)

**Active** (model):
The single Text model and single Live model currently in effect, install-wide.
Switching re-points the whole install and persists; it takes effect on the next
message (Text) or next voice session (Live), never retroactively on one in flight.
_Avoid_: default, current, selected

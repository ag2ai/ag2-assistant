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
deliverables it produces. Lives inside the profile; always read+write to its own
profile with no Grant needed (Folders govern only paths outside the Root); there
is no separate visible "workspace" folder outside the Root.
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
servers). The agent edits its own Profile config, never the
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
The security policy of allowed commands (command-prefix and whole-tool grants).
Commands only — folder access is the separate Folder/Grant system. Edited only by
the user, never by the agent.
_Avoid_: settings, config (permissions are policy, not configuration), folder
permissions (that is a Grant)

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

## Chats

**Chat**:
A persisted conversation with the assistant inside a Profile: its transcript, its
title, and its Starred flag. Titled automatically after the first exchange; a
user Rename is authoritative and is never overwritten by the auto-titler.
Deleting a Chat is permanent and unrecoverable.
_Avoid_: session (the retired chat-sense name — MCP client sessions and realtime
voice sessions are different, protocol-level concepts that keep the word),
conversation, thread

**Starred** (chat):
A user-set flag on a Chat that lifts it into the Starred section pinned at the
top of the chat history. Toggleable at any time; unstarring returns the Chat to
its natural date group. No effect on the Chat's content or last-update time.
_Avoid_: pinned, favorite

## Folders

**Folder**:
A named, install-wide registry entry for one directory outside the Root — a name
and a path, unique by path. The only way disk outside the Root becomes reachable:
no Folder (or no Grant to it) means no access, with no block/deny concept. Deleting
a Folder is always allowed and revokes every Grant to it instantly; a Folder whose
path no longer exists on disk is a badged, repointable state, not an error.
_Avoid_: project folder (the retired single read-only mechanism), mount, share,
directory, workspace

**Grant**:
The link from one Profile or one Chat to a Folder, carrying a mode — `read` or
`read+write` (write implies read; write-only is unrepresentable). Effective access
in a chat is the union of its profile's Grants and its own; the most permissive
covering Grant wins, so Grants only ever widen and nothing subtracts. Created by
the user — via settings or by approving the agent's runtime request (which
auto-creates the Folder, auto-named and renameable) — never by the agent itself.
"Allow once" approves a single turn's access without creating a Folder or a Grant.
_Avoid_: permission (that is the commands policy), reference (a Secret's concept),
access rule

## Models

The install-wide, named backends the assistant runs on. Two kinds — one for typed
chat, one for spoken voice — each an independent list with its own single Active
selection, both living in the Global config (shared across every Profile).

**Text model**:
A named configuration for a chat LLM — a provider/type, a model name, and an
optional referenced Secret. The assistant's "brain" for typed conversation. Shown as
the Text section of Settings → Models; exactly one is Active install-wide.
_Avoid_: LLM config (the `llm_configs` store/implementation name), model (bare —
ambiguous with a Live model)

**Live model**:
A named configuration for realtime voice — a Voice provider, a realtime model, an
optional referenced Secret, and a chosen Voice. The spoken counterpart of a Text model.
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
_Avoid_: default, current, selected (a Secret's Default is the unrelated fallback
concept)

## Secrets

**Secret**:
A named, reusable API key — a name, a write-only value (only a last-4 hint is ever
shown back), and an optional provider tag. Referenced by any number of Text and
Live models; rotating its value re-keys every model that references it. Secrets are
unique by value: no two Secrets may hold the same key — pasting a known key in the
model form snaps to the existing Secret, and an explicit add with a duplicate value
is rejected with a pointer to it. The provider tag is soft: it groups and sorts,
never forbids — a Secret with no tag (or any tag) can be attached to any model,
which is what keeps custom/unknown endpoints workable. Covers LLM provider keys
only — channel bot tokens and the GitHub token are separate, non-reusable concepts.
_Avoid_: API key (the value inside a Secret, not the entity), credential, token
(collides with channel bot tokens), key (bare)

**Default** (secret):
The at-most-one Secret per provider tag that serves as that provider's install-wide
fallback: a model with no referenced Secret sends its provider's Default. Only a
provider-tagged Secret can be a Default. A key present only in the real environment
(e.g. `.env`) is the last-resort fallback below the Default; the Default wins when
both exist.
_Avoid_: shared key / provider key (the retired pre-Secret concept), active (that
is the models' concept)

**Referenced** (secret):
The link from one Text or Live model to the Secret it authenticates with. Optional —
an empty reference means fallback (Default, then environment). Deleting a Secret is
always allowed; models referencing it degrade to fallback and their health/key-source
labelling reports it honestly. In the model form, pasting a raw key mints a new
Secret on the spot with an auto-generated name, renameable later.
_Avoid_: attached, bound, owned (a Secret is never owned by one model)

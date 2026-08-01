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
A profile's working file space — where both the agent and the user read and write
files: the agent saves deliverables and chat output here, and the user browses,
uploads, renames, moves, and deletes them from the Files tab. Lives inside the
profile; always read+write to its own profile with no Grant needed (Folders govern
only paths outside the Root); there is no separate visible "workspace" folder
outside the Root.
_Avoid_: workspace (the retired `~/Documents/AG2 Assistant` level)

**Directory**:
A nesting level in the Files tree — any expandable node, wherever it is rooted. A
Directory in the profile's **Files** space lives inside the Root and needs no Grant;
a Directory nested inside a **Folder** lives outside the Root and inherits that
Folder's Grant (its reachability, and its `read`/`read+write` mode). Distinct from a
**Folder**, which is the install-wide registry entry / Grant target that *roots* a
granted subtree in the tree: the Folder is a root node, the Directories are the plain
nesting nodes beneath it (or in the Files space). UI copy says "directory" for
nesting nodes; "folder" is reserved for the Grant-target roots.
_Avoid_: folder (that is the Grant target — the registry entry that roots a subtree,
not a plain nesting node)

**Attachment** (message attachment):
A file bound to a single chat message so the agent sees it *this turn* — encoded
inline and sent with the message, transient (it is not persisted to the Files
space). Reached from the composer three ways, all the same pipeline: the `+`
picker, paste, and dropping a file onto the composer. Distinct from an **Upload**,
which writes a durable file into the Files space, and from a **File reference**,
which points at a file the agent can *already* reach (a path, not inline bytes).
_Avoid_: upload (an Attachment is not written to the Files space), file reference
(that carries a path to an already-accessible file, not encoded bytes)

**File reference**:
An inline `@`-pointer, dropped into a chat message, that names a file or
**Directory** the agent can *already* access — anything in the profile's **Files**
space or inside a Folder this chat holds a **Grant** to. Transient and
message-scoped like an **Attachment**, but it carries a **path**, not encoded
bytes: at send the reference resolves to an absolute path and is appended to the
message as a `Referenced files:` block, and the agent opens it itself (`read_file`
for a file, `list_folder` for a Directory). Picked from a type-to-filter `@`
picker fed by a chat-aware search over the reachable corpus; a pick shows inline as
a cosmetic `@label` while the underlying path is tracked in a list. UI copy: "@ a
file". (ADR 0012)
_Avoid_: attachment (that encodes bytes for a file the agent *can't* otherwise
reach; a File reference is a path to one it can), reference (bare — collides with a
Secret's **Referenced**; always qualify as "File reference"), mention

**Upload** (into Files):
Writing a durable file into a profile's **Files** space — via the Files tab's
upload button or by dropping OS files onto the **Files tree**. Persistent, lives
in the Root. The same drag-and-drop gesture means Upload on the Files tree but
**Attachment** on the composer — the drop *target* decides.
_Avoid_: attach (that is the transient, message-scoped Attachment)

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
servers, and the per-profile model **Active override**). The agent edits its own
Profile config, never the Global config or another profile's overlay.
_Avoid_: settings (the retired per-profile `settings.json`)
_Note_: the Text and Live model *definitions* are NOT here — both live in the single
shared install-wide store (Global config). What a profile *may* carry is an **Active
override**: which shared model is Active for this profile (see Active). A legacy
per-profile `voice_provider` still exists as a fallback, but voice is now configured
install-wide via the Active Live model.

**Bundled skills**:
First-party skills that ship with the app (e.g. `web-research`, `pdf-tools`,
`email-drafting`), available from first run. Read-only: they can be **Disabled**
(install-wide or **Suppressed** per profile) but never **Deleted** — there is no
writable file to remove, so their off-state is recorded separately.
_Avoid_: built-in (reserve for non-skill features), first-party skill (fine in
prose, but "Bundled" is the canonical term)

**Global skills**:
Skills installed once at the Root, available to every profile. Only the user
places skills here — the agent never installs or writes into this layer. Managed
install-wide (Enable/Disable/Delete affects every profile) and individually
**Suppressed** by any profile.

**Profile skills**:
Skills installed inside one profile, visible only to it. The default target for
every install and for agent-authored skills. On a name clash, the Profile skill
wins over the Global one. Fully managed by that profile: Enable/Disable/Delete.

**Skill state** (Enabled / Disabled):
Whether a skill appears in the agent's `<available_skills>` catalog. A new
concept: previously a skill was either present on disk (available) or absent.
**Disabled** keeps the skill installed but out of the catalog. Because the catalog
is a **construction-time snapshot**, a state change lands immediately in storage
but only reaches the running agent on its next build.
_Avoid_: uninstall (that is Delete — removal from disk), archive (a profile
concept)

**Suppression** (per-profile skill override):
A profile turning a **Global** or **Bundled** skill **off for itself only**,
without touching it for other profiles. Mirrors the Folders *structure*
(install-wide registry + per-profile records) but with the default **inverted**:
absence of a record means *inherit "on"* (like the model **Active override**), so
the record only ever exists to suppress. A Global skill is available to a profile
iff it is install-wide **Enabled** AND not **Suppressed** here.
_Avoid_: grant (Folders default-deny + opt-in; Suppression is default-allow +
opt-out — the opposite), disable (reserve for the install-wide / own-skill flag;
a profile *suppresses* a shared skill, it does not *disable* it for everyone)

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

## Navigation

**Page**:
The full composition of the main application view — the active **Tab** plus the open
**Thread** together. This is what the path addresses; switching between Chats, Tasks,
and Files, or opening a Chat/Task, moves you between Pages. Distinct from a Settings
**Section** (nav *inside* a Modal) and from a **Modal** layered on top.
_Avoid_: screen, route, view

**Tab**:
The Drawer's top-level switch between **Chats**, **Tasks**, and **Files** — which
list or tree fills the left rail. Exactly one Tab is active. A Tab is orthogonal to
the open **Thread**; the two together compose the current **Page**. Switching Tabs
changes only the rail, never the main pane.
_Avoid_: section (Settings' nav), page (a Tab is only half of a Page), view

**Thread**:
A **Chat** or a **Task** as opened in the main pane — the union of the two openable,
conversation-like items. At most one Thread is open. Orthogonal to the active Tab
(opening the Files Tab does not close the open Thread); together with the Tab it
composes the current **Page**. Not a synonym for Chat: a Chat is the persisted
entity, whereas a Thread is whichever Chat *or* Task is currently on screen.
_Avoid_: conversation, tab

**Modal**:
A single-purpose panel floating over the whole **Page** — **Settings**, **Memory**,
and the other dialogs. At most one Modal is open at a time; the Page underneath is
untouched and returns unchanged when the Modal is dismissed.
_Avoid_: overlay (reserved for the config-override metaphor, e.g. Profile config),
dialog, popup

**Section** (Settings):
A nav target inside the Settings **Modal** — General, Profiles, Models, Secrets,
Tools, Integrations, Advanced. The Settings Modal shows exactly one Section
at a time; opening Settings from the Drawer lands on the initial Section (General).
Some Sections group finer areas: Models holds the Text and Live areas, and
**Profiles** holds the profile list plus a three-tab **Profile config zone**
(Profile Memory · Folders · Focus areas). There is no longer a standalone Folders
Section — the install-wide Folder registry is reached through the Profiles zone's
Folders tab (ADR 0015). Distinct from a **Tab** (the Drawer), a **Profile config
tab** (inside the Profiles Section), and a **Page** (the Tab+Thread view underneath).
_Avoid_: page (that is the Tab+Thread view), tab, screen

**Profile config tab** (Settings → Profiles):
One of the three form tabs inside the **Profiles** Section that configure the
**active** Profile — **Profile Memory** (the profile's persona memory only),
**Folders** (this profile's **Grants** — one `read`/`read+write`/`none` switch per
registered **Folder**, with add-a-folder registering install-wide and auto-granting
this profile `read`), and **Focus areas** (the persona focus pills). Always scoped
to the active Profile: switching the active Profile re-points all three. Cross-profile
Folder granting is done by switching Profile, not from a multi-profile matrix (retired,
ADR 0015). The shared "Who you are" memory is *not* here — it is install-wide and
lives in Settings → Advanced.
_Avoid_: section (that is the Settings-nav target one level up), Drawer tab, page

**Active file**:
The one file the preview rail is currently showing, reflected back in the Files
tree as a highlighted row. Derived from the URL's aside slot (`aside=file:<path>`),
so it is whatever file the rail names — nothing is "active" when the rail is closed
or holds the Inspector. The `<path>` is either a **Files**-space-relative path or an
absolute path into a granted **Folder** (the rail and the raw endpoint tell the two
apart by absoluteness alone). Distinct from **selected** (the Files tree's
upload-target Directory) and from **Active** (model): those are unrelated senses of
the word.
_Avoid_: selected (that is the upload target), focused, current, open

**Reveal** (a file):
Surface the **Active file** where it lives in the Files tree: switch to the Files
**Tab**, expand its collapsed ancestor **Directories** (persisting, as if the user
clicked each chevron), pull a fresh listing so a just-written file is present, and
scroll its highlighted row into view. Triggered by clicking the filename in the
preview header (path-backed previews only). Works for a **Folder** file too, when
that Folder is reachable in the currently open **Thread**; a soft no-op if it is not
(you switched to a Thread without the Grant). A locate-and-surface action: it does
*not* change what is **Active** (the file is already the Active file) nor the
**selected** upload target.
_Avoid_: activate (the file is already Active — Reveal moves the Tab and viewport,
not the active state), open (that puts a file into the preview rail; Reveal assumes
it is already previewed), select (that is the upload target), locate

**Mentioned in** (file backlink):
The set of **Threads** whose transcript contains the **Active file**'s path — a
reverse link from a file back to the conversations touching it, surfaced from the
preview header (path-backed previews only). "Contains" is deliberately loose: the
path matched as a plain substring *anywhere* in the stream (a `Referenced files:`
block, a produce event, tool output, or bare prose), so the count is "Mentioned in
N threads", not "referenced by". Path-historical — it matches the path as it lives
now, so a moved/renamed file shows none. Spans this profile's **Chats** and **Task
Runs** (both are streams in `chats.db`), never another profile's. Distinct from a
**File reference**, which is the forward `@`-pointer into a message; this is the
backward file→Thread view. (ADR 0014)
_Avoid_: reference/mention (bare — a **File reference** is the forward `@`-pointer;
this is the reverse backlink), used in, contains

## Chats

**Chat**:
A persisted conversation with the assistant inside a Profile: its transcript, its
title, and its Starred flag. Titled automatically after the first exchange; a
user Rename is authoritative and is never overwritten by the auto-titler.
Deleting a Chat is permanent and unrecoverable.
_Avoid_: session (the retired chat-sense name — MCP client sessions and realtime
voice sessions are different, protocol-level concepts that keep the word),
conversation
_Note_: a Chat opened in the main pane is a **Thread** (the chat-or-task union) —
"thread" names that union, never the persisted Chat entity itself.
_Note_: a Chat is not owned by the surface that started it. The browser and a
**Peer** reach the same Chats, and a Peer may attach to a Chat begun in the browser
(ADR 0020). What a Chat can never do is cross Profiles.

**Starred**:
A user-set flag on a listed **Chat** or **Task** that lifts it into a "Starred"
section pinned at the top of its **Tab**'s rail — the chat history, or the task
list. Toggleable at any time from the row's kebab menu; a starred item shows
*only* in the Starred section, never also in its natural group below (a Chat's
date group, a Task's newest-first list). Purely a display pin: no effect on the
item's content, last-update time, scheduling, or runs. For a Task the pin also
outranks the needs-input float — a starred task that needs input stays in the
Starred section, its row's status icon still signalling the request.
_Avoid_: pinned, favorite

## Connections

**Connection**:
One configured instance of a messaging platform — Telegram, Discord or Slack — with
its own name, token(s), **Connection default profile**, **Paired accounts**, group
pins and **Channel exposure**. The unit of configuration, and the key everything
platform-side is stored under. A platform can be connected as many times as the user
wants: two Telegram bots are two independent Connections. Install-wide and never
owned by a Profile (ADR 0022). The **platform** survives as a field on it, saying
which adapter to construct and which surfaces exist.
_Avoid_: channel (the retired one-per-platform sense), integration (that is the
Settings section listing Connections), bot (that is the platform-side account),
binding (the retired one-platform-one-Profile link)

**Connection default profile**:
The Profile a **Connection**'s conversations land in when nothing else has been
chosen — one per Connection, set install-wide. A fallback, not an owner: it never
decides whether the Connection connects (its token does), changing it needs no
restart, and a **Peer** that has chosen its own **Peer profile** ignores it entirely.
A Connection with no default answers that it has nowhere to go rather than guessing,
and a Profile withdrawn from every one of its surfaces cannot be it.
_Avoid_: binding, assignment, owning profile, active default (that is the WebUI's
fallback Profile, an unrelated install-level setting)

**Peer**:
One conversation on the platform side — a direct message or a group — identified by
its **Connection** and that platform's own chat id. Keyed by the Connection, not the
platform: on Telegram a direct message's chat id is the user's own id, identical
across two bots, so two Connections would otherwise share one conversation. This is what
a Connection actually talks to, and what holds everything persisting between messages:
its **Peer profile**, its **Peer sender**, and the **Chat** it is currently attached to.
A Peer starts many Chats over time and keeps owning the ones it leaves — that is how a
**Task** started in a conversation delivers its outcome back there.
_Avoid_: session (the retired chat-sense name — see **Chat**), chat (that is the
persisted entity a Peer attaches to), user (a Peer is a conversation; several people
speak in a group Peer), conversation

**Peer profile**:
The Profile a **Peer** currently speaks to — persistent, platform-side, surviving
restarts. Chosen per Peer and never inherited from another Peer. Changing it always
moves the Peer to a fresh **Chat**, because a Chat cannot cross Profiles. A group Peer's
is *pinned*: chosen once when the bot first speaks there and re-pointed only from the
WebUI, since a group is read by everyone in it and by nobody's choice but the install's.
_Avoid_: active profile (**Active** names the model selection, and the WebUI's
"active Profile" is whichever Profile the open client is viewing — both are unrelated
senses), current profile, bound profile

**Attached** (Peer → Chat):
The link from a **Peer** to the one **Chat** it is speaking in. At most one Chat per
Peer. Attaching is pure navigation — it neither creates nor destroys a Chat, and the
Chat it leaves stays exactly as it was, reachable again later and from the browser
throughout.
_Avoid_: open (that is the browser's **Thread**), selected, bound, current

**Channel exposure**:
The set of surfaces a Profile is reachable from. Surfaces belong to a **Connection**,
not to a platform — one per Connection, except on Telegram where direct messages and
groups are withdrawable independently — so with two Telegram bots a Profile can
answer on one and not the other. Default-allow: absence of a record means reachable,
so a record exists only ever to withdraw. A Profile withdrawn from a surface cannot
be chosen there and none of its **Chats** are offered there. Set from inside the
Connection, read Connection-major: one row per Profile, a switch per surface.
_Avoid_: grant (Folders are default-deny + opt-in; exposure is the exact opposite),
permission (that is the commands policy), suppression (the per-profile skill override
— same shape, different subject)

**Paired account**:
A platform account allowed to speak to one **Connection** — being paired to the work
Telegram bot grants nothing on the personal one. Identity is the platform's
numeric user id. A handle (`@username`) is only an *invitation*: it pins to a numeric
id the first time that handle speaks, and is matched by id ever after — so releasing
or changing a handle neither breaks the pairing nor opens it to whoever takes the
handle next. An unpaired account gets no answer and learns nothing about the install
(ADR 0021).
_Avoid_: user (**Profile**'s _Avoid_ already reserves the word), member, allowlist
entry

**Peer sender**:
The platform account a **Peer** is judged by — the last one it served, and the account a
push into that conversation is gated on being a **Paired account** of the Connection. In a
direct message it is the person; in a group it is whoever spoke last, and a Peer holding
none is closed to a push until a message stamps one (ADR 0022).
_Avoid_: owner (a Peer has none — a group Peer belongs to no one account), author,
last speaker (an unpaired one is turned away before it can stamp anything), sender_id
(that is the inbound message's field, from which this is taken)

**Mirror**:
What an **Attached** Peer receives from its Chat: every completed message in that
Chat, whoever wrote it and from whichever surface — so a conversation held in the
browser reads back on the platform, and the reverse. Completed messages and the
agent's questions only; the intermediate work of a turn is not mirrored. A Peer
mirrors exactly the Chat it is attached to and stops the instant it attaches
elsewhere (ADR 0020).
_Avoid_: sync, broadcast, echo, notification (a task outcome pushed to a Peer is a
separate, unattached-delivery concept)

**Tool trace**:
The list of tools a **Peer**'s own turn called, shown in the conversation as that turn
runs and left behind as its record. Live while the turn is in flight, bounded in
length, and kept when the turn is stopped or fails. Distinct from the **Mirror**, which
carries a Chat's *completed* messages to an Attached Peer and never its intermediate
work.
_Avoid_: log, chips (the browser's rendering of the same events), progress (that names
the delivery callback, not the thing shown)

## Folders

**Folder**:
A named, install-wide registry entry for one directory outside the Root — a name
and a path, unique by path. The only way disk outside the Root becomes reachable:
no Folder (or no Grant to it) means no access, with no block/deny concept. Deleting
a Folder is always allowed and revokes every Grant to it instantly; a Folder whose
path no longer exists on disk is a badged, repointable state, not an error. In the
Files tree a Folder reachable in the currently open **Thread** roots a browsable
subtree of **Directories** and files — the tree's one Thread-scoped section (the
Files-space section stays Thread-independent). Its files preview and download, and
under a `read+write` Grant edit/rename/delete/move within that subtree, exactly as
Files-space files do.
_Avoid_: project folder (the retired single read-only mechanism), mount, share,
directory, workspace

**Grant**:
The link from one Profile or one Chat to a Folder, carrying a mode — `read` or
`read+write` (write implies read; write-only is unrepresentable), plus a chat-only
`none` that blocks a profile-granted Folder for one chat. Profile Grants are a
monotone allowlist. A chat Grant _overrides_ the profile Grant on the same Folder
for that chat only — widening, narrowing, or blocking it, never touching the
profile Grant or other chats (ADR 0006 amendment); with no chat override the
profile Grant stands, and the most permissive surviving covering Grant wins.
Created by the user — via settings or by approving the agent's runtime request
(which auto-creates the Folder, auto-named and renameable) — never by the agent itself.
"Allow once" approves a single turn's access without creating a Folder or a Grant.
_Avoid_: permission (that is the commands policy), reference (bare — a Secret's
**Referenced** link and a **File reference** both claim the word; never call a
Grant a reference), access rule

## Memory

**Persona memory** (profile memory):
A single Profile's learned-and-curated memory — preferences and context for that
persona only, which the agent keeps refining as you chat and the user edits freely.
Per-profile; edited from the **Profile Memory** tab in Settings → Profiles. Distinct
from the **Shared identity** doc, which is install-wide.
_Avoid_: profile config (that is the backend config overlay), settings

**Shared identity** ("Who you are"):
The install-wide "who the user is" document — identity facts (name, location,
timezone, family, writing voice) true no matter which Profile is active; every
Profile sees it. Edited from Settings → **Advanced**, deliberately *outside* the
per-profile Profile config zone because editing it reaches every Profile (ADR 0015).
_Avoid_: persona memory (that is the per-profile layer), profile memory, global config

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

**Custom endpoint** (Text model):
A **Text model** that names its own endpoint rather than reaching its vendor's. Naming
an endpoint is the whole of the distinction — there is no separate flag, and no
recorded memory of the **Template** it came from. A Custom endpoint is the only kind
of Text model that may choose its **API interface**; every other Text model reaches
one vendor over one settled surface. Ollama is *not* a Custom endpoint: its local
address is a different field and admits no interface choice.
_Avoid_: compatible model, custom provider, self-hosted (an endpoint may be a cloud
proxy), BYO endpoint

**API interface**:
The wire a **Custom endpoint** is spoken over — OpenAI · Responses, OpenAI · Chat
Completions, or Anthropic. An attribute of a Text model, offered only once that model
names an endpoint and hidden entirely otherwise: the vendor-reaching Text models each
have exactly one surface and so present no choice. Naming an endpoint reveals the
choice; withdrawing the endpoint settles it back to the vendor's own surface.
_Avoid_: type (the stored field's name, which also carries the vendor), protocol, API
version, provider (the vendor is a separate axis and is never chosen here)

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
The single Text model and single Live model currently in effect. The install-wide
Active is the default; a **Profile** may override *which* shared model is Active for
it (a per-profile **Active override**), and the effective Active is the profile
override when set, else the install-wide Active, else the environment fallback (an
env pin still wins last and is unswitchable). The models themselves stay a single
shared install-wide list (ADR 0004) — only the *selection* is per-profile. Switching
persists and takes effect on the next message (Text) or next voice session (Live),
never retroactively on one in flight.
_Avoid_: default, current, selected (a Secret's Default is the unrelated fallback
concept)

**Active override** (per-profile model):
A Profile's optional choice of which shared **Text model** and which shared **Live
model** is **Active** for it, overriding the install-wide Active for that Profile
only. Set from two model switchers in the header of Settings → **Profiles** (Text
reuses the composer's switcher; Live is a parallel one), each offering "use install
default" to clear the override. Stored in the Profile's config overlay, never forking
the shared model list (ADR 0015 · ADR 0004 amendment).
_Avoid_: profile model (there is no per-profile model, only a per-profile selection),
default

**Template** (model):
A prefilled starting point for creating a **Text model** — a vendor, a type, and
default field values, offered as a grid of cards when adding a model. Picking one
opens the model editor prefilled; a Template is never saved, never listed, and has no
lifecycle of its own — it exists only in the moment of creation. Templates are
presented in groups under one rule: those needing no API key group together and come
first, everything else groups by vendor. A Template's group and its **Credential
source** both follow from its type, never from the Template itself. A Template that
seeds an endpoint creates a **Custom endpoint**, whose **API interface** is then the
user's to change; every other Template's surface is settled at the moment it is
picked and is never presented again.
_Avoid_: preset, starter, example (a Template is prefill, not a sample), provider (a
Template names one but is not one)

**Model catalog**:
The models an *authority* says it offers, read live and never stored. Two kinds of
authority answer: an ACP adapter, asked for the catalog behind a CLI-login **Text
model**, and a provider endpoint, asked for the catalog behind a keyed one. A catalog
is the sole authority on which model names *exist*; when it cannot be read, the reason
is named rather than hidden, and **Known models** stands in.
_Avoid_: model list (bare — collides with the list of saved Text models), available
models, inventory

**Known models**:
What this install knows *about* model names — a label, a price, and a context window
per name, shipped with the app. Known models is emphatically not a statement that a
name exists: it adorns the names a **Model catalog** returns, stands in (marked as
unverified) when no catalog can be read, and supplies the names offered before any key
is available. A name a catalog returns but Known models has never heard of is offered
plainly; its missing price is the honest signal that it is newer than the app.
_Avoid_: catalog (that is the live authority), default models, supported models (a
Known model may not exist; an existing model may be unknown)

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
The link from one Text or Live model to the Secret it authenticates with. Distinct
from a **File reference** (an `@`-pointer to a file) — this is the model→Secret
sense of the word, and stays qualified as "Referenced (secret)". Optional —
an empty reference means fallback (Default, then environment), or, for a model whose
**Credential source** is a subscription or a CLI login, no Secret at any point.
Deleting a Secret is
always allowed; models referencing it degrade to fallback and their health/key-source
labelling reports it honestly. In the model form, pasting a raw key mints a new
Secret on the spot with an auto-generated name, renameable later.
_Avoid_: attached, bound, owned (a Secret is never owned by one model)

**Credential source**:
Where a **Text model**'s credentials actually come from when a call is made — a
**Referenced** Secret, a provider **Default**, the environment, an OAuth subscription
sign-in, a CLI login the user already holds, or nothing at all (a local or
custom-endpoint model needing no key). Exactly one applies to any model at any moment,
and the model's key labelling names it honestly rather than merely reporting whether a
Secret exists. Distinguishes the two credential paths that bypass Secrets entirely:
a subscription model authenticates with an OAuth sign-in, and a CLI-login model
borrows the session of a provider's own command-line tool.
_Avoid_: key source (the implementation's field name), auth, key (bare), credential
(the Secret entry already claims this word)

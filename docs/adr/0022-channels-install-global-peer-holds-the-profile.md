# Channels are install-global; the Peer holds the Profile selection

A **Channel** used to be an install-level resource *assigned to exactly one Profile*:
the registry stored `channels: {platform: profile_id|null}`, the adapter was
constructed inside that Profile's runtime and handed that runtime's gateway, and the
Settings copy said it plainly — "each connects to one profile." One Telegram bot could
therefore serve exactly one Profile, and reaching a second Profile meant rebinding the
whole install and cutting off the first.

We invert the ownership. Every adapter now starts **once at install level** and
resolves a Profile per inbound message. The unit that carries the selection is the
**Peer** — one conversation on the platform side, a DM or a group, identified by
platform + that platform's chat id. A Peer holds its **Peer profile** and the **Chat**
it is attached to, persistently across restarts. The `{platform: profile}` binding is
replaced by two separate things that used to be conflated inside it: a per-Channel
**default profile** (which is the *only* mode Discord and Slack have, since they get
no commands) and per-Profile **Channel exposure** (which surfaces a Profile is
reachable from at all).

Exposure is deliberately shaped as the inverse of **Grant** and the twin of
**Suppression**: default-allow, with a record existing only ever to withdraw. Folders
are default-deny because they expose disk *outside* the Root to an agent; Channel
exposure governs which of the owner's own Profiles answer on a surface the owner
already had to pair an account into, so a fresh Profile that is silently mute in
Telegram would be a confusing dead state, not a safe one.

## Considered options

- **Keep the binding and only add commands inside the bound Profile** — rejected: it
  leaves the original problem entirely unsolved. Switching Profile is the whole point.
- **Selection per platform *user* rather than per conversation** — rejected: identical
  to per-conversation in a DM (a person has one DM with the bot), and incoherent in a
  group, where one visible thread would silently fork into as many parallel contexts
  as there are speakers.
- **Make only Telegram global and leave Discord/Slack bound** — rejected: `Channel`,
  `PUSH_CHANNELS`, `bind_channel`/`restart_channel`, the registry and the Settings
  section are all shared by the three platforms. Splitting the model in half would
  leave two competing channel architectures, two registries and two UI sections as
  permanent debt.

## Consequences

- **Mechanism for all three platforms, commands for Telegram only.** Discord and Slack
  move to install level and gain exposure, but without a command surface they can only
  ever sit in their Channel's default profile. That is why the default profile setting
  survives at all; for Telegram it is a fallback, since a new Telegram Peer facing more
  than one exposed Profile is made to choose explicitly.
- **A Chat id can no longer double as a platform address.** Chats were keyed
  `telegram:<platform_chat_id>`, which is exactly how a task pushed its outcome back
  to the right conversation. With one Peer owning many Chats over time, the two must be
  separate: new Chats are minted with an opaque origin-prefixed id, mirroring the
  browser's, and a task records the **Peer** it originated from rather than its Chat id.
- **Peer state is install-level**, alongside the Folder registry and permissions — it
  spans Profiles by construction and cannot live inside any one of them.
- **Withdrawing exposure from under a live Peer stops it.** The Peer is told its
  Profile is no longer reachable and must choose another; it is never migrated silently,
  which would drop a message written for one Profile into another Profile's transcript.
- **No migration.** The old binding, the old Chat id scheme and the old Settings section
  are replaced outright.

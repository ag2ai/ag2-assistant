# Channel access requires a Paired account; groups are fenced separately

Channels had no authorisation of any kind. `InboundMessage` carried a `sender_id` and
nothing ever read it: anyone who learned the bot's handle got the assistant with its
tools, Folder Grants, memory and tasks. That was survivable only because a Channel was
pinned to a single Profile and the bot was assumed secret.

ADR 0022 and ADR 0020 make it unsurvivable. A stranger would now get `/profile` — an
enumeration of every Profile in the install and entry into any of them — plus `/resume`
into any Chat, including ones begun in the browser. So a **Paired account** allowlist
becomes a precondition of the feature, not an enhancement: an unpaired account gets no
answer and learns nothing about the install, not even that a Profile named `work`
exists.

**Numeric id is identity; a handle is an invitation.** A bot cannot resolve a
`@username` to an id — `resolveUsername` is MTProto, not the Bot API — so a handle
typed into Settings cannot be validated when entered, only matched later against
whoever turns up. Handles are also mutable and re-assignable, which makes
match-by-handle a guess rather than an authentication: release `@nikita` and its next
owner inherits your allowlist entry. A handle entry therefore lives in a pending state,
pins to the numeric id of the first account presenting it, and is matched by id ever
after. Pairing by one-time code from Settings is the other, id-native path.

**Groups are fenced by exposure, not by discipline.** The allowlist governs who may
*write* to the bot; it says nothing about the dozen people who can *read* a group. A
paired user could mis-tap `/resume` in a work group and publish a private Chat to all
of them. So **Channel exposure** separates Telegram DMs from Telegram groups as
independent surfaces: a Profile not exposed to groups cannot be chosen in one and none
of its Chats are offered there, which makes "give a group its own Profile" a property
of the model rather than a habit. A group Peer additionally *pins* its Profile — chosen
once, re-pointed only from the WebUI — so no single member can move a shared context
out from under the others.

## Considered options

- **Rely on the bot handle staying secret** — rejected: not a control, and the blast
  radius is now every Profile and every Chat in the install.
- **Manual numeric id entry only** — rejected as the sole path: correct, but the id is
  not discoverable from the Telegram client, so onboarding would route through a
  third-party bot. Kept as a secondary path alongside the code.
- **Match handles on every message** — rejected: see above, this is the re-assignment
  hole.
- **A profile × account matrix** — rejected: it would import a user model the domain
  does not have. A **Profile** is explicitly *not* a user (see the glossary's
  `_Avoid_`); Profiles are facets of one owner, and the allowlist is about people.
  Access is scoped per surface, not per person.
- **Confirming risky `/resume` picks in groups instead of fencing them** — rejected:
  it makes leakage depend on someone reading a warning while in a hurry.

## Consequences

- Pairing is a prerequisite of first use; a fresh install with a token but no paired
  account answers nobody, which is the intended failure mode.
- Group Peers cannot switch Profile from the platform at all. `/profile` is refused
  there, and re-pointing is a WebUI action.
- The allowlist is per Channel, not per Profile — it answers "who may speak", while
  exposure answers "what may be spoken to". Neither substitutes for the other.
- Revocation bites at once inbound, and outbound too — a push re-reads the allowlist
  before delivering (ADR 0022). The one lag is a task already running in a group: the
  gate resolves the Peer's current sender, so a revoked account's outcome still arrives
  if another paired member spoke while the run was in flight.

# The Text model selection is per-Chat, and the composer switches the Chat

A **Chat** may now carry a **Chat override**: which shared **Text model** is **Active**
for that Chat alone. The composer's model switcher — which until now was a shortcut for
the *install-wide* Active, re-pointing every Profile and every Chat from a control
sitting under one conversation — becomes the setter for that override, and the
install-wide Active is reachable only from Settings → Models. On a **Channel** the same
choice is made with `/model`. The shared model list is untouched: as with the
per-profile **Active override** (ADR 0015), only the *selection* moves down a level
(ADR 0004 amendment).

Resolution becomes, outermost first: **env pin > Chat override > Task model (inside a
Run's thread only) > profile Active override > install-wide Active > env fallback**.

An **env pin** is `AG2ASSISTANT_MODEL` in the *process* environment — a deployment-level
model selection. A provider set without a model is not a pin of the model, and neither is
a saved **Secret** whose env happens to carry the variable: that is user data, and letting
it pin would disable every Chat override install-wide from a Settings page.

## Why

The composer's switcher was titled "Model for your next message" and meant "model for
everyone's next message" — a user reaching for "let me ask *this* one question of the
expensive model" silently re-pointed their whole install, and the only honest per-scope
selection lived two levels away in Settings. Meanwhile **Task** already had exactly the
concept we wanted (`model`, "None = profile default") and `send_message` already
accepted a per-turn `llm_config_id` with a cached per-config agent behind it. The
missing piece was never the mechanism; it was a place to record the intent per Chat.

## Considered options

- **A Chat remembers the model it was born on** (bound at creation, never drifts) —
  rejected: it makes every Chat permanently opinionated, forces a decision about what to
  write into every existing transcript doc that records no model, and loses the useful
  "just follow whatever I'm defaulting to" state. Absent-means-inherit matches `Task.model`.
- **Keep the composer switcher install-wide and add a second, per-Chat control** —
  rejected: two model switchers around one composer that differ only in blast radius is
  a distinction nobody can make at a glance, and it preserves the original footgun.
- **A new Chat inherits the previous Chat's override** — rejected: it re-introduces the
  invisible drift the override exists to remove. A Chat override should always be
  something you did to *that* Chat.
- **Per-chat Live model too** — rejected for now: Live resolves on its own path from its
  own store, and a voice session's binding to a Chat is a separate question we did not
  want to answer in passing.
- **Refuse `/model` in groups, like `/profile`** — accepted, though it is the odd one
  out: every other Channel command works in a group, including `/clear`, which
  permanently destroys the group's Chat. The deciding factor was disclosure — the picker
  prints your Text model *names*, which are user-chosen strings ("Opus · work key"), into
  a room whose membership is not yours to control.

## Consequences

- **The fast path to the install-wide default leaves the main screen.** Changing what
  everything defaults to is now Settings → Models ("Use"), two clicks in. Deliberate:
  the frequent action (this Chat) got the near control, the rare one (the whole install)
  got the far one.
- **Background work does not follow the override.** A Chat's generated title and a Run's
  summary resolve `cheap_model` from the *Profile*, never from the Chat. Pinning a Chat
  to an expensive model must not quietly make its six-word title expensive. This is a
  carve-out in code and will look like an oversight to anyone who does not read this.
- **A Run's thread resolves through five layers, and no other stream does.** In exchange
  it fixes a real bug: a manual reply typed into a Run's thread ran on the Profile
  default, not the Task's model, even though the Run itself ran on the Task's model. The
  auto-resolution from `chat_id` that already serves folder and command grants is
  extended to cover the model.
- **A deleted model leaves a dangling override**, which falls silently down the chain and
  is never swept up — matching the profile override's existing "degrade, don't fail". The
  switcher always renders the *effective* model, so the display never lies; it simply
  does not mention the ghost. Deleting a model therefore stays O(1) instead of rewriting
  every transcript doc that ever referenced it.
- **The two clients disagree about an unsent choice.** A model picked before a Chat
  exists is ephemeral in the WebUI (component state, lost on reload) and durable on a
  Channel (**Pending override**, in `peers.json`). Accepted: a bot has no client to hold
  it, and materialising an empty Chat just to store it would litter the drawer.
- **A group member cannot set the model at all**, though the same member can `/clear` the
  Chat. See the disclosure argument above.

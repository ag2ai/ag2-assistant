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

That chain describes a turn a *person* sends. A caller that names a model outright —
`send_message(..., llm_config_id=…)`, today only the Task service running a **Run**'s own
turn — sits **above the Chat override**, directly under the env pin: naming a model means
"run exactly this". So overriding a Run's thread retargets the follow-ups you type into
it, while the Run itself keeps running on the Task's model, unchanged and unmigrated. The
same thread therefore resolves two ways, by who is speaking, which is the point: the Task
layer exists so a *manual* reply is answered by the model that did the work, and the
override exists so you can ask one cheap follow-up about an expensive Run without editing
the Task. A model chosen in a client before the Chat existed is not such a caller — it is
the Chat's own first say, and sits at the Chat-override layer.

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
- **Let a Chat override outrank an explicitly-passed `llm_config_id`**, so that
  overriding a Run's thread also moves the Run's own turns — rejected: it turns a chat
  window into a silent editor of Task configuration. A Task's model is a property of the
  automated work, chosen once and expected to hold; a thread override is a property of
  the conversation you are having about it. Overriding a thread to a cheap model to ask
  one question must not quietly downgrade every future run of that Task, and there would
  be nothing in the Task editor to show that it had.
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
- **A deleted model leaves a dangling override**, which falls silently to the layer
  directly beneath it — in a Run's thread, the Task's model — and is never swept up,
  matching the profile override's existing "degrade, don't fail". Degrading is *walking
  the chain*, not skipping to the bottom: the drop happens where the override is read,
  so the turn and `effective_model` cannot disagree about it. A model that still exists
  but cannot run is not dangling and gets no rescue: that turn fails, exactly as an
  unusable install-wide Active does today. The
  switcher always renders the *effective* model, so the display never lies; it simply
  does not mention the ghost. Deleting a model therefore stays O(1) instead of rewriting
  every transcript doc that ever referenced it.
- **The two clients disagree about an unsent choice.** A model picked before a Chat
  exists is ephemeral in the WebUI (component state, lost on reload) and durable on a
  Channel (**Pending override**, in `peers.json`). Accepted: a bot has no client to hold
  it, and materialising an empty Chat just to store it would litter the drawer.
- **The WebUI switcher waits for its Chat read before it will act.** Which of the two
  mechanisms a pick uses — a patch, or a model riding the first turn — depends on
  whether the Chat exists, which only the read can say; and the wrong mechanism fails
  *silently*, because the send path ignores a client-supplied model on a Chat that
  already has a transcript. So "not loaded yet" is a third state, not a synonym for
  "no Chat yet": the control is inert for that moment, and a pick that somehow beat the
  read is reconciled into a patch when it lands.
- **A group member cannot set the model at all**, though the same member can `/clear` the
  Chat. See the disclosure argument above.

// The open Chat's Text-model selection — the state behind the composer's model
// switcher (ADR 0025). The switcher sets THIS Chat's override (PATCH /chats/{id}
// {model}), never the install-wide Active; that action lives in Settings → Models.
//
// Pure: every function is a fold over the state the store in store.js holds, so the
// rules are testable without a browser. The api calls and the store writes live in
// components/composer/ModelSwitcher.svelte and controller.js.
//
//   chatId   the Chat this state describes (a late read for another one is dropped)
//   override the Chat's own selection — '' means it inherits
//   effective the config id a message sent right now would run on (server-resolved,
//            so the client never has to walk profile → install-wide itself)
//   exists   whether the Chat is on the server yet (it is created by its first
//            message) — null until the read lands, which is NOT the same as false:
//            an existing Chat read as "not there yet" would send the choice on the
//            turn instead of patching it, and the server drops a chat_model on a Chat
//            that already has a transcript. Unknown is therefore its own state, and
//            `loadedChat` reconciles anything held while it was unknown.
//   pending  a choice made before the Chat existed: null = none, '' = "Use default".
//            Deliberately page-local — not localStorage, not the server — so a reload
//            returns to inheriting. It is applied by the first message, which the
//            server then records as the Chat's own override.

export const NO_CHAT_MODEL = {
  chatId: null,
  override: '',
  effective: '',
  exists: null,
  pending: null,
}

/** The state a freshly opened Chat starts from: nothing known, nothing held. */
export function openedChat(chatId) {
  return { ...NO_CHAT_MODEL, chatId }
}

/** Fold a GET /chats/{id} body in. Returns the next state and a model to PATCH, which
 *  is non-null only when the read settles a choice made while `exists` was unknown and
 *  the Chat turns out to exist — that choice is its override, and must be written now
 *  rather than ride the next turn (the server ignores a chat_model on a Chat that
 *  already has a transcript, so it would be lost). A read that resolves after the user
 *  has moved to another Chat is dropped rather than shown against the wrong
 *  conversation. */
export function loadedChat(state, chatId, body) {
  if (state.chatId !== chatId) return { state, patch: null }
  const exists = (body.messages || []).length > 0
  const next = {
    ...state,
    override: body.model || '',
    effective: body.effective_model || '',
    exists,
  }
  if (exists && state.pending !== null) {
    return { state: { ...next, override: state.pending, pending: null }, patch: state.pending }
  }
  return { state: next, patch: null }
}

/** Choose `id` ('' = "Use default"). Returns the next state and the model to PATCH, or
 *  `patch: null` when the choice is held in the page instead: there is no Chat yet, or
 *  the read that would say so has not landed (held, then reconciled by `loadedChat` —
 *  the switcher is inert while unknown, so this is the belt to that braces). */
export function chooseModel(state, id) {
  const model = id || ''
  if (state.exists !== true) return { state: { ...state, pending: model }, patch: null }
  return { state: { ...state, override: model, pending: null }, patch: model }
}

/** Consume any held choice for the turn about to be sent: the model to put on the
 *  frame ('' = nothing held), and the state that turn leaves behind — the message
 *  creates the Chat, and the server records the choice as its override. */
export function sentFirstMessage(state) {
  if (state.pending === null) return { state, model: '' }
  return {
    state: { ...state, override: state.pending, pending: null, exists: true },
    model: state.pending,
  }
}

/** What the switcher renders: the config to name on the closed button and mark in
 *  the menu, and whether that selection is inherited (which marks "Use default"
 *  instead). A held choice outranks the loaded one — it is what the next message
 *  will run on, which is what the switcher promises. */
export function switcherSelection(state) {
  const chosen = state.pending === null ? state.override : state.pending
  return { activeId: chosen || state.effective || null, inherited: !chosen }
}

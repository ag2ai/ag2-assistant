// The open Chat's Text-model selection — a pure fold over the state store.ts holds for
// the composer's switcher, which PATCHes /chats/{id} {model} (ADR 0025).
//
//   chatId    the Chat this state describes (a late read for another one is dropped)
//   override  the Chat's own selection — '' means it inherits
//   effective the config id a message sent right now would run on, server-resolved
//   exists    whether the Chat is on the server yet — null until the read lands, which
//             is its own state: a pick made while unknown is held, then reconciled
//   pending   a choice made before the Chat existed: null = none, '' = "Use default".
//             Page-local, so a reload returns to inheriting.
import type { Transcript } from '../schemas/index.ts'

export type ChatModelState = {
  chatId: string | null
  override: string
  effective: string
  exists: boolean | null
  pending: string | null
}

// Every fold below answers with the next state and what to write to the server:
// a model id ('' clears the override), or null when there is nothing to write.
export type ChatModelStep = { state: ChatModelState; patch: string | null }

export const NO_CHAT_MODEL: ChatModelState = {
  chatId: null,
  override: '',
  effective: '',
  exists: null,
  pending: null,
}

/** The state a freshly opened Chat starts from: nothing known, nothing held. */
export function openedChat(chatId: string): ChatModelState {
  return { ...NO_CHAT_MODEL, chatId }
}

/** Fold a GET /chats/{id} body in, dropping a read for a Chat no longer open. Returns
 *  the next state and a model to PATCH — non-null only when a choice held while
 *  `exists` was unknown lands on a Chat that turns out to exist. */
export function loadedChat(
  state: ChatModelState,
  chatId: string,
  body: Pick<Transcript, 'messages'> & { model?: string; effective_model?: string },
): ChatModelStep {
  if (state.chatId !== chatId) return { state, patch: null }
  const exists = (body.messages || []).length > 0
  const next: ChatModelState = {
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
 *  `patch: null` when there is no Chat to write it to yet and the choice is held. */
export function chooseModel(state: ChatModelState, id: string | null | undefined): ChatModelStep {
  const model = id || ''
  if (state.exists !== true) return { state: { ...state, pending: model }, patch: null }
  return { state: { ...state, override: model, pending: null }, patch: model }
}

/** Consume any held choice for the turn about to be sent: the model to put on the frame
 *  ('' = nothing held), and the state that turn leaves behind. */
export function sentFirstMessage(state: ChatModelState): { state: ChatModelState; model: string } {
  if (state.pending === null) return { state, model: '' }
  return {
    state: { ...state, override: state.pending, pending: null, exists: true },
    model: state.pending,
  }
}

/** What the switcher renders: the config to name on the closed button and mark in the
 *  menu, and whether it is inherited (which marks "Use default"). A held choice wins. */
export function switcherSelection(
  state: ChatModelState,
): { activeId: string | null; inherited: boolean } {
  const chosen = state.pending === null ? state.override : state.pending
  return { activeId: chosen || state.effective || null, inherited: !chosen }
}

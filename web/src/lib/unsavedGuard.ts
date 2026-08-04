// A guard for the rail editor's unsaved edits: the router's aside helpers consult it
// before a teardown (close, switch to Inspector, open another file). ADR 0011.
type DirtyCheck = () => boolean

let dirtyCheck: DirtyCheck | null = null

// The editor registers its dirty predicate while mounted; a non-function clears it.
export function setUnsavedGuard(fn: DirtyCheck | null): void {
  dirtyCheck = typeof fn === 'function' ? fn : null
}

// The confirmation shown before discarding unsaved editor changes.
export const DISCARD_PROMPT = 'You have unsaved changes here. Discard them?'

// True when it's safe to proceed: nothing registered, the editor is clean, or the
// user accepted the prompt. `confirm` is injected so the decision tests headless.
export function confirmDiscard(confirm: (message?: string) => boolean = globalThis.confirm): boolean {
  if (!dirtyCheck || !dirtyCheck()) return true
  return !!confirm(DISCARD_PROMPT)
}

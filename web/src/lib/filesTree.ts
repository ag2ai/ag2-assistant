// Pure logic for the Files-tree body's click behavior. The tree body doubles as the
// "click empty space to clear the upload/mkdir target" surface, so it must tell a
// background click apart from a click that bubbled up from a row (which manages its
// own target). Centralizing the decision here means no row handler needs its own
// `stopPropagation`, and a new row type can't silently reintroduce the "clicking a
// row wipes the selection" bug — the same `closest()` idiom onDocPointer already uses
// for menus. Import-free so it unit-tests under node:test with a jsdom element.

// True when a click on the tree body should CLEAR the selected target: only a click
// on the tree background itself, never one on a row (`.ftrow`). A missing/invalid
// target (no `closest`) is treated as "not background" — never wipe on uncertainty.
export function clearsTreeTarget(target: unknown): boolean {
  if (!canClose(target)) return false
  return !target.closest('.ftrow')
}

// Duck-typed instead of `instanceof Element`: the caller hands over an EventTarget,
// and the test drives this with a jsdom element from another realm.
function canClose(v: unknown): v is Pick<Element, 'closest'> {
  return !!v && typeof (v as Partial<Element>).closest === 'function'
}

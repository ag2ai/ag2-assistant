# Preview as a URL-addressable right-rail aside

ADR 0008 made the **URL the source of truth** for shell navigation across three
dimensions — the active **Tab**, the open **Thread**, and the open **Modal** — but
deliberately left the right-side rail (the AG2 Inspector) out of the URL, and the
file preview was a centered modal invisible to navigation: it couldn't be
deep-linked, Back didn't close it, and a refresh lost it.

We add a **fourth orthogonal shell dimension — the `aside`** (the right rail) — and
make it URL-authoritative on the same footing as Tab, Thread, and Modal. The single
overlay hash slot of ADR 0008 (`#settings=<section>`) becomes a **multi-key
fragment** (`#settings=<section>&aside=<value>`) so the Modal and the aside coexist
and each is preserved when the other changes. The aside key owns the whole rail and
holds **one** value — `aside=file:<workspace-relative-path>` (a file preview) or
`aside=inspector` (the AG2 Inspector) — so preview⇄Inspector **mutual exclusion is
structural**: one slot can't hold two occupants, and no cross-component coordination
code is needed. The file preview moves from a centered modal into a **docked panel**
in the app grid's right column; the main conversation column flexes to make room. The
former persisted **`ag2View`** toggle **retires as the source of truth** — the
Inspector's visibility is now derived from the route, so refresh and shared links
restore whichever occupant the URL names, and a bare URL means the rail is closed.

The hash stays client-only and is never sent to the gateway (a preview target must
not reach the server), matching how ADR 0008 keeps the Modal slot off the wire. Rail
**width** is genuine view-state — drag-resizable, clamped, persisted to
`localStorage`, and deliberately **not** in the URL.

## Considered options

- **Keep the preview as an in-memory store, like the Inspector was.** Rejected: it
  re-introduces exactly the store↔URL split ADR 0008 eliminated — a second source of
  truth that isn't deep-linkable, Back-dismissable, or refresh-survivable, and that
  drifts from the URL. Deriving the rail occupant from the route keeps one source of
  truth.
- **Fold the preview into the existing single Settings hash slot.** Rejected: a single
  slot is mutually exclusive, so a preview and Settings could never be open at once —
  yet the whole point of the docked rail is to read a file *while* adjusting a
  setting or continuing to chat. It would also conflate a **persistent docked rail**
  (a place your file stays open) with the **transient Modal layer** (dip-in/dip-out
  overlays), two different lifetimes that deserve separate keys.
- **Put the preview target in the path rather than the hash.** Rejected: a preview is
  a transient, client-only overlay orthogonal to the durable Page (profile/Tab/
  Thread), and the target path must not reach the gateway — both point to the hash,
  matching where ADR 0008 placed the Modal.
- **Keep `ag2View` persisted and mirror it into the URL.** Rejected: same
  two-sources-of-truth drift as the first option; the cross-session "remember
  Inspector on" preference is intentionally dropped in favour of URL-truth.

## Consequences

- A previewed file is deep-linkable and shareable; Back closes it (opening an aside
  pushes, switching/closing replaces, mirroring the Modal's rule); refresh restores
  it. The same is now true of the Inspector.
- Preview and Inspector can never both occupy the rail — enforced by the grammar
  (one value per `aside` key), not by runtime coordination. Opening a file replaces
  an open Inspector and vice versa.
- A preview and the Settings Modal can be open together (`#settings=…&aside=file:…`),
  and each closes independently; a path intent preserves the whole hash, an aside
  intent touches only the `aside` key, a Modal intent only the Modal key.
- The docked rail is shell navigation, not a Modal, so it no longer counts toward the
  "any modal open?" guard — the ⌘/Ctrl-1..9 profile shortcuts keep firing while a
  preview is open.
- A preview with **no addressable on-disk path** (a rare text-only, in-memory
  deliverable body) is the single documented exception: it opens transiently via the
  existing in-memory `viewer` store and is not URL-addressed. Deliverables are
  preferentially addressed by their real path so this case effectively never arises.
- The `ag2View` demo mode (Inspector + per-item provenance tags) is now URL-driven;
  the cross-session "remember Inspector on" preference is gone.

## Amendment — preview expand + split, per-viewing width (2026-07)

The "rail width is persisted view-state, one shared store" line above is refined for
the **file preview** occupant (the Inspector is unchanged):

- **Expand.** The preview gains a header toggle that expands it to fill the Thread
  column (spans `main` + `aside`; the drawer stays). This is **view-state, not
  navigation** — a `previewExpanded` boolean, `localStorage`-backed so it survives a
  refresh mid-view, but deliberately **not** in the URL: not deep-linkable, Back
  doesn't collapse it, shared links don't carry it (same category as width). The
  Thread stays mounted while collapsed to a 0-width column (clipped, not
  `display:none`) so a live stream + scroll position survive expand⇄collapse. The
  Inspector never expands.
- **Split width.** The preview gets its **own** width store (`previewWidth`), separate
  from the Inspector's `railWidth` — closing a preview no longer disturbs the
  Inspector's width and vice versa.
- **Per-viewing lifecycle.** For the preview only, width and expand are **reset on
  close** (`resetPreviewView`, fired from the component's unmount so any close path —
  × button, Back, switching the rail to the Inspector — is covered). Every fresh
  preview opens docked at the default; a mid-view refresh still restores them (a hard
  reload doesn't run the unmount hook). Grabbing the drag handle exits expanded (any
  drag sets a finite docked width). The Inspector's width stays sticky as before.

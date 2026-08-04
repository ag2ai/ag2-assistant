# URL is the source of truth for shell navigation

The app shell has three independent navigation dimensions — the active **Tab**
(Chats / Tasks / Files, in the Drawer), the open **Thread** (a Chat or Task in the
main pane), and the open **Modal** (Settings and friends). Previously only the Thread
lived in the URL; Tab and Modal state were in-memory stores, so neither was
deep-linkable, refresh-survivable, or dismissable with the browser's Back.

We make the **URL authoritative** for all three. The **path** carries the durable
location — profile, Tab, and the optional open Thread — as
`/app/{pid}/{tab}[/{c|t}/{id}]`. The Tab is an **outer** segment that is preserved
when a Thread opens or closes, and the Thread suffix is preserved when the Tab
switches: the two are **orthogonal** (opening the Files Tab must not close your open
Chat). The transient **Modal** layer lives in the URL **hash** as a single overlay
slot, client-side only and never sent to the gateway. The slot's vocabulary is
`#settings=<section>` and `#poweredby` (the AG2 architecture map, valueless); one
occupant at a time, and opening either evicts the other. The former `settingsOpen`
/ `settingsPage`-style stores become **derived** from the route, so the route is the
single source of truth.

## Considered options

- **Tab as a query param, or the Modal as a path segment.** Rejected: a Tab and a
  Thread are primary content and belong in the path hierarchy; a Modal is a transient
  overlay orthogonal to all content and belongs in the hash. Encoding either the
  other way implies a nesting that isn't real and forces base-preservation gymnastics.
- **Nest the Thread under the Tab such that switching Tabs closes the Thread.**
  Rejected: Tab and Thread are orthogonal; both are preserved independently.
- **Settings as a full route/page that replaces the main pane.** Rejected: Settings
  is dip-in/dip-out configuration layered over your work; a page throws away the
  underlying Thread/Tab context.
- **Keep Tab/Modal as in-memory stores synced to the URL.** Rejected: two sources of
  truth invite drift and store↔URL feedback loops.

## Consequences

- Back closes an open Modal and walks Tab/Thread history; refresh and shared links
  restore the full shell state.
- Modal state is invisible to the gateway (it is in the hash) — intended; the server
  routes the path, the client owns the overlay.
- The Modal slot is single and mutually exclusive, matching existing behaviour
  (opening one Modal closes another).
- Legacy `/app/{pid}/c/{id}` and `/t/{id}` URLs still resolve and are canonicalised
  to the Tab form on the next navigation.

---
status: accepted
date: 2026-10-06
---

# Card sources refresh Chat and file instances independently

For issue #122, a Card instance with a backend source requests fresh data when it
is shown, without an assistant Turn. This applies to both instances in Chat and
explicitly saved instance copies in Files. Each refresh targets its own object:
refreshing a file does not update the originating Chat, and refreshing the Chat
does not update its saved copies. This extends ADR 0043's initial passive preview
for source-backed instances while retaining its independent-copy guarantee, rather
than adopting issue #122's original shared-instance model.

The Card catalog is a reference for generation only. A generated instance retains
its own layout, source configuration, parameters and result validation contract;
refresh does not resolve these from the current reusable definition. Save instance
copies the instance's current state, including its source settings, into a separate
object. Editing or deleting the catalog definition does not change either object's
retained configuration. This preserves ADR 0043's definition independence when
instances gain backend sources.

For a custom source, retaining the Card-owned backend code is part of the
instance's independence: a mutable path into the catalog would leave a live
dependency on the generating definition. Save copies that retained code along
with the instance's other state. App data tools and the selected execution
environment remain runtime resources.

A composite rendered message can contain several source-backed Cards. Each
source updates only its own data and has its own refresh interval and error state;
refreshing one does not run the others. Save retains the whole rendered message
with all of its source settings, preserving ADR 0043's message-level copy boundary.

A successful refresh replaces the values managed by its source, including values
the user edited manually. Layout, source parameters and other sources' data remain
outside that update. Manual values do not form a persistent override layer over
fresh source results.

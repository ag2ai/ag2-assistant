# Screens

Screens are pages of Cards outside conversations. Open **Screens** in the sidebar
and select a dashboard. Ask the assistant to create one (for example, “make me a
morning dashboard with weather and AAPL”) or rearrange it (“move weather to the
top”). The **screens** Skill creates independent Card instance files and a Screen
that references them. The available sources remain those supported by Cards:
Weather, Quotes and approved custom code; other data needs a suitable Card source.

To rename a Screen, open its sidebar **⋮** menu and choose **Rename**. Enter or
leaving the input saves; Escape or an empty name cancels. The new title appears
in the sidebar and page header. Its file path, layout and Card references stay the same.

For a publication or monitoring dashboard, ask for a compact data table. The Card
author can keep ID, date, class, preview and metrics in separate columns, truncate
long previews, align numeric cells, and place toned percentage pills beside counts.
Summary and baseline rows share the columns of the records. The table follows the
app's theme and scrolls horizontally on narrow screens; its widths use design tokens.

A Screen file ends in `.screen.yaml` and lives anywhere in the Profile's Files:

```yaml
kind: screen
format_version: 1
title: Morning
layout:
  - id: root
    component: Column
    children: [weather, market]
  - id: weather
    component: CardInstance
    path: weather.card-instance.yaml
  - id: market
    component: CardInstance
    path: investments/market.card-instance.yaml
```

The layout uses the same primitives as Cards (`Row`, `Column`, `Text`, etc.).
`CardInstance` is a file reference with exactly `id`, `component` and `path`.
All paths are relative to the Profile's Files root, including references in a
Screen kept in a nested Directory. Screens do not copy values or consult current
catalog definitions: each referenced instance retains its own layout, values,
parameters, source contract and custom code. Up to 32 instances may be referenced.

The backend expands the entire page into one A2UI surface. Component ids and
absolute bindings are namespaced; instance data lives under `/_cards/<reference-id>`.
Relative bindings such as `./title` are permitted only inside repeated templates,
including Table cell templates. Invalid layouts, unsafe paths and missing files
produce repair errors; they never substitute a catalog Card or start an agent Turn.

Open the `.screen.yaml` source in **Files** to edit it. Reorder `children` to rearrange
Cards, nest them in `Row`/`Column` primitives, or reuse an existing instance path in
another Screen. Ordinary Files editing, ETag conflicts, rename and deletion apply.
Renaming an instance requires updating the paths that reference it. Invalid Screen
files remain listed with a repair indicator.

Source controls stay hidden during successful automatic refresh. They appear when
an update fails or a custom code version needs approval.
Source Refresh, approval and Secret binding use the same Profile-owned service as
saved-instance previews. Sources refresh once when the Screen is opened, including
Cards below the fold. Screens do not schedule interval updates. Automatic requests
for the same object share its declared interval with Chat references;
manual Refresh always requests an update. Concurrent requests coalesce. Responses
use typed source events and keep last-good values on failures. Card data remains
a snapshot until the page is reloaded; edits and updates from other views do not change it.
Fetching creates no assistant Turn. Authored configuration survives restart; fetched
file values use the existing bounded runtime cache and may return to the saved values
until refreshed after restart. Opening a Screen never executes unapproved code.

To show an existing instance in a conversation, ask the assistant to show that saved
file. Its `screens/show_instance` script adds an explicit file reference: it reads
current file state on replay and subscribes to the same source events. This differs
from ordinary drawn Chat Cards and **Save instance**, whose independent-copy behavior
is unchanged. A copied file remains a separate object even if its stored UUID matches.

External HTTP(S) links, such as publication IDs, open in a new browser tab without an
assistant Turn. Screen and referenced-file inputs/actions and in-app navigation links
remain passive. Source Refresh and approval are active buttons. Dragging Cards between
columns and writing values back without a Turn are follow-up work; Screens do not enable
historical actions.

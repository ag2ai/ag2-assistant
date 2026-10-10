# Visual inspection through screens

`run_skill_script(name="screens", script="preview", args={...})` is the only visual
preview script. It returns an image for the model and JSON diagnostics. It does not
save, draw a Chat Card, edit a draft, or refresh a source. Configure a Chromium
executable in Settings → General → Visual preview before using it.

Choose one target:

- Saved Screen or instance: `{"path":"english.screen.yaml"}` or
  `{"path":"calendar.card-instance.yaml"}`. Paths are relative to the Profile's Files root.
- Candidate Screen: `{"document":{"kind":"screen","format_version":1,"title":"...",
  "layout":[...]},"path":"english.screen.yaml"}`. Existing instance references are read
  from Files. Candidate documents are validated without being written.
- Candidate Card: `{"definition":{...},"data":{...}}`, using the same validated definition
  and current data as card-author. The reusable worked example is not current data.
- Chat draft: `{"draft_id":"draft-...","version":1}`. Omitting version uses its current
  version with current instance edits; it must belong to this Chat.

Add `width` (240–2560, default 960), `height` (240–2160, default 720), and `theme`
(`dark` or `light`, default `dark`). Width is the preview content viewport, excluding
application navigation. Screen padding and the 1280px content maximum match the app.
Inspect at least a desktop width and a narrow width, for example 960 and 360.

Use the image to assess readability, hierarchy and spacing. JSON reports measured
layout boxes by component id and repeated-template scope, horizontal scroll, escaped
parent bounds, page overflow, browser errors, and screenshot truncation. Images capture
up to 4096px of height. A horizontally scrollable Table is intentional; page overflow
or a frame escaping its parent calls for a layout correction. Diagnostics include up
to 500 boxes. Hidden elements have no box. `scroll_regions` reports the measured
inner viewport of Table and CalendarHeatmap, so their local scroll remains visible in
diagnostics even when their enclosing frame does not overflow.

External resources are blocked and listed in `blocked_resources`. Source scripts are
never run by preview; their retained values render as-is. Animations and interactive
controls are disabled. These limits can affect remote images and font appearance;
report them when they affect the design assessment. Repair schema errors first,
then preview again. Save or revise the intended target only after inspecting it.

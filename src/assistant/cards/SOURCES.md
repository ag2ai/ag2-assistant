# Card data and backend sources

This reference is available as `screens/sources.md`, `card-author/sources.md`, and
`rich-views/reference/sources.md` through `read_skill_resource`.

## Choose where values come from

A Card can keep values directly. Pass factual values in `rich-views` script arguments,
`card-author/draft_card`'s `data`, or `screens/save_instance`'s `fields`. The rendered
instance retains them in `message.data`; no external JSON file, source script or model
Turn is needed to display those retained values. Without a source they are a snapshot.
JSON, logs, SQLite or an API are inputs only when the requested data needs them.

For automatic updates, retain a backend source in the Card. A reusable `.card.yaml`
definition uses top-level `source` and `parameters`. An already saved
`.card-instance.yaml` uses `message.data._sources`. Inline Python `code` and optional
helper `files` travel with the instance; merely creating a standalone `.py` file does
not connect it. Attaching this metadata uses ordinary Card/file tools and requires
no coding agent, ACP bridge or new application handler.

Keep the three documents distinct:

- A definition has `fields` schemas, `required`, `layout`, an illustrative `example`,
  and optional `source` and parameter schemas. It is a reusable template.
- An instance retains actual values, expanded primitives, bound parameters and its
  source result contract. Changing the catalog definition does not update old instances.
- A Screen lays out `CardInstance` file references and ordinary primitives. Its layout
  does not inline Card values or source code. A plain section heading can be a `Text`.

## New source-backed definition

This illustrative definition reads a JSON object containing a string `summary`.
Choose the actual input path and obtain real initial values before creating the Card.
The file must be accessible in the Profile's selected skills runtime.

```yaml
name: FileSummary
description: A summary read from a selected JSON file.
fields:
  summary: {type: string}
required: [summary]
parameters:
  input_path: {type: string, default: /path/to/summary.json}
source:
  code: |
    import json
    import sys
    from pathlib import Path

    args = json.loads(sys.argv[1])
    document = json.loads(Path(args["path"]).read_text())
    print(json.dumps({"summary": document["summary"]}, ensure_ascii=False))
  args:
    path: {parameter: input_path}
  interval_seconds: null
layout:
  - {id: root, component: Card, child: summary}
  - {id: summary, component: Text, text: {path: /summary}}
example:
  summary: Illustrative summary, not current data.
```

For catalog Cards, pass chosen parameter values in `_parameters` alongside initial
fields to `rich-views/<CardName>` or `screens/save_instance`. For the example:
`{"summary":"<observed summary>","_parameters":{"input_path":"/actual/summary.json"}}`.
For a `card-author` draft, `data` contains the declared field values; choose parameter
defaults in the definition, or supply parameters through matching declared fields.
Do not add `_parameters` to draft data unless it is a declared field.

`source` declares exactly one of `tool` or `code`. The current tool sources are
`get_weather` and `get_quotes`; other data can use approved custom code.
`parameters` on a definition contains JSON schemas; on a retained source it contains
chosen values. `args` maps these values into the JSON passed in `sys.argv[1]`.
Code prints exactly one JSON object matching `fields` and `required`, without prose
or debugging output. A helper must be included in `source.files` under a safe relative
path; do not assume a neighbouring workspace `.py` file is copied into the runtime.

## Attach a source to an existing instance

Read the whole target instance and its expanded component ids before editing. Preserve
its envelope, including `instance_id`, `saved_at`, `save_request_id`, `request_hash`,
`message.surface_id`, `message.version`, `message.catalog_id`, `message.title` and
`message.intent`. Keep current factual values and layout bindings. Use ordinary Files
edits; save scripts create new files and never overwrite an existing instance.

The following is a `message.data` fragment for an instance whose root id is `root`
and whose Text reads `/summary`. It attaches the same source without replacing the
layout or needing a catalog change:

```yaml
summary: Current observed summary.
_sources:
  root:
    code: |
      import json
      import sys
      from pathlib import Path

      args = json.loads(sys.argv[1])
      document = json.loads(Path(args["path"]).read_text())
      print(json.dumps({"summary": document["summary"]}, ensure_ascii=False))
    parameters:
      input_path: /path/to/summary.json
    args:
      path: {parameter: input_path}
    fields:
      summary: {type: string}
    required: [summary]
    path: ""
    interval_seconds: null
```

The `_sources` key is an existing **component id**, not a data field or catalog name.
Expansion can turn definition id `title` into `root__title`; inspect actual saved ids.
The instance root is a stable anchor when present. Removing or renaming an anchor
requires updating the source key. Do not remove required `message.title` when removing
a visible title component. The source's `path` is a data target: `""` updates root
fields; `/_cards/<nested-card-id>` updates one nested Card, not a file or individual field.
Composite sources must own distinct targets. Returned keys must match the source's
`fields` contract and the values its retained layout reads.

Keep references to the same instance file when updating a Screen. Separate saved copies
remain independent. Editing the definition or drawing a new Chat Card alone does not
update the file already referenced by the Screen.

## Refresh and verify

Custom code runs through the Profile's selected skills sandbox after approval of its
exact code/helper-file version. Check input availability in that runtime: host paths
are not automatically accessible inside Docker. Keep code read-only when it only
fetches statistics. Declare `secret_names` when needed and let the Profile approval UI
bind Secrets; never put Secret values or consent records in a Card file.

Opening a Screen requests its sources once, including Cards below the fold; there is
no Screen interval polling. Manual Refresh requests an update. Chat/file previews can
refresh while visible at a configured interval. `interval_seconds: null` disables
interval polling, not the Screen's initial request. Refresh itself starts no model Turn.
Source failures retain last-good values. Successful results replace source-owned values;
manual edits to those values are not persistent overrides. File refresh results use a
runtime cache; they do not rewrite the authored instance's saved initial values.

Use `screens/preview` to validate bindings, source anchors and layout at desktop and
narrow widths. Preview renders retained values without executing sources: a successful
image proves neither input access nor a successful calculation. Preview `height` is
240–2160 and `width` is 240–2560; a taller page does not require a larger viewport.
After approval, check a real source refresh separately and compare its values with the
input and declared time window. Recalculate relative windows, such as the last three
months or twelve weeks, on every refresh. Missing measurements stay absent or `null`,
not invented zeroes. Keep illustrative design data out of real saved instances.

Report separately whether the source was attached, the layout preview passed, approval
was granted, and refresh was verified. A denied test or pending approval leaves execution
unverified; do not switch tools to bypass the denial or claim the dashboard is current.

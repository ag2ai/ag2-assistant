# Card sources

WeatherPanel and MarketBoard can refresh through `get_weather` and `get_quotes`
without an assistant Turn. The generating call supplies `_parameters`, for example
`{"location":"Moscow","units":"celsius"}` or `{"symbols":"AAPL,MSFT","title":"Watchlist"}`.
Parameters that cannot come from required displayed fields or declared defaults
are required in `_parameters`; missing choices reject drawing instead of dropping
the source. Worked examples can include `_parameters`.
Instances retain their parameters, result contract and expanded layout. Existing
source-less instances remain passive; opening old history never infers a source.

A reusable definition can declare parameter schemas and a source:

```yaml
parameters:
  location: {type: string}
source:
  tool: get_weather
  args:
    location: {parameter: location}
  interval_seconds: 600
```

Arguments can contain nested `{parameter: name}` references or literal JSON values.
The source returns a JSON object matching the definition's `fields` and `required`.
A composite message retains independently addressed sources under `data._sources`;
each one owns only its declared fields at its retained data path.

For a custom backend source, replace `tool` with inline `code`, optionally include
`files` mapping relative helper paths to their contents, and declare `secret_names`.
The application materializes these retained files as a temporary Skill with
`scripts/source.py`. That script receives one JSON argument in `sys.argv[1]` and
prints one JSON result. It runs through the existing Skill runtime and command
blocklist in the selected local subprocess or one-shot Docker sandbox. Docker's
configured image and network mode apply; unavailable Docker produces an error.
Execution is bounded to 30 seconds and 100 KB of JSON output.

The Review source button displays the retained code and helpers. Approve version
authorizes that executable hash in the current Profile; Decline withdraws it.
Copies of the same code reuse approval within that Profile. Editing code or helpers
requires new approval. Choose each requested Secret explicitly in this dialog.
References use stable Secret IDs; portable metadata carries no values or approval.
Selected keys are supplied only to that call, and raw keys are redacted from results.

Refresh runs when the Card enters the visible viewport of a visible tab, at its
declared interval while visible, or through its Refresh button. Scrolling offscreen,
hiding the tab or closing the view stops interval requests. Saved previews keep
other historical buttons and inputs inactive.

Save instance copies the complete message's currently displayed values and authored
source metadata. Chat and file copies refresh independently, including renamed and
byte-copied files. File refresh values are display-only and cached in the Profile
runtime: they survive reopening in that runtime, but are not written into the file
automatically or retained across restart. Authored configuration and explicitly
selected Secret references remain durable; ordinary file editing retains ETag checks.
Errors preserve available last-good values. Overlapping requests for the same object
and source coalesce; intervening authored edits, moves and deletions discard results.

Backend callers can use `gateway.card_sources.refresh(source_id, path=...)` or
`gateway.card_sources.refresh(source_id, chat_id=..., surface_id=...)`. The Profile
Gateway exposes the same operation at `POST /api/p/{pid}/card-sources/refresh`, and
approval at `POST /api/p/{pid}/card-sources/approval`. Acknowledgements contain typed
`CardSourceUpdated` events. Chat events also publish and persist on that Chat's AG2
stream; file events use a separate AG2 stream and the same event projection. Neither
operation invokes a model or creates a Turn. Scheduling remains follow-up #135.

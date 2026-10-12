# Native charts

Use the shared `Chart` primitive for line, grouped bar and stacked bar charts.
It draws directly from the Card's data in Chat and saved Screens. No generated image,
SVG file, JavaScript, CSS or external URL is needed. Read `vocabulary.md` first.

- `variant` is `line` or `bar`; `stacked: true` combines bar series at each category.
- `labels` is an ordered array of 1–366 non-empty strings, each at most 200 characters.
  Dates are categorical: include every intended period, in the intended order.
- `series` contains 1–8 objects: `{label, values, tone?}`. Each `values` array has exactly
  one finite number or `null` per label. Values lie between −1e12 and 1e12.
  `null` means missing: lines break and no bar segment is drawn. Zero is a measured zero.
  Never fill an unmeasured week with zero or interpolate it as a factual observation.
- Series automatically receive distinct theme colours. Optional `tone` takes `neutral`,
  `muted`, `accent`, `positive` or `negative`; never supply a colour or CSS length.
- `annotations` is an optional array of up to 8 `{index, label}` objects. An index lies
  between 0 and `labels.length - 1`; a fractional index puts a marker between categories.
  Use these for lesson starts, releases or other factual events.
- `labels`, `series`, `annotations`, `xLabel`, `yLabel`, `description` and `locale` may
  be literal or bound. The nested entries of arrays are plain data, not bindings.
- `xLabel` names the categories and `yLabel` includes the unit (e.g. errors per 100 words).
  `description` supplies the accessible chart title; put a visible heading in a `Text`.
  `locale: ru` localises numbers and controls; the default is English.
- `size: sm/md/lg` controls height; `md` is the default. `width: fill` fills the Card;
  narrow and dense charts scroll inside their frame. The numeric axis includes zero.
- The legend shows and hides series locally. `showLegend: false` hides it. Hover shows
  exact values, and the **Show data** button opens the full table, including missing data.
  These controls change the presentation only; they do not edit the retained data.

Declare data shapes in the Card's `fields` and supply current values separately to
`draft_card`. For example, this complete definition uses explicitly illustrative data:

```json
{
  "name": "WeeklyErrors",
  "description": "Weekly errors per 100 words with the lesson start marked",
  "fields": {
    "weeks": {"type": "array", "minItems": 1, "maxItems": 366, "items": {"type": "string", "minLength": 1, "maxLength": 200}},
    "series": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "properties": {
      "label": {"type": "string", "minLength": 1, "maxLength": 200},
      "values": {"type": "array", "minItems": 1, "maxItems": 366, "items": {"type": ["number", "null"], "minimum": -1000000000000, "maximum": 1000000000000}}
    }, "required": ["label", "values"], "additionalProperties": false}},
    "markers": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
      "index": {"type": "number", "minimum": 0}, "label": {"type": "string", "minLength": 1, "maxLength": 200}
    }, "required": ["index", "label"], "additionalProperties": false}}
  },
  "required": ["weeks", "series", "markers"],
  "layout": [
    {"id": "root", "component": "Column", "width": "fill", "children": ["heading", "chart", "note"]},
    {"id": "heading", "component": "Text", "text": "Example · weekly errors", "variant": "h3"},
    {"id": "chart", "component": "Chart", "variant": "bar", "stacked": true,
      "labels": {"path": "/weeks"}, "series": {"path": "/series"}, "annotations": {"path": "/markers"},
      "xLabel": "Week", "yLabel": "Errors per 100 words", "description": "Illustrative weekly errors",
      "size": "md", "width": "fill"},
    {"id": "note", "component": "Text", "text": "Illustrative values. Missing measurements remain blank.", "variant": "caption"}
  ],
  "example": {
    "weeks": ["17.08", "24.08", "31.08", "07.09", "14.09"],
    "series": [
      {"label": "Omitted articles", "values": [2.3, 2.1, null, 1.8, 1.2]},
      {"label": "Incorrect articles", "values": [1.1, 0.8, null, 0.5, 0.4]}
    ],
    "markers": [{"index": 2, "label": "Lessons started · 31.08"}]
  }
}
```

For line charts set `variant: line` and omit `stacked`. For grouped bars set
`variant: bar` and omit `stacked`. Explain missing periods and source limitations in a
caption. Gather real observations from the conversation and tools before drawing.

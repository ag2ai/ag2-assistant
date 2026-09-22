# The Card vocabulary

Every **Card** — Bundled, Global or Profile — declares its `layout` from the primitives and
the styling words listed here. A Card never carries a colour or a length: it names a tone, an
emphasis, a size or a gap, and the renderer resolves the name to a design token
([ADR 0028](../../../docs/adr/0028-a-cards-look-is-data-not-code.md)).

A value is either written out or **bound**: `{path: /title}` reads the data model,
`{path: ./day}` reads the item a repeated template is drawing, and `{path: .}` is that item
itself.

## Layout

| Component | Draws |
|---|---|
| `Card` | A framed block around one `child`. `variant: feature` gives it the editorial board frame and its own surface tones, and suppresses the generic chrome when it is the root. |
| `Column` / `Row` | Its `children` stacked or in a line. |
| `List` | Its `children`, as a column. |
| `Divider` | A horizontal rule. `emphasis: strong` makes it the heavy one. |

`children` is either a list of layout ids or one repeated template:
`{componentId: mover, path: /quotes, start: 1}` draws `mover` once per item of `/quotes`,
skipping the first `start` items — so a layout that already drew the lead on its own does not
draw it twice.

## Content

| Component | Properties |
|---|---|
| `Text` | `text`, `variant`, `emphasis`, `tone`, `format`, `map`. Text that resolves to nothing draws nothing. |
| `Metric` | A number and the movement behind it: `value`, `unit`, `label`, `delta` (absolute), `deltaPercent`, `size`, `align`, `tone`. It signs, groups and arrows the movement itself. |
| `Sparkline` | `values` — a normalised 0–100 series — plus `size` and `tone`. Fewer than two points keep the column at `sm`/`md` and draw nothing at `lg`. |
| `Link` | Its `child`, opening one of the app's own things or an external page. Exactly one target, and the first it names wins: `task`, `chat`, `file`, `folder` — or `url`, for a page on the web (http(s) only). A `task` or `chat` the app no longer lists, and a `url` on a scheme we will not follow, are drawn as the plain text the Link wraps; a path is taken as given. A target a row may not carry needs a `when` beside it, or every row draws the link's text. |
| `Icon`, `Image`, `Video` | As the Basic Catalog declares them; an `Icon` also takes a `size`, a `tone` and a `map`. |
| `Button`, `CheckBox`, `ChoicePicker`, `TextField`, `Slider`, `DateTimeInput` | As the Basic Catalog declares them. |

## Styling words

| Word | Values | On |
|---|---|---|
| `tone` | `neutral`, `muted`, `accent`, `positive`, `negative` — **or a binding**: a bound number takes its tone from its sign, so a rising value is green and a falling one red without a Card naming either, and a bound word carrying a `map` takes the tone that table names for it, so a status field colours its own badge. | `Text`, `Metric`, `Sparkline`, `Icon`, and a marked layout |
| `variant` | `h1`, `h2`, `h3`, `h4`, `body`, `caption`, `eyebrow`, `quote` — or `pill`, a chip rather than a step on the scale: a short label a Card sets beside others, taking its ink from its `tone`; or `badge`, the status mark, one uppercase word outlined in its own tone | `Text` |
| `variant` | `feature` | `Card` |
| `emphasis` | `strong` | `Text`, `Divider` |
| `size` | `sm`, `md`, `lg` | `Metric`, `Sparkline`, `Icon` |
| `format` | `time`, `ago`, `datetime` — a timestamp written the way the rest of the app writes one | `Text` |
| `map` | a table of value → name, so a Card prints `Market open` for a field that carries `open`, draws a `check` for one that carries `done`, or tones a row from the word its status field holds. A value the table does not name is taken as it is. | `Text`, `Icon`, a bound `tone` |
| `gap` | `none`, `xs`, `sm`, `md`, `lg` | `Column`, `Row`, `List` |
| `align` | `start`, `center`, `end`, `stretch` | `Column`, `Row`, `List`; `Metric` takes `start`/`end` |
| `justify` | `start`, `center`, `end`, `between` | `Column`, `Row`, `List` |
| `grow` | `true` — take the room the row has left over | any component |
| `marker` | `start` — a rule down the leading edge, drawn in the component's `tone`. **Or a binding**, read like `when`: the rule is drawn only for the rows that value is there for, so one repeated row can mark the event that is up next. Any other word marks it too — there is one edge. | `Column`, `Row`, `List`, `Card` |
| `when` | a binding — the component is drawn only when that value is there, so a heading never stands over an empty list. A list of bindings is drawn for when any one of them is there. `false` and an empty array count as absent; zero does not. | any component |

A look nobody can express is a missing primitive, not a reason to write a colour into a Card:
raise it, and adding it serves every Card at once.

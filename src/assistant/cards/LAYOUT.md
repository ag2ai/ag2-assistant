# Sizing and responsive columns

Choose the box whose size you are changing. A Column's `align` controls its children;
changing alignment inside a Card does not change the enclosing Card's size.

- `width: content` fits the component to its contents, including normal frame padding.
  It takes precedence over `grow: true` and remains bounded by its container.
- `width: fill` fills the available container. For a table filling a Card, set it on
  the Table, not only the Card or the Column around the table.
- Omit `width` to preserve existing behavior. These modes work on Card, Column, Row,
  Grid, List, Table and CalendarHeatmap; they are semantic words, never CSS lengths.
- `Grid` with `columns: 2` gives equal halves. Two `grow: true` children share leftover
  space but retain different content widths, so they do not guarantee equal halves.
- Grid accepts 1–6 columns. `minColumnWidth: sm/md/lg` selects a readable minimum;
  tracks collapse to fewer equal columns when the container is narrow. Empty tracks
  remain, so one child can occupy a half-width slot. No empty spacer is necessary.

A content-sized calendar frame alongside a full-sized summary:

```yaml
- {id: root, component: Grid, columns: 2, minColumnWidth: md, gap: md, width: fill, children: [calendar, summary]}
- {id: calendar, component: Card, width: content, child: table}
- id: table
  component: Table
  width: content
  variant: data
  density: compact
  columns: {path: /weeks}
  rows: {path: /days}
  cells: {path: ./cells}
  header: week
  cell: day
  columnWidth: narrow
  columnAlign: center
- {id: week, component: Text, text: {path: ./label}, variant: caption}
- {id: day, component: Text, text: {path: .}, variant: caption}
- {id: summary, component: Card, width: fill, child: summary_text}
- {id: summary_text, component: Text, text: {path: /summary}}
```

For a Screen, a Grid can instead reference two retained `CardInstance` nodes. Set
instance frame sizing in the retained instance's own layout. Paths remain relative
to the Profile's Files root.

Table widths still use narrow/regular/wide tokens. Compact density changes cell
padding, not the minimum column width. A dense activity heatmap needs a dedicated
CalendarHeatmap primitive rather than increasingly small table labels. Read heatmap.md.

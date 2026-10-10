# Compact activity calendars

Use `CalendarHeatmap` for a GitHub-like calendar of small activity squares. Keep Table
for readable records and tabular comparisons. Heatmap uses fixed square sizes independent
of Table's column widths and can fit inside a content-sized Card.

```yaml
- {id: root, component: Card, width: content, child: body}
- {id: body, component: Column, align: start, gap: sm, children: [title, calendar]}
- {id: title, component: Text, text: "Английский · октябрь 2026", variant: h3}
- id: calendar
  component: CalendarHeatmap
  startDate: {path: /start}
  endDate: {path: /end}
  days: {path: /days}
  cellSize: md
  weekStartsOn: monday
  showLegend: true
  locale: ru
```

Each `days` entry is one unique ISO date, not one session:

```json
{
  "start": "2026-10-01",
  "end": "2026-10-31",
  "days": [
    {"date":"2026-10-04","count":1,"label":"4 октября: одно занятие"},
    {"date":"2026-10-06","count":3,"label":"6 октября: три занятия"},
    {"date":"2026-10-08","status":"analysis","label":"8 октября: только анализ"},
    {"date":"2026-10-10","status":"pause","label":"10 октября: подтверждённая пауза"},
    {"date":"2026-10-11","status":"upcoming"}
  ]
}
```

This is illustrative data. Obtain actual dates and counts from the user's logs or source.
Aggregate sessions by date before rendering. Missing dates mean **unknown**, never proof
of a missed session. Mark a known future day `upcoming`, a confirmed break `pause`, and
an explicitly known missed day `missed`. Do not infer these states from missing files.
A present count of zero defaults to missed; omit count when the data is unknown. An entry
with neither count nor status remains unknown. Explicit status overrides count-derived
status. Analysis is distinct from activity; it does not count as a session.

Properties:

- `startDate` and `endDate`: inclusive ISO dates (`YYYY-MM-DD`), literal or bound. The
  range contains 1–366 days. Invalid dates, reversed ranges and larger ranges report an error.
- `days`: a literal array or binding. Up to 3660 entries; dates outside the displayed
  range are ignored. Entries accept only `date`, `count`, `status`, `label`.
- `date`: required ISO date. Duplicate dates within the displayed range report an error.
- `count`: optional non-negative integer up to 1,000,000. Positive counts imply activity;
  1, 2, 3 and 4+ use increasing intensities. More sessions change intensity, not cell count.
- `status`: optional `activity`, `analysis`, `pause`, `missed`, `unknown`, or `upcoming`.
- `label`: optional text (up to 500 characters) for the native tooltip and accessible label.
- `cellSize`: `sm`, `md` (default), or `lg`. Never provide CSS lengths.
- `weekStartsOn`: `monday` (default) or `sunday`. Rows are weekdays; columns are weeks.
- `showLegend`: defaults true; false gives the smallest calendar. The optional legend
  explains statuses. Missing and out-of-range dates have different visual states.
- `locale`: literal or bound BCP 47 language tag for dates and weekday/month labels.
  Status labels support English and Russian. Invalid locale tags fall back to English.
- `width`: `content` (default behavior) or `fill` for its outer frame.

Declare the same dated entry keys, enum statuses, bounded counts, required date, and
`additionalProperties: false` in the Card's field schemas. Keep `example` compatible.
Month labels are compact; full dates remain in each cell's tooltip and accessible label.
Wide ranges scroll inside the calendar rather than widening the Card or page.

Inspect the design using `screens` → `preview` at desktop and narrow widths before saving.
An attached source uses the normal retained-data contract: the calendar itself never
reads disk, launches a task, or schedules refreshes.

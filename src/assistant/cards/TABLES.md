# Compact data tables

Use `variant: data` for publication, analytics and monitoring tables. Keep each field in
its own column, give a long preview `wide` + `ellipsis`, and use `end` + `nowrap` for
numeric cells. Choose `density: compact` when the user asks for many single-line records.
On a narrow view the table scrolls; never merge ID and preview to make it fit.

Different widths require both `Table.columnWidth: {"path": "./width"}` and a `width`
token on each item in `columns` (for example `{"name": "PREVIEW", "width": "wide"}`).
Adding metadata to the data alone does not configure the Table. The available tokens are
`narrow`, `regular` and `wide`. Read this current reference when revising an older table;
update its component properties as well as its data. Independent `Row` components do not
share column widths, and `grow` cannot align cells across rows. Use a `Row` inside a Table
cell only to compose that cell's contents.

Widths and tones are renderer tokens, not pixels or arbitrary colours. `columnWidth`,
`columnAlign` and `columnOverflow` read the column item; `rowVariant` reads the row item.
Set `summary` for aggregate rows and `baseline` for comparison-period rows. Supply those
rows after the records, with the same cell positions. Missing cells draw a dash; an empty
object reserves a blank cell. A `Text` pill can sit beside a count in a `Row`; supply its
`tone` from actual thresholds or an agreed baseline, not from invented performance claims.

The following is a complete **illustrative** definition for `draft_card`. Pass real values
separately in `data`; retain the labelled example in `definition.example`. Keep URLs real
when using user data and omit links if none are known. Tables use the app's current theme.
For a dashboard use the Screens skill to save independent instances and a Screen on request.

```json
{
  "name": "PublicationMetrics",
  "description": "A compact publication metrics table with highlighted rates.",
  "fields": {
    "title": {
      "type": "string"
    },
    "columns": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "rows": {
      "type": "array",
      "items": {
        "type": "object"
      }
    }
  },
  "required": [
    "title",
    "columns",
    "rows"
  ],
  "layout": [
    {
      "id": "root",
      "component": "Card",
      "variant": "feature",
      "child": "body"
    },
    {
      "id": "body",
      "component": "Column",
      "gap": "sm",
      "children": [
        "title",
        "table"
      ]
    },
    {
      "id": "title",
      "component": "Text",
      "variant": "h3",
      "text": {
        "path": "/title"
      }
    },
    {
      "id": "table",
      "component": "Table",
      "variant": "data",
      "density": "compact",
      "columns": {
        "path": "/columns"
      },
      "rows": {
        "path": "/rows"
      },
      "cells": {
        "path": "./cells"
      },
      "header": "header",
      "cell": "cell",
      "columnWidth": {
        "path": "./width"
      },
      "columnAlign": {
        "path": "./align"
      },
      "columnOverflow": {
        "path": "./overflow"
      },
      "rowVariant": {
        "path": "./variant"
      }
    },
    {
      "id": "header",
      "component": "Text",
      "variant": "caption",
      "text": {
        "path": "./label"
      }
    },
    {
      "id": "cell",
      "component": "Row",
      "gap": "xs",
      "align": "center",
      "children": [
        "link",
        "text",
        "pill"
      ]
    },
    {
      "id": "link",
      "component": "Link",
      "url": {
        "path": "./url"
      },
      "when": {
        "path": "./url"
      },
      "child": "linkText"
    },
    {
      "id": "linkText",
      "component": "Text",
      "text": {
        "path": "./linkText"
      }
    },
    {
      "id": "text",
      "component": "Text",
      "text": {
        "path": "./text"
      }
    },
    {
      "id": "pill",
      "component": "Text",
      "variant": "pill",
      "text": {
        "path": "./pill"
      },
      "tone": {
        "path": "./tone"
      }
    }
  ],
  "example": {
    "title": "Recently published — illustrative example",
    "columns": [
      {
        "label": "POST",
        "width": "narrow",
        "overflow": "nowrap"
      },
      {
        "label": "DATE",
        "width": "narrow",
        "overflow": "nowrap"
      },
      {
        "label": "CLASS",
        "width": "regular",
        "overflow": "nowrap"
      },
      {
        "label": "PREVIEW",
        "width": "wide",
        "overflow": "ellipsis"
      },
      {
        "label": "VIEWS",
        "width": "narrow",
        "align": "end",
        "overflow": "nowrap"
      },
      {
        "label": "FWD",
        "width": "regular",
        "align": "end",
        "overflow": "nowrap"
      },
      {
        "label": "REACT",
        "width": "regular",
        "align": "end",
        "overflow": "nowrap"
      },
      {
        "label": "COMMENTS",
        "width": "regular",
        "align": "end",
        "overflow": "nowrap"
      }
    ],
    "rows": [
      {
        "cells": [
          {
            "linkText": "#42",
            "url": "https://example.com/posts/42"
          },
          {
            "text": "Oct 7"
          },
          {
            "pill": "release"
          },
          {
            "text": "A deliberately long publication preview that stays on one line instead of making every row tall"
          },
          {
            "text": "1,020"
          },
          {
            "text": "12",
            "pill": "1.18%",
            "tone": "muted"
          },
          {
            "text": "31",
            "pill": "3.04%",
            "tone": "positive"
          },
          {
            "text": "105 (18)",
            "pill": "10.3%",
            "tone": "positive"
          }
        ]
      },
      {
        "cells": [
          {
            "linkText": "#41",
            "url": "https://example.com/posts/41"
          },
          {
            "text": "Oct 6"
          },
          {
            "pill": "poll"
          },
          {
            "text": "Which workflow would you like to see next?"
          },
          {
            "text": "1,360"
          },
          {
            "text": "1",
            "pill": "0.07%",
            "tone": "muted"
          },
          {
            "text": "4",
            "pill": "0.29%",
            "tone": "muted"
          },
          {
            "text": "28 (12)",
            "pill": "2.06%",
            "tone": "positive"
          }
        ]
      },
      {
        "variant": "summary",
        "cells": [
          {
            "text": "Avg"
          },
          {},
          {},
          {
            "text": "2 posts"
          },
          {
            "text": "1,190"
          },
          {
            "text": "6.5",
            "pill": "0.55%"
          },
          {
            "text": "17.5",
            "pill": "1.47%"
          },
          {
            "text": "66.5",
            "pill": "5.59%"
          }
        ]
      },
      {
        "variant": "baseline",
        "cells": [
          {
            "text": "Base"
          },
          {},
          {},
          {
            "text": "Previous period"
          },
          {
            "text": "970"
          },
          {
            "text": "10.6",
            "pill": "1.09%"
          },
          {
            "text": "18.5",
            "pill": "1.91%"
          },
          {
            "text": "23.1",
            "pill": "2.38%"
          }
        ]
      }
    ]
  }
}
```

# Browser layout checks

The fixtures mount the real Svelte renderer and theme CSS. They check narrow and wide
tables in 280px and 620px frames, all four cross-axis alignments, and nested Columns,
Rows, Cards and Lists. Wide tables must scroll to their last column without overflowing
any ancestor. Short tables must fit without scrolling and share a Row when space allows.
Sizing fixtures check equal Grid tracks and content/fill modes. Calendar fixtures cover
compact monthly and scrollable annual ranges, three square sizes, English/Russian labels,
accessible tooltips and distinct unknown/analysis/activity states. Surface fixtures check
ordinary, feature and unframed cards without generic chrome, editable/read-only bindings,
save icon placement/tooltips/keyboard focus, definition dropdown defaults/dismissal/focus
restoration, current versus obsolete drafts, and the composing placeholder on narrow and wide chat layouts.

Start Vite with `npm --prefix web run dev -- --host 127.0.0.1 --port 5186`, then launch
a **dedicated** Chrome with `--remote-debugging-port=9360` and a temporary
`--user-data-dir`. The checker navigates that browser's first page:

```sh
npm --prefix web run test:layout -- http://127.0.0.1:9360 http://127.0.0.1:5186/app
```

Open `/app/browser/layout.html` on the Vite server to inspect the fixtures visually.
Pass `charts` after the Vite URL for native chart fixtures covering themes, sizing,
missing values, event markers, legend controls and accessible data tables.
Pass `thinking <profile-id>` to mount the real Thread and check activity through tool
rounds, aggregated run labels, counted repeated details and hover/focus/tap lists, replay, final replies, cancellation
and compaction. Long run lists must scroll without escaping the viewport. Written files expose inline
Open/Download icons within their run list, without an extra timeline badge.
Tools with no meaningful parameters retain their counted chip without an empty popover. It reads settings
from the running gateway through Vite's API proxy; it sends no chat messages.
These fixtures are development files. Production ships the app entry and a passive
preview entry used by the screens skill.

# Browser layout checks

The fixtures mount the real Svelte renderer and theme CSS. They check narrow and wide
tables in 280px and 620px frames, all four cross-axis alignments, and nested Columns,
Rows, Cards and Lists. Wide tables must scroll to their last column without overflowing
any ancestor. Short tables must fit without scrolling and share a Row when space allows.

Start Vite with `npm --prefix web run dev -- --host 127.0.0.1 --port 5186`, then launch
a **dedicated** Chrome with `--remote-debugging-port=9360` and a temporary
`--user-data-dir`. The checker navigates that browser's first page:

```sh
npm --prefix web run test:layout -- http://127.0.0.1:9360 http://127.0.0.1:5186/app
```

Open `/app/browser/layout.html` on the Vite server to inspect the fixtures visually.
These fixtures are development files. Production ships the app entry and a passive
preview entry used by the screens skill.

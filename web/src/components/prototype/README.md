# Sidebar activity rail — throwaway UI prototype

Question: can an icon-only activity rail plus a separate work panel make Chats,
Tasks, Files, and Screens easier to navigate than the current four-way switch?

Based on PR #151. All three variants live on the existing app routes and use
the running gateway on port 8878 for real, read-only profile data.

Run from this branch:

```sh
npm --prefix web run prototype:sidebar
```

Open `http://127.0.0.1:8888/app/profile-2/screens/s/english-demo.screen.yaml?variant=A`.
The floating bottom bar and left/right arrow keys cycle between:

- A — VS Code: persistent activity rail, profile at the top of a dense work panel,
  section title, creation row, and plain list.
- B — Compact workspace: profile avatar in the rail, large section title and
  search, descriptive rows, creation action and profile switcher at the bottom.
- C — Collapsible explorer: centered activity icons and a wider work panel with
  screen tiles. Click the active section or the collapse button to reclaim space.

Creation opens an in-memory draft. Files open a read-only excerpt in the panel.
The main page supplies visual context; its controls are inert. The prototype
server accepts only read requests and replay streams. Variant choice lives in
the URL; draft, filter, panel, and menu state lives in memory. Full state is
printed to the browser console after interactions and variant switches.

The switcher and layouts are development-only. No winner has been selected yet.
Keep this branch as a primary source; rewrite the chosen behavior for production
after the design decision is recorded in the implementation issue.

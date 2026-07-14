# Single hidden root directory; the visible Documents workspace is retired

All persistent state — global config, secrets, skills, and every profile's
config, skills, databases, and working files — lives under one root
(`~/.ag2assistant/`, overridable via `--data-dir` / `AG2ASSISTANT_DATA_DIR`).
The agent's working file space is `profiles/<id>/files/` inside that root; the
previous visible workspace level (`~/Documents/AG2 Assistant/<Profile>/`) is
removed entirely, and the GUI Files browser becomes the primary window onto the
agent's files.

## Considered Options

- **Move all state into `~/Documents/AG2 Assistant/`** — rejected: Documents is
  often iCloud-synced (secrets and live SQLite databases must not sync), and
  state inside the agent's own write sandbox would let the agent and the GUI
  Files browser read, list, and delete settings, memory, and keys.
- **Keep the three-level split (data dir / workspace / project folder)** —
  rejected: one root is a simpler mental model; read-only sources (the project
  folder) will be reintroduced later as a separate layer.
- **Keep `files/` path configurable per profile** — rejected: the level is
  removed completely, not made optional (`AG2ASSISTANT_WORKSPACE` is retired;
  Docker needs only the `/data` mount).

## Consequences

- Existing `~/Documents/AG2 Assistant/<Profile>/` folders become untouched
  archives; the app never reads or migrates them, so old deliverable links in
  task history go stale (accepted).
- Agent output is no longer visible in Finder by default.

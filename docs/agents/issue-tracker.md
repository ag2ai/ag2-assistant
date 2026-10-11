# Issue tracker: GitHub

GitHub Issues in `ag2ai/ag2-assistant` are the source of truth for tasks and specs
(you may know a spec as a PRD). Publish new issues, specifications, triage state
and discussion on GitHub.

## Local work buffers

- `.scratch/` is local-only and git ignored. Use it only for tasks currently being
  worked on, never as a backlog or a permanent issue archive.
- Keep one feature per directory: `.scratch/<feature-slug>/`.
- Temporary specs may use `spec.md`; temporary ticket files may use
  `issues/<NN>-<slug>.md`, numbered from `01`, with a link to their GitHub issue.
- Local notes and status are working buffers; GitHub holds the authoritative
  issue description, labels and discussion (see `triage-labels.md`).
- After the work is merged and the GitHub issue is closed, delete its local
  copies and completed work buffers.

## When a skill says "publish to the issue tracker"

Create or update the issue on GitHub. A local draft alone does not publish an issue.

## When a skill says "fetch the relevant ticket"

Read the current issue and its relevant comments from GitHub using its number or
URL. A local buffer may help navigate active work, but does not replace fetching
the authoritative ticket.

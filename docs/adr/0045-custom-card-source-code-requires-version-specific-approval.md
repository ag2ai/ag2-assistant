---
status: accepted
date: 2026-10-06
---

# Custom Card source code requires version-specific approval

For issue #122, custom Card source code needs explicit user approval before its
first automatic execution. Approval covers one version of the code: subsequent
openings and refreshes may execute it automatically, but a code change requires
renewed approval. Card sources can run repeatedly when shown without an assistant
Turn, so version-specific approval establishes consent for that automatic execution
while the existing skills runtime continues to provide its blocklist and selected
sandbox boundary.

Approval is scoped to one Profile and one code version. Saving or copying an
instance with unchanged code within that Profile reuses its approval; moving a
copy to another Profile requires approval there. The portable instance carries
its source configuration, not the originating Profile's authority to execute it.

Custom sources use the current execution environment's network settings, including
the selected Docker network mode. Issue #122 introduces no separate per-Card host
allow-list. An unavailable network leaves the last available values intact and
reports an update error.

The user explicitly selects the Secrets supplied to a custom source, optionally
as part of its first execution approval. Declaring key names in Card code or
configuration alone grants no access. Sources use the existing Secret store,
extending Secrets to Card data-provider keys as well as model keys. Each call
receives only its selected keys for that execution; instance copies retain
references, without embedding key values or another Profile's authorization.

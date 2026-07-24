# Secrets absorb both per-config keys and shared provider keys

API keys used to exist in two unrelated shapes: an install-wide key per known
provider (`openai`/`gemini`/`anthropic` in `secrets.json`, loaded into `os.environ`)
and a private key welded to exactly one Text/Live model config (`llm_keys` /
`live_keys`, keyed by config id). Nothing in between — reusing one key across two
model configs meant pasting it twice. We replaced *both* with a single entity: the
**Secret** — a named, reusable, value-unique key `{name, value, provider?}` that any
number of Text and Live models reference by id. The old shapes survive as degenerate
cases: a per-config key is a Secret only one model references; a shared provider key
is a provider-tagged Secret marked **Default** (at most one per provider tag), which
keeps the env-loading role for the plumbing that reads `os.environ` (image gen,
voice providers).

## Considered Options

- **Absorption with a Default designation (chosen).** One entity, one Settings
  surface, one resolution path: referenced Secret → (custom `base_url` with no
  Secret? placeholder) → provider Default → env var → none.
- **Secrets alongside the existing mechanisms.** Rejected — a model would have had
  three competing key sources, and `key_source` labelling, the model form, and the
  resolution order would each grow a third branch to maintain forever.
- **Hard provider scoping** (a Secret usable only by configs of its provider).
  Rejected — kills keys for custom/unknown OpenAI-compatible endpoints (MiniMax,
  vLLM), which are exactly the configs that most need a per-key identity. The
  provider tag is therefore *soft*: it groups and sorts, never forbids; its only
  hard meaning is Default eligibility.

## Consequences

- Secrets are **unique by value**: pasting an already-stored key in the model form
  snaps to the existing Secret; an explicit add with a duplicate value is rejected
  with a pointer to it. Migration dedupes by value, the shared-provider-key
  survivor keeping the tag and Default badge.
- Deleting a Secret is always allowed, even while referenced — dependents degrade
  down the resolution order and the health dot / `key_source` report it. This
  mirrors the store's existing delete-the-active-config behavior.
- A key set only in the real environment (`.env`) remains the last-resort fallback,
  below the Default — Docker/compose workflows keep working with no Secret at all.
- Channel bot tokens and the GitHub token are deliberately **not** Secrets: they
  are env-var-keyed singletons with no reuse story, and they keep their existing
  storage and endpoints.

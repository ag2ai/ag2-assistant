# A Base URL is what makes an endpoint custom; only a custom endpoint picks its API interface

The model editor offered one flat **Type** select carrying every configuration the app
supports, on every model, always. That select conflated four independent facts — the
vendor, whether the endpoint was the vendor's own or a third party's, which API surface
was spoken, and how credentials were obtained — and offered all of them as one open
choice. The result was an editor that invited a user looking at a first-party GPT
configuration to change its Type to Ollama, and that made "OpenAI-compatible" and
"OpenAI · Responses" read as siblings when one names a *deployment* and the other a
*wire*.

**The presence of a Base URL is the whole of the distinction.** A Text model that names
its own endpoint is a **Custom endpoint** and may choose its **API interface** (OpenAI ·
Responses, OpenAI · Chat Completions, Anthropic); a Text model that reaches its vendor
has one settled surface and the control is *hidden outright*, not disabled. There is no
new field, no persisted origin flag, and no memory of which **Template** created a
model — the discriminator is data the model already carried. Ollama is excluded: its
address is a different field and admits no interface choice.

**We paid a capability for it.** First-party OpenAI is now Responses-only: `type:
openai` with an empty Base URL is no longer reachable from the editor. Chat Completions
against OpenAI itself requires setting the Base URL to `https://api.openai.com/v1`
explicitly, which is the honest reading anyway — the caller is choosing a surface, so
they name the endpoint that serves it. Clearing a Base URL therefore *snaps*
`openai → openai_responses` rather than leaving an un-editable state stranded behind a
hidden control. Both OpenAI surfaces share a model catalogue, so the snap never discards
the model name.

**The rule is UI policy, not a domain invariant.** The store stays permissive: every
`(type, base_url)` pair it accepted before is still valid and still derives to a
meaningful provider config. Nothing migrates, the HTTP API does not break, hand-edited
`config.yaml` entries keep working, and a configuration saved before this change renders
under the new rule without being rewritten. The rule constrains what is *offerable*, and
lives at one seam in the web layer.

## Considered options

- **A persisted origin flag** (which Template made this model) — rejected: it would make
  a **Template** something that survives creation, contradicting its definition as
  prefill with no lifecycle, and it would leave the vendor/deployment conflation intact.
- **Splitting the stored type into vendor + interface axes** — rejected as too large for
  the problem: it forces a migration of every stored configuration and a rewrite of the
  derivation table, to express a constraint the existing fields already imply.
- **Filtering the unlocked select to same-vendor siblings only** — rejected: repointing
  a compatible entry from an OpenAI-wire server at an Anthropic-wire one is a real thing
  users do, and forbidding it would mean deleting and recreating the configuration.

## Consequences

`TYPE_LABEL.openai` becomes "OpenAI · Chat Completions" everywhere, including saved-model
card subtitles and the composer's model switcher — a compatible entry that read
"OpenAI-compatible · mistral-7b" now reads "OpenAI · Chat Completions · mistral-7b". The
string "OpenAI-compatible" survives only as a Template name, which is the one place it
was ever accurate. The Base URL field becomes the sole discoverability path for the
interface choice, so its help text has to carry that meaning.

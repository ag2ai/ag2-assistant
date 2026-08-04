# A pasted key goes to the provider, never to us

The model editor asks the provider what models it offers, so the **Model** field can
suggest real names instead of a placeholder. That probe needs credentials, and the
editor holds two kinds: a **Secret reference** (a `secret_id` — the raw value never
leaves the store) and a key you just pasted into the form and have not saved yet. The
obvious implementation routes both through one gateway route, exactly as the draft
**Test** button already does — `api_key` rides the test payload today, and the save path
deletes it afterwards because a **Secret** is what actually persists the key.

**We split the probe by where the key came from.** A model authenticated by a saved
Secret is probed by the gateway, because it must be: the frontend holds only the
Secret's last-4 hint, and a custom endpoint or Ollama host may be reachable from the
gateway and not from the browser. A key that has only been *pasted* is probed by the
**browser, calling the provider directly** — that string never reaches our backend,
our logs, or our process memory until the user commits to creating a Secret from it.
No token crosses a boundary it did not have to cross to answer the question asked.

**The distinction is consent, not secrecy.** Test *does* post a pasted key to the
gateway, ten lines away in the same file, and that stays right: pressing Test is an
explicit act of trying a credential. Autocompletion fires on focus — a background
request the user did not ask for by name — and a background request is not the place to
widen a token's blast radius. The rule is about which requests the user authored, and
it is why a frontend provider-fetch layer exists at all next to a perfectly good
backend route.

## Considered options

- **One backend route for both** — rejected: it is the plausible-looking simplification
  this ADR exists to forbid. It sends an unsaved token to our process on form focus, and
  it would have cost pre-save discovery nothing only because the token was surrendered.
- **No live list until the model is saved** (backend-only, pasted keys never probed) —
  rejected as honouring the rule while defeating the feature: first-run *is* the
  paste-a-key path, so the case with no suggestions would have been the common one.
- **Browser-direct for everything** — impossible. Saved Secret values are never served
  to any API caller (`secrets.py`), so the browser cannot authenticate that probe.

## Consequences

Two probe paths must be maintained, and the browser one carries per-provider quirks
that the server path does not: Anthropic requires an
`anthropic-dangerous-direct-browser-access` header, Gemini takes the key in the query
string, and Ollama answers only for origins its own configuration allows. A custom
`base_url` endpoint typically sends no CORS headers at all, so the browser path fails
there and falls back to **Known models** — and because a CORS refusal is
indistinguishable from a dead host in a browser, that failure reads as `unreachable`
rather than `no_list_endpoint`, which is less precise than the gateway path can be
about the same endpoint. The gateway probe route is therefore *not* the general answer
it looks like: it authenticates by `secret_id` only, and accepts no key material.

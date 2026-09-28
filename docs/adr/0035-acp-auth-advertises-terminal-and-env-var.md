# ACP auth advertises terminal + env_var, never an OAuth flow

The served agent's `authMethods` are `terminal` and `env_var`. There is nothing to "sign in" to:
AG2 Assistant has no account system — it is a local shell over the owner's own provider keys —
so the Registry's Agent-Auth model (agent-run OAuth against a vendor) has no honest referent
here. `terminal` + `env_var` is the established BYO-key pattern in the ACP Registry (OpenCode
and Kilo declare `terminal`; `env_var` appears only ever alongside a gate-satisfying method),
and `terminal` satisfies the Registry's listing gate.

`agent` is never advertised. The only OAuth flow in the codebase with a baked-in client id is
`codex_auth.py`'s — which impersonates OpenAI's own Codex CLI and, per its own docstring, likely
violates OpenAI's Terms of Service. It exists for the owner's private use of their subscription,
and must never be the advertised sign-in of a publicly listed agent.

Because upstream tracks "has the wire `authenticate` been called on this connection" rather than
"do credentials exist", a configured `AuthProvider` deadlocks terminal-style auth (which
completes out of band, by design). Until the credential-state probe ships upstream, the listener
chooses at construction: credentials resolvable for the bound Profile ⇒ `auth=None` (sessions
ungated; methods not advertised — the accepted trade); unconfigured ⇒ the provider (methods
advertised honestly, sessions answer `auth_required` until setup completes and the listener
restarts). The restart wrinkle is exactly what the upstream probe removes.

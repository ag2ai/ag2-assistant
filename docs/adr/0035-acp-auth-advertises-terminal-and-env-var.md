# ACP auth advertises terminal + env_var, never an OAuth flow

*(Amended — see "Amendment: `env_var` left the stable schema" below.)*

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

## Amendment: `env_var` left the stable schema

ACP v1 (`agent-client-protocol` 1.0) keeps only `terminal` and `agent` as stable auth methods;
`EnvVarAuthMethod` is gone. The listener now advertises `terminal` alone. `terminal` was already
the method that satisfies the Registry's listing gate, so the gate is unaffected; `agent` stays
unadvertised.

The provider-key route is not carried over. Whether a key unlocks the listener depends on the
bound Profile's provider (and an OpenAI subscription Profile reads no key at all), and a cold
start has no Profile to read a key for, so no single env-var list is true. `terminal`'s
description points at the one path that always completes setup: `ag2-assistant gateway` and its
browser onboarding (profile, provider and key), then a listener restart. The method carries no
`args` yet: there is no CLI setup command for a client to launch in a terminal.

ACP v1 also lets an agent offer a `terminal` method only to a client that declares
`clientCapabilities.auth.terminal`; an omitted capability means unsupported. Every listener
therefore negotiates per connection (`AssistantACPAgent`): a client without that capability gets
no auth methods, and its sessions still answer `auth_required`. The Registry's validator does not
send `auth.terminal`; it signals terminal auth with `clientCapabilities._meta["terminal-auth"]`,
which counts as the capability so the listing check keeps passing.

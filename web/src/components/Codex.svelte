<script>
  // "Sign in with ChatGPT" — run the assistant on your OpenAI Codex / ChatGPT
  // subscription instead of a pay-per-token API key. UNOFFICIAL: OpenAI does not
  // officially support this, and your account could be rate-limited. Mirrors the
  // Google modal's connect/poll pattern (backend: /api/codex/*).
  import { onMount, onDestroy } from 'svelte'
  import { codexOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let st = $state(null)          // /api/codex/status
  let assistant = $state(null)   // s.assistant {provider, model, auth_mode}
  let model = $state('gpt-5-codex')
  let connecting = $state(false)
  let pendingState = $state('')  // OAuth flow state (for the headless paste path)
  let manualCode = $state('')
  let showManual = $state(false)
  let err = $state('')
  let busy = $state(false)
  let poll = null

  async function refresh() {
    try { st = await api.codexStatus() } catch (e) { err = String(e.message || e) }
    try {
      const s = await api.settings()
      assistant = s.assistant
      if (assistant?.model && assistant.provider === 'openai') model = assistant.model
    } catch {}
  }
  onMount(refresh)
  onDestroy(() => { if (poll) clearInterval(poll) })

  // OpenAI is the active assistant provider AND already in subscription mode.
  const active = $derived(assistant?.provider === 'openai' && assistant?.auth_mode === 'subscription')

  async function connect() {
    err = ''
    try {
      const r = await api.codexLoginUrl()
      if (!r.ok || !r.auth_url) { err = r.error || 'Could not start sign-in'; return }
      pendingState = r.state
      window.open(r.auth_url, '_blank')
      connecting = true
      poll = setInterval(async () => {
        const s = await api.codexStatus()
        if (s.signed_in) { clearInterval(poll); poll = null; connecting = false; st = s }
      }, 2000)
    } catch (e) { err = String(e.message || e) }
  }

  async function submitCode() {
    if (!manualCode.trim() || !pendingState) return
    err = ''; busy = true
    try {
      await api.codexSubmit(pendingState, manualCode.trim())
      manualCode = ''; showManual = false; connecting = false
      if (poll) { clearInterval(poll); poll = null }
      await refresh()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Point the assistant at the ChatGPT subscription (provider=openai, subscription).
  async function useSubscription() {
    err = ''; busy = true
    try { await api.setLlm('openai', model.trim() || 'gpt-5-codex', 'subscription'); await refresh() }
    catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function disconnect() {
    err = ''; busy = true
    try { await api.codexLogout(); await refresh() } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const close = () => ($codexOpen = false)
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal">
  <h2>Sign in with ChatGPT</h2>

  <p class="muted" style="font-size:12px;line-height:1.5">
    Run the assistant on your <b>ChatGPT Plus/Pro (Codex) subscription</b> instead of a
    pay-per-token OpenAI API key — the same mechanism the Codex CLI uses.
    <b style="color:#d8552f">Unofficial:</b> OpenAI does not officially support this and
    your account could be rate-limited. Requests route through the ChatGPT backend with
    your sign-in token.
  </p>

  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}

  {#if !st}
    <p class="muted">Loading…</p>
  {:else if st.signed_in}
    <p>Signed in ✓ <span class="muted">(account: {st.account_id || 'unknown'})</span></p>

    {#if active}
      <p class="muted" style="font-size:13px">The assistant is using your ChatGPT subscription for OpenAI (model <b>{assistant.model}</b>).</p>
    {:else}
      <p class="muted" style="font-size:13px">Now switch the assistant to use it:</p>
      <div class="keyrow">
        <input type="text" placeholder="model, e.g. gpt-5-codex" bind:value={model} />
        <button class="open" disabled={busy} onclick={useSubscription}>Use subscription</button>
      </div>
    {/if}

    <button class="linkbtn" disabled={busy} onclick={disconnect} style="margin-top:8px">Sign out</button>
  {:else}
    <p>{connecting ? 'Waiting for ChatGPT — complete sign-in in the opened tab…' : 'Sign in with your ChatGPT account.'}</p>
    <button class="open" onclick={connect}>Sign in with ChatGPT</button>

    {#if connecting}
      <p class="muted" style="font-size:12px;margin-top:10px">
        Didn't get redirected (headless / remote)?
        <button class="linkbtn" onclick={() => (showManual = !showManual)}>Paste the code manually</button>
      </p>
      {#if showManual}
        <p class="muted" style="font-size:12px">After signing in, copy the <code>code</code> value from the redirect URL and paste it here:</p>
        <div class="keyrow">
          <input type="text" placeholder="code" bind:value={manualCode} />
          <button class="open" disabled={busy} onclick={submitCode}>Submit</button>
        </div>
      {/if}
    {/if}
  {/if}

  <button class="modal-close" onclick={close}>Close</button>
</div>

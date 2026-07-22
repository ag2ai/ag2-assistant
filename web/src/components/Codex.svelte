<script>
  // "Sign in with ChatGPT" — authenticate against your OpenAI Codex / ChatGPT
  // subscription so an `openai_subscription` LLM config can run without an API key.
  // UNOFFICIAL: OpenAI does not officially support this, and your account could be
  // rate-limited. Backend: /api/codex/*.
  //
  // Scope: sign-in ONLY. Choosing the provider/model is the job of the LLM config
  // form in Settings → Models (type `openai_subscription`), which is what opens this
  // modal and stays mounted underneath it — so on close the user lands back on their
  // in-progress config with "Signed in" ticked, and saves it there.
  import { onDestroy, tick } from 'svelte'
  import { codexOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let st = $state(null) // /api/codex/status
  let connecting = $state(false)
  let pendingState = $state('') // OAuth flow state (for the headless paste path)
  let manualCode = $state('')
  let showManual = $state(false)
  let codeInput = $state(null)
  let err = $state('')
  let busy = $state(false)
  let poll = null

  async function refresh() {
    try {
      st = await api.codexStatus()
    } catch (e) {
      err = String(e.message || e)
    }
  }
  refresh()
  onDestroy(() => {
    if (poll) clearInterval(poll)
  })

  async function connect() {
    err = ''
    try {
      const r = await api.codexLoginUrl()
      if (!r.ok || !r.auth_url) {
        err = r.error || 'Could not start sign-in'
        return
      }
      pendingState = r.state
      window.open(r.auth_url, '_blank')
      connecting = true
      // The loopback callback completes the flow on its own when port 1455 is
      // reachable; when it isn't (Docker, remote host) the user pastes the code
      // instead and submitCode() takes over.
      poll = setInterval(async () => {
        const s = await api.codexStatus()
        if (s.signed_in) {
          clearInterval(poll)
          poll = null
          connecting = false
          st = s
        }
      }, 2000)
    } catch (e) {
      err = String(e.message || e)
    }
  }

  async function revealManual() {
    showManual = true
    await tick()
    codeInput?.focus()
  }

  async function submitCode() {
    if (!manualCode.trim() || !pendingState) return
    err = ''
    busy = true
    try {
      const r = await api.codexSubmit(pendingState, manualCode.trim())
      if (r && r.ok === false) {
        err = r.error || 'Could not complete sign-in'
        busy = false
        return
      }
      manualCode = ''
      showManual = false
      connecting = false
      if (poll) {
        clearInterval(poll)
        poll = null
      }
      await refresh()
    } catch (e) {
      err = String(e.message || e)
    }
    busy = false
  }

  async function disconnect() {
    err = ''
    busy = true
    try {
      await api.codexLogout()
      await refresh()
    } catch (e) {
      err = String(e.message || e)
    }
    busy = false
  }

  const close = () => ($codexOpen = false)
</script>

<div class="modal-backdrop over" onclick={close}></div>
<div class="modal over codex">
  <h2>Sign in with ChatGPT</h2>

  <p class="muted intro">
    Run the assistant on your <b>ChatGPT Plus/Pro (Codex) subscription</b> instead of a
    pay-per-token OpenAI API key — the same mechanism the Codex CLI uses.
    <b class="warn">Unofficial:</b> OpenAI does not officially support this and your account
    could be rate-limited. Requests route through the ChatGPT backend with your sign-in token.
  </p>

  {#if err}<p class="err">{err}</p>{/if}

  {#if !st}
    <p class="muted">Loading…</p>
  {:else if st.signed_in}
    <p class="ok">Signed in ✓ <span class="muted">(account: {st.account_id || 'unknown'})</span></p>
    <p class="muted note">
      Close this to return to your model settings — pick the model there and save to start
      using the subscription.
    </p>

    <div class="foot">
      <button class="linkbtn" disabled={busy} onclick={disconnect}>Sign out</button>
      <button class="open primary" onclick={close}>Done</button>
    </div>
  {:else}
    <p>
      {connecting
        ? 'Waiting for ChatGPT — complete sign-in in the opened tab…'
        : 'Sign in with your ChatGPT account.'}
    </p>
    <button class="open primary" onclick={connect}>
      {connecting ? 'Reopen sign-in tab' : 'Sign in with ChatGPT'}
    </button>

    {#if connecting}
      <p class="muted note">
        Running in Docker or on a remote host, or the tab shows
        <b>"localhost refused to connect"</b>? That's expected — the redirect points at
        <code>localhost:1455</code>, which isn't published.
        {#if !showManual}
          <button class="linkbtn" onclick={revealManual}>Paste the code manually</button>
        {/if}
      </p>

      {#if showManual}
        <p class="muted note">
          Copy the <b>whole address</b> from the browser's URL bar (even on the error page) —
          or just the <code>code</code> value from it — and paste it here:
        </p>
        <div class="pasterow">
          <input
            type="text"
            bind:this={codeInput}
            bind:value={manualCode}
            onkeydown={(e) => e.key === 'Enter' && submitCode()}
            placeholder="http://localhost:1455/auth/callback?code=… (or just the code)"
            spellcheck="false"
          />
          <button class="open primary" disabled={busy || !manualCode.trim()} onclick={submitCode}>
            {busy ? 'Submitting…' : 'Submit'}
          </button>
        </div>
      {/if}
    {/if}

    <div class="foot end">
      <button class="modal-close" onclick={close}>Cancel</button>
    </div>
  {/if}
</div>

<style>
  /* This modal lives outside .settings, so the global `.settings .linkbtn` /
     `.settings .keyrow` rules never reached it — the buttons fell back to the raw
     browser default (grey box). Style them locally instead of widening those
     global selectors. */
  .codex .intro {
    font-size: 12px;
    line-height: 1.5;
  }
  .codex .note {
    font-size: 12px;
    line-height: 1.5;
    margin-top: 6px;
  }
  .codex .warn {
    color: #d8552f;
  }
  .codex .err {
    color: #d8552f;
    font-size: 13px;
  }
  .codex .ok {
    font-size: 14px;
  }

  /* Text-link button (matches `.settings .linkbtn`). */
  .codex .linkbtn {
    flex: none;
    border: none;
    background: none;
    padding: 0;
    font: inherit;
    font-size: 12px;
    color: var(--accent);
    cursor: pointer;
  }
  .codex .linkbtn:hover {
    text-decoration: underline;
  }
  .codex .linkbtn:disabled {
    opacity: 0.4;
    cursor: default;
    text-decoration: none;
  }

  /* Paste row: input flexes to fill, Submit sits beside it — together they span the
     modal's full width. */
  .codex .pasterow {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
  }
  .codex .pasterow input {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 8px 10px;
    font: inherit;
    font-size: 13px;
    background: var(--bg);
    color: var(--ink);
  }
  .codex .pasterow input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .codex .pasterow .open {
    flex: none;
  }

  /* Footer: destructive/secondary on the left, the one primary action on the right. */
  .codex .foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-top: 12px;
  }
  .codex .foot.end {
    justify-content: flex-end;
  }
  .codex .foot :global(.modal-close) {
    align-self: auto;
  }
  /* .open / .open.primary come from the app-wide button rules (app.css); the modal
     ancestor supplies them here. Only the positional override above is local. */
</style>

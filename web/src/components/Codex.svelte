<script lang="ts">
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
  import { codexOpen } from '../store.ts'
  import { api } from '../transport/api/index.ts'
  import { errText } from '../lib/errors.ts'
  import type { CodexStatus } from '../schemas/index.ts'
  import { m } from '../paraglide/messages.js'

  let st = $state<CodexStatus | null>(null) // /api/codex/status
  let connecting = $state(false)
  let pendingState = $state('') // OAuth flow state (for the headless paste path)
  let manualCode = $state('')
  let showManual = $state(false)
  let codeInput: HTMLInputElement | undefined = $state()
  let err = $state('')
  let busy = $state(false)
  let poll: ReturnType<typeof setInterval> | null = null

  async function refresh() {
    try {
      st = await api.codexStatus()
    } catch (e) {
      err = errText(e)
    }
  }
  refresh()
  onDestroy(() => {
    if (poll) clearInterval(poll)
  })

  async function connect() {
    err = ''
    try {
      // The route always answers {ok, auth_url, state}; a failure is a non-2xx,
      // which throws with the backend's message.
      const r = await api.codexLoginUrl()
      pendingState = r.state
      window.open(r.auth_url, '_blank')
      connecting = true
      // The loopback callback completes the flow on its own when port 1455 is
      // reachable; when it isn't (Docker, remote host) the user pastes the code
      // instead and submitCode() takes over.
      poll = setInterval(async () => {
        const s = await api.codexStatus()
        if (s.signed_in) {
          if (poll) clearInterval(poll)
          poll = null
          connecting = false
          st = s
        }
      }, 2000)
    } catch (e) {
      err = errText(e)
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
      // A rejected code answers 400, so the throw below carries its message.
      await api.codexSubmit(pendingState, manualCode.trim())
      manualCode = ''
      showManual = false
      connecting = false
      if (poll) {
        clearInterval(poll)
        poll = null
      }
      await refresh()
    } catch (e) {
      err = errText(e)
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
      err = errText(e)
    }
    busy = false
  }

  const close = () => ($codexOpen = false)
</script>

<!-- Backdrop: click-to-dismiss duplicates the Cancel button, so it stays out of
     the a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop over" role="presentation" onclick={close}></div>
<div class="modal over codex">
  <h2>{m.cdx_title()}</h2>

  <p class="muted intro">
    {m.cdx_intro_pre()} <b>{m.cdx_intro_plan()}</b> {m.cdx_intro_mid()}
    <b class="warn">{m.cdx_unofficial()}</b> {m.cdx_intro_tail()}
  </p>

  {#if err}<p class="err">{err}</p>{/if}

  {#if !st}
    <p class="muted">{m.loading()}</p>
  {:else if st.signed_in}
    <p class="ok">{m.llm_signed_in()} ✓ <span class="muted">{m.cdx_account({ id: st.account_id || m.cdx_unknown() })}</span></p>
    <p class="muted note">{m.cdx_done_note()}</p>

    <div class="foot">
      <button class="linkbtn" disabled={busy} onclick={disconnect}>{m.cdx_sign_out()}</button>
      <button class="open primary" onclick={close}>{m.action_done()}</button>
    </div>
  {:else}
    <p>{connecting ? m.cdx_waiting() : m.cdx_prompt()}</p>
    <button class="open primary" onclick={connect}>
      {connecting ? m.cdx_reopen() : m.cdx_title()}
    </button>

    {#if connecting}
      <p class="muted note">
        {m.cdx_docker_pre()}
        <b>{m.cdx_localhost_refused()}</b>{m.cdx_docker_mid()}
        <code>localhost:1455</code>{m.cdx_docker_post()}
        {#if !showManual}
          <button class="linkbtn" onclick={revealManual}>{m.cdx_paste_manually()}</button>
        {/if}
      </p>

      {#if showManual}
        <p class="muted note">
          {m.cdx_paste_pre()} <b>{m.cdx_whole_address()}</b> {m.cdx_paste_mid()}
          <code>code</code> {m.cdx_paste_post()}
        </p>
        <div class="pasterow">
          <input
            type="text"
            bind:this={codeInput}
            bind:value={manualCode}
            onkeydown={(e) => e.key === 'Enter' && submitCode()}
            placeholder={m.cdx_code_placeholder()}
            spellcheck="false"
          />
          <button class="open primary" disabled={busy || !manualCode.trim()} onclick={submitCode}>
            {busy ? m.cdx_submitting() : m.action_submit()}
          </button>
        </div>
      {/if}
    {/if}

    <div class="foot end">
      <button class="modal-close" onclick={close}>{m.action_cancel()}</button>
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
    color: var(--danger);
  }
  .codex .err {
    color: var(--danger);
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

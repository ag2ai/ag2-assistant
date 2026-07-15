<script>
  // The composer's model switcher: a fast shortcut for the install-wide ACTIVE LLM
  // configuration — the exact same action as Settings → Models' "Use" (POST
  // /llm-configs/{id}/use), surfaced next to the input. It is NOT a per-thread or
  // per-message override: switching re-points load_config() for the whole install
  // and persists, so the change lands on the NEXT message you send (the in-flight
  // turn already built its agent from the previous config and can't switch
  // retroactively) — the button says so.
  //
  // Self-contained like ModelsPage/McpServers: owns its own fetch, refetches after
  // the mutation. No shared store state.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { settingsOpen, settingsPage } from '../../store.js'
  import { LOGO, TYPE_LABEL, isUsable } from '../../lib/llm.js'
  import Icon from '../Icon.svelte'

  let configs = $state([])
  let active = $state(null)
  let envOverride = $state(null)
  let busy = $state(false)
  let open = $state(false)   // popover menu open

  onMount(reload)

  async function reload() {
    try {
      const d = await api.llmConfigs()
      configs = d.configs || []
      active = d.active ?? null
      envOverride = d.env_override ?? null
    } catch {
      // A failed fetch just leaves the row empty-stated; the composer stays usable.
    }
  }

  const activeConfig = $derived(configs.find((c) => c.id === active) || null)

  async function choose(c) {
    if (busy || c.id === active || !isUsable(c)) return
    open = false
    busy = true
    try { await api.useLlmConfig(c.id); await reload() } catch { /* keep prior active */ }
    busy = false
  }

  function openSettings() {
    open = false
    settingsPage.set('model')
    settingsOpen.set(true)
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') open = false }} />

<div class="modelsw">
  {#if envOverride}
    <!-- Env vars (AG2ASSISTANT_LLM_PROVIDER / _MODEL) win last in load_config(), so a
         switch here would silently no-op. Show the pin, offer no menu. -->
    <div class="modelsw-pin" title="Model pinned by environment variables — unset them to switch">
      Pinned by environment{envOverride.provider ? ` · ${envOverride.provider}` : ''}{envOverride.model ? ` · ${envOverride.model}` : ''}
    </div>
  {:else if !configs.length}
    <button class="modelsw-empty" onclick={openSettings}>
      No models configured — add one in Settings
    </button>
  {:else}
    <div class="modelsw-wrap">
      <button class="modelsw-btn" disabled={busy} onclick={() => (open = !open)}
              title="Model for your next message">
        {#if activeConfig}
          <img class="modelsw-logo" src={LOGO[activeConfig.type]} alt="" />
          <span class="modelsw-name">{activeConfig.name}</span>
          <span class="modelsw-dot" class:warn={!isUsable(activeConfig)}></span>
        {:else}
          <span class="modelsw-name muted">Choose a model</span>
        {/if}
        <Icon name="chevron-down" size={13} />
      </button>

      {#if open}
        <!-- Scrim closes the menu on any outside click. -->
        <button class="modelsw-scrim" aria-label="Close model menu" onclick={() => (open = false)}></button>
        <div class="modelsw-menu" role="menu">
          {#each configs as c (c.id)}
            <button
              class="modelsw-item" class:active={c.id === active}
              role="menuitem"
              disabled={!isUsable(c)}
              title={isUsable(c) ? '' : 'Not ready — add a key or sign in via Settings'}
              onclick={() => choose(c)}
            >
              <img class="modelsw-logo" src={LOGO[c.type]} alt="" />
              <span class="modelsw-itemmeta">
                <span class="modelsw-name">
                  {c.name}{#if c.id === active}<Icon name="check" size={12} />{/if}
                </span>
                <span class="modelsw-sub">{TYPE_LABEL[c.type]} · {c.model}</span>
              </span>
              <span class="modelsw-dot" class:warn={!isUsable(c)}></span>
            </button>
          {/each}
          <button class="modelsw-manage" role="menuitem" onclick={openSettings}>Manage models…</button>
        </div>
      {/if}
    </div>
  {/if}
</div>

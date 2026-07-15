<script>
  // The composer's model switcher: a fast shortcut for the install-wide ACTIVE LLM
  // configuration — the exact same action as Settings → Models' "Use" (POST
  // /llm-configs/{id}/use), surfaced next to the input. It is NOT a per-thread or
  // per-message override: switching re-points load_config() for the whole install
  // and persists, so the change lands on the NEXT message you send (the in-flight
  // turn already built its agent from the previous config and can't switch
  // retroactively) — the button says so.
  //
  // Reads the shared `llmConfigs` store (lib/llm.js), not a private fetch — so a
  // rename / add / active-switch made in Settings → Models shows up here live,
  // without a reload. Mutations here (choose → Use) refresh the same store, so
  // Settings sees them too. See docs/adr/0004-shared-llm-config-store.md.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { settingsOpen, settingsPage, SETTINGS_PAGE } from '../../store.js'
  import { LOGO, TYPE_LABEL, isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  import Icon from '../Icon.svelte'

  let busy = $state(false)
  let open = $state(false)   // popover menu open

  // A failed load just leaves the row empty-stated; the composer stays usable.
  onMount(() => { loadLlmConfigs().catch(() => {}) })

  const configs = $derived($llmConfigs.configs)
  const active = $derived($llmConfigs.active)
  const envOverride = $derived($llmConfigs.envOverride)
  const activeConfig = $derived(configs.find((c) => c.id === active) || null)

  async function choose(c) {
    if (busy || c.id === active || !isUsable(c)) return
    open = false
    busy = true
    try { await api.useLlmConfig(c.id); await loadLlmConfigs() } catch { /* keep prior active */ }
    busy = false
  }

  function openSettings() {
    open = false
    settingsPage.set(SETTINGS_PAGE.MODELS)
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

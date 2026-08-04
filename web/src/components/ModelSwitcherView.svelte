<script lang="ts" generics="C extends { id: string; name: string }">
  // Presentational model switcher — the button + popover menu shared by the composer's
  // install-wide switcher (composer/ModelSwitcher) and the per-profile Text/Live
  // switchers in Settings → Profiles (ADR 0015). It owns ONLY the open/close + render;
  // every data source and mutation is injected, so the same look/interaction drives
  // either "set install-wide Active" or "set this profile's Active override". Styles are
  // the global .modelsw-* classes in app.css.
  import Icon from './Icon.svelte'
  import type { LlmEnvOverride } from '../schemas/index.ts'

  // Generic over the config row so the same view drives the text (LlmConfig) and
  // voice (LiveConfig) switchers; it reads only `id` and `name` itself.
  type Props = {
    configs?: C[]
    activeId?: string | null      // the id shown selected on the button + checked in the menu
    envOverride?: LlmEnvOverride | null   // → "pinned by environment", no menu
    busy?: boolean
    disabled?: boolean
    title?: string
    placeholder?: string
    logoFor: (c: C) => string
    labelFor: (c: C) => string
    usable?: (c: C) => boolean
    down?: boolean                // open the menu downward (header-mounted) vs up (composer)
    inherited?: boolean           // the active selection is inherited (no per-profile override)
    defaultEntry?: { label: string; sub: string } | null   // a "use install default" item
    emptyLabel?: string
    onEmpty?: () => void          // empty-state click
    onChoose?: (c: C) => void
    onDefault?: () => void        // clear override
    onManage?: (() => void) | null   // footer "Manage…"
    manageLabel?: string
  }

  let {
    configs = [],
    activeId = null,
    envOverride = null,
    busy = false,
    disabled = false,
    title = '',
    placeholder = 'Choose a model',
    logoFor,
    labelFor,
    usable = () => true,
    down = false,
    inherited = false,
    defaultEntry = null,
    emptyLabel = 'No models configured',
    onEmpty,
    onChoose,
    onDefault,
    onManage = null,
    manageLabel = 'Manage models…',
  }: Props = $props()

  let open = $state(false)
  const activeConfig = $derived(configs.find((c) => c.id === activeId) || null)

  function choose(c: C) {
    if (busy || !usable(c)) return
    open = false
    onChoose?.(c)
  }
  function pickDefault() {
    open = false
    onDefault?.()
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') open = false }} />

<div class="modelsw">
  {#if envOverride}
    <div class="modelsw-pin" title="Model pinned by environment variables — unset them to switch">
      Pinned by environment{envOverride.provider ? ` · ${envOverride.provider}` : ''}{envOverride.model ? ` · ${envOverride.model}` : ''}
    </div>
  {:else if !configs.length}
    <button class="modelsw-empty" onclick={onEmpty}>{emptyLabel}</button>
  {:else}
    <div class="modelsw-wrap">
      <button class="modelsw-btn" disabled={busy || disabled} onclick={() => (open = !open)} {title}>
        {#if activeConfig}
          <img class="modelsw-logo" src={logoFor(activeConfig)} alt="" />
          <span class="modelsw-name">{activeConfig.name}</span>
          {#if inherited}<span class="modelsw-tag">inherited</span>{/if}
          <span class="modelsw-dot" class:warn={!usable(activeConfig)}></span>
        {:else}
          <span class="modelsw-name muted">{placeholder}</span>
        {/if}
        <Icon name="chevron-down" size={13} />
      </button>

      {#if open}
        <button class="modelsw-scrim" aria-label="Close model menu" onclick={() => (open = false)}></button>
        <div class="modelsw-menu" class:down role="menu">
          {#if defaultEntry}
            <button class="modelsw-item" class:active={inherited} role="menuitem" onclick={pickDefault}>
              <span class="modelsw-itemmeta">
                <span class="modelsw-name">{defaultEntry.label}{#if inherited}<Icon name="check" size={12} />{/if}</span>
                <span class="modelsw-sub">{defaultEntry.sub}</span>
              </span>
            </button>
          {/if}
          {#each configs as c (c.id)}
            <button
              class="modelsw-item" class:active={!inherited && c.id === activeId}
              role="menuitem"
              disabled={!usable(c)}
              title={usable(c) ? '' : 'Not ready — add a key or sign in via Settings'}
              onclick={() => choose(c)}
            >
              <img class="modelsw-logo" src={logoFor(c)} alt="" />
              <span class="modelsw-itemmeta">
                <span class="modelsw-name">
                  {c.name}{#if !inherited && c.id === activeId}<Icon name="check" size={12} />{/if}
                </span>
                <span class="modelsw-sub">{labelFor(c)}</span>
              </span>
              <span class="modelsw-dot" class:warn={!usable(c)}></span>
            </button>
          {/each}
          {#if onManage}
            <button class="modelsw-manage" role="menuitem" onclick={onManage}>{manageLabel}</button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>

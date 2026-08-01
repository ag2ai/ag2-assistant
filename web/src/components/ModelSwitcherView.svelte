<script>
  // Presentational model switcher — the button + popover menu shared by the composer's
  // per-Chat switcher (composer/ModelSwitcher, ADR 0025) and the per-profile Text/Live
  // switchers in Settings → Profiles (ADR 0015). It owns ONLY the open/close + render;
  // every data source and mutation is injected, so the same look/interaction drives
  // either "set this Chat's override" or "set this profile's Active override". Styles
  // are the global .modelsw-* classes in app.css.
  import Icon from './Icon.svelte'
  import BrandMark from './BrandMark.svelte'

  let {
    configs = [],
    activeId = null,        // the id shown selected on the button + checked in the menu
    envOverride = null,     // {provider?, model?} → "pinned by environment", no menu
    busy = false,
    disabled = false,
    title = '',
    placeholder = 'Choose a model',
    brandFor,               // (c) => brand key, for lib/brandMarks — text configs key
                            // off `type`, voice ones off `provider`
    labelFor,               // (c) => sub-line text
    usable = () => true,
    down = false,           // open the menu downward (header-mounted) vs up (composer)
    inherited = false,      // the active selection is inherited (no override of its own)
    inheritedTag = true,    // suffix the closed button with "inherited" when it is.
                            // The composer turns this off: its button promises the
                            // model your next message runs on, and nothing else —
                            // where that model came from is the open menu's job
                            // (which row carries the check). ADR 0025.
    defaultEntry = null,    // { label, sub } → show a "use install default" item, else null
    emptyLabel = 'No models configured',
    onEmpty,                // () => void (empty-state click)
    onChoose,               // (c) => void
    onDefault,              // () => void (clear override)
    onManage = null,        // () => void | null (footer "Manage…")
    manageLabel = 'Manage models…',
  } = $props()

  let open = $state(false)
  const activeConfig = $derived(configs.find((c) => c.id === activeId) || null)

  function choose(c) {
    if (busy || !usable(c)) return
    open = false
    onChoose?.(c)
  }
  function pickDefault() {
    open = false
    onDefault?.()
  }
</script>

<script module>
  // The size every .modelsw-* row draws its brand mark at, here and on the task page.
  export const MARK_SIZE = 14
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
          <BrandMark brand={brandFor(activeConfig)} size={MARK_SIZE} />
          <span class="modelsw-name">{activeConfig.name}</span>
          {#if inherited && inheritedTag}<span class="modelsw-tag">inherited</span>{/if}
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
              <BrandMark brand={brandFor(c)} size={MARK_SIZE} />
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

<script lang="ts">
  import { profileEpoch } from '../../store.ts'
  import { closeOverlay, openAsideFile } from '../../router.ts'
  import { revealFile } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { errText } from '../../lib/errors.ts'
  import type { CardDefinition, CardProblem, ProfileCard } from '../../schemas/card.ts'

  let { profile = false }: { profile?: boolean } = $props()
  let cards = $state<(CardDefinition | ProfileCard)[]>([])
  let problems = $state<CardProblem[]>([])
  let loading = $state(true)
  let busy = $state('')
  let confirming = $state('')
  let err = $state('')
  const layers = $derived(profile ? ['profile', 'global', 'bundled'] : ['global', 'bundled'])
  const available = (c: CardDefinition | ProfileCard) =>
    'available' in c ? c.available : c.enabled

  function adopt(data: { cards: (CardDefinition | ProfileCard)[]; problems: CardProblem[] }) {
    cards = data.cards; problems = data.problems
  }
  async function load() {
    loading = true; err = ''
    try { adopt(await (profile ? api.profileCards() : api.cards())) }
    catch (e) { err = errText(e) }
    loading = false
  }
  $effect(() => { $profileEpoch; load() })

  async function toggle(c: CardDefinition | ProfileCard) {
    busy = c.name; err = ''
    try {
      adopt(await (!profile ? api.setCardState(c.name, !c.enabled)
        : c.origin === 'profile' ? api.setProfileCardState(c.name, !available(c))
        : api.suppressCard(c.name, 'suppressed' in c && !c.suppressed)))
    } catch (e) { err = errText(e) }
    busy = ''
  }
  async function remove(c: CardDefinition | ProfileCard) {
    busy = c.name; err = ''
    try {
      adopt(await (profile ? api.deleteProfileCard(c.name) : api.deleteCard(c.name)))
      confirming = ''
    } catch (e) { err = errText(e) }
    busy = ''
  }
  function open(path: string) {
    closeOverlay(); openAsideFile(path); revealFile(path)
  }
</script>

<div class="skzone">
  <div class="setrowwrap">
    <p class="setsub">{profile
      ? 'Cards this profile can use. Turn an inherited Card off for this profile, or disable a Card it owns.'
      : 'Turn a Card off for every profile without deleting its file. Bundled Cards ship with the app and are read-only.'}
      Changes apply to the next turn.</p>
    <button class="open" disabled={loading} onclick={load}>Refresh</button>
  </div>
  {#if err}<p class="muted error" role="alert">{err}</p>{/if}
  {#if loading}
    <p class="muted">Loading…</p>
  {:else}
    {#each layers as layer}
      <div class="setgroup">{layer === 'profile' ? 'This profile' : layer === 'global' ? 'Global' : 'Bundled'}</div>
      <div class="sklist">
        {#each cards.filter((c) => c.origin === layer) as c (c.name)}
          {@const rowBusy = busy === c.name}
          {@const deletable = profile ? c.origin === 'profile' : c.origin === 'global'}
          {@const canToggle = !rowBusy && confirming !== c.name && (!profile || c.enabled)}
          <div class="skcard" class:off={!available(c)}>
            <button class="sktop cardswitch" role="switch" aria-checked={available(c)}
              disabled={!canToggle} aria-label={`${c.name}: ${profile ? 'available to this profile' : 'enabled app-wide'}`}
              onclick={() => toggle(c)}>
              <span class="skmain">
                <span class="skname">{c.name}</span>
                <span class="skdesc">{c.description}</span>
              </span>
              <span class="skctl">
                {#if profile && !c.enabled}<span class="muted">off app-wide</span>
                {:else}<span class="setswitch" class:on={available(c)} class:busy={rowBusy} aria-hidden="true"></span>{/if}
              </span>
            </button>
            <div class="skmeta">
              <span class="skdot" class:third={c.origin !== 'bundled'}></span>
              <span>{c.origin === 'bundled' ? 'Ships with the app' : c.origin === 'profile' ? 'This profile' : 'Shared across profiles'}</span>
              {#if confirming === c.name}
                <span class="skconfirm">Delete {c.name}?</span>
                <button class="open danger" disabled={rowBusy} onclick={() => remove(c)}>Confirm</button>
                <button class="open" disabled={rowBusy} onclick={() => confirming = ''}>Cancel</button>
              {:else}
                <button class="open" onclick={() => open(c.path)}>{c.origin === 'bundled' ? 'View file' : 'Edit file'}</button>
                {#if deletable}<button class="open" disabled={rowBusy} onclick={() => confirming = c.name}>Delete</button>{/if}
              {/if}
            </div>
          </div>
        {:else}
          <p class="muted">No {layer} Cards.</p>
        {/each}
      </div>
    {/each}
    {#if problems.length}
      <div class="setgroup">Files that did not load</div>
      {#each problems as p (p.path)}
        <div class="setrowwrap">
          <div class="problem"><strong>{p.path.split('/').pop()}</strong><span class="muted">{p.origin}</span><p class="error">{p.error}</p></div>
          <button class="open" onclick={() => open(p.path)}>{p.origin === 'bundled' ? 'View file' : 'Edit file'}</button>
        </div>
      {/each}
    {/if}
  {/if}
</div>

<style>
  .cardswitch { width: 100%; border: 0; background: transparent; text-align: left; color: inherit; font: inherit; cursor: pointer; }
  .cardswitch:disabled { cursor: default; opacity: 1; }
  .cardswitch:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .skdesc { display: block; }
  .error { color: var(--danger); overflow-wrap: anywhere; }
  .problem { flex: 1; min-width: 0; }
  .problem span { margin-left: 8px; }
  .problem p { margin: 4px 0; }
</style>

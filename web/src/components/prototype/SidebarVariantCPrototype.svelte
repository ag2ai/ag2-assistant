<script lang="ts">
  import type { Snippet } from 'svelte'
  import Icon from '../Icon.svelte'
  import { sidebarPanelOpen } from './sidebarPrototype.ts'
  let { rail, profiles, items, create, auxiliary, title, count }: {
    rail: Snippet; profiles: Snippet; items: Snippet; create: Snippet; auxiliary: Snippet; title: string; count: number;
  } = $props()
</script>

<aside class="sidebar-variant-c" aria-label="Collapsible sidebar prototype">
  {@render rail()}
  {#if $sidebarPanelOpen}
    <section class="workspace">
      <div class="profile-header">{@render profiles()}</div>
      <header><div><span class="eyebrow">WORKSPACE</span><h2>{title}<small>{count}</small></h2></div><button title="Collapse sidebar" aria-label="Collapse sidebar" onclick={() => sidebarPanelOpen.set(false)}><Icon name="chevron-left" size={17} /></button></header>
      <div class="create">{@render create()}</div>
      {@render auxiliary()}
      <div class="workspace-list">{@render items()}</div>
      <footer><Icon name="eye" size={13} /><span>Keep your page in view</span></footer>
    </section>
  {/if}
</aside>

<style>
  .sidebar-variant-c { height: 100%; display: flex; background: var(--panel); border-right: 1px solid var(--line); }
  .workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .profile-header { padding: 20px 18px 13px; }
  header { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px 20px; }
  .eyebrow { font-size: 9px; letter-spacing: 1.5px; color: var(--text-faint); }
  h2 { margin: 6px 0 0; font-size: 23px; font-weight: 500; letter-spacing: -.6px; display: flex; align-items: baseline; gap: 8px; }
  small { font: 10px var(--font-mono); color: var(--text-faint); }
  button { display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 7px; color: var(--text-muted); background: var(--surface); cursor: pointer; }
  button:hover { color: var(--text); background: var(--surface-hover); }
  .create { padding: 0 9px 14px; }
  .workspace-list { flex: 1; overflow: auto; padding: 0 11px; }
  footer { padding: 14px 18px; border-top: 1px solid var(--line); display: flex; align-items: center; gap: 7px; color: var(--text-faint); font-size: 10px; }
</style>

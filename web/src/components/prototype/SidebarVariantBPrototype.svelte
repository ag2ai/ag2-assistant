<script lang="ts">
  import type { Snippet } from 'svelte'
  import Icon from '../Icon.svelte'
  let { rail, profiles, items, create, auxiliary, title, count, query = $bindable('') }: {
    rail: Snippet; profiles: Snippet; items: Snippet; create: Snippet; auxiliary: Snippet; title: string; count: number; query: string;
  } = $props()
</script>

<aside class="sidebar-variant-b" aria-label="Compact sidebar prototype">
  {@render rail()}
  <section class="workspace">
    <header><h2>{title}</h2><span>{count}</span></header>
    <div class="search"><Icon name="search" size={14} /><input aria-label="Filter sidebar items" placeholder={`Search ${title.toLowerCase()}…`} bind:value={query} /></div>
    {@render auxiliary()}
    <div class="workspace-list">{@render items()}</div>
    <div class="create">{@render create()}</div>
    <footer>{@render profiles()}</footer>
  </section>
</aside>

<style>
  .sidebar-variant-b { height: 100%; display: flex; background: var(--surface); border-right: 1px solid var(--line); }
  .workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  header { padding: 22px 16px 15px; display: flex; align-items: center; justify-content: space-between; }
  h2 { margin: 0; font-size: 19px; font-weight: 600; letter-spacing: -.5px; }
  header span { color: var(--text-faint); font-size: 11px; }
  .search { margin: 0 12px 18px; display: flex; align-items: center; gap: 7px; color: var(--text-faint); padding: 8px 9px; border: 1px solid var(--line); border-radius: 7px; }
  input { background: none; border: none; color: var(--text); outline: none; min-width: 0; width: 100%; font: 12px var(--font-sans); }
  .search:focus-within { border-color: var(--accent); }
  .workspace-list { flex: 1; overflow: auto; padding: 0 9px; }
  .create { border-top: 1px solid var(--line); margin-top: 10px; padding: 10px 6px 6px; }
  footer { padding: 12px 16px 16px; }
</style>

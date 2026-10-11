<script lang="ts">
  import type { Snippet } from 'svelte'
  let { rail, profiles, items, create, auxiliary, title, count }: {
    rail: Snippet; profiles: Snippet; items: Snippet; create: Snippet; auxiliary: Snippet; title: string; count: number;
  } = $props()
</script>

<aside class="sidebar-variant-a" aria-label="VS Code sidebar prototype">
  {@render rail()}
  <section class="workspace">
    <div class="profile-header">{@render profiles()}</div>
    <header><h2>{title}</h2><span class="count">{count}</span></header>
    <div class="create">{@render create()}</div>
    {@render auxiliary()}
    <div class="workspace-list">{@render items()}</div>
    <footer>AG2 Assistant</footer>
  </section>
</aside>

<style>
  .sidebar-variant-a { height: 100%; display: flex; background: var(--panel); border-right: 1px solid var(--line); }
  .workspace { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .profile-header { padding: 19px 15px 17px; border-bottom: 1px solid var(--line); }
  header { display: flex; align-items: center; gap: 8px; padding: 19px 17px 9px; }
  h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .9px; margin: 0; }
  .count { margin-left: auto; font: 11px var(--font-mono); color: var(--text-faint); }
  .create { padding: 0 6px 9px; }
  .workspace-list { flex: 1; overflow: auto; padding: 0 6px; }
  footer { padding: 12px 17px; border-top: 1px solid var(--line); color: var(--text-faint); font-size: 10px; letter-spacing: .3px; }
</style>

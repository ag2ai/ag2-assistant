<script lang="ts">
  // A Card link (ADR 0008): an in-app target navigates the shell, an external one
  // opens a tab. A link to nothing that is there is the plain text it wraps.
  import type { Snippet } from 'svelte'
  import { go, openAsideFile } from '../../router.ts'
  import { knownThings, revealFolder } from '../../store.ts'
  import { a2uiLink } from '../../lib/a2ui.ts'
  import type { A2UIComponent, A2UIData } from '../../lib/a2ui.ts'

  type Props = {
    component: A2UIComponent
    data?: A2UIData
    scope?: string
    grow?: number
    passive?: boolean
    children: Snippet
  }
  let { component, data = {}, scope = '', grow, passive = false, children }: Props = $props()

  const link = $derived(a2uiLink(component, data, scope, $knownThings))

  function open() {
    if (!link) return
    if (link.kind === 'task') go('/t/' + link.value)
    else if (link.kind === 'chat') go('/c/' + link.value)
    else if (link.kind === 'file') openAsideFile(link.value)
    else if (link.kind === 'folder') revealFolder(link.value)
  }
</script>

{#if link?.kind === 'url'}
  <a class="a2ui-link" href={link.value} target="_blank" rel="noopener noreferrer" style:flex-grow={grow}>{@render children()}</a>
{:else if passive}
  <span class="a2ui-link" aria-disabled="true" style:flex-grow={grow}>{@render children()}</span>
{:else if !link}
  {@render children()}
{:else}
  <button type="button" class="a2ui-link" onclick={open} style:flex-grow={grow}>{@render children()}</button>
{/if}

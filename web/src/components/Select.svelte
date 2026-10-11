<script lang="ts">
  import type { Snippet } from 'svelte'
  import type { HTMLSelectAttributes } from 'svelte/elements'

  type Props = Omit<HTMLSelectAttributes, 'value' | 'children' | 'multiple'> & {
    value?: string;
    multiple?: false;
    children: Snippet;
  }
  let { value = $bindable(), children, ...attributes }: Props = $props()
</script>

<select {...attributes} bind:value>
  {@render children()}
</select>

<style>
  select { width: 100%; min-width: 0; box-sizing: border-box; padding: 8px 32px 8px 10px; border: 1px solid var(--line); border-radius: var(--radius-sm); color: var(--text); background-color: var(--bg); font: inherit; appearance: none; -webkit-appearance: none; background-image: var(--select-chevron); background-repeat: no-repeat; background-position: right 10px center; background-size: 14px; }
  select:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  select:disabled { cursor: default; opacity: 0.6; }
</style>

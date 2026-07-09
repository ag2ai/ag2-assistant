<script>
  // Settings → Profiles: the profile list, the (read-only) project folder, focus areas.
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'
  import { FOCUS } from '../../lib/focuses.js'
  import Icon from '../Icon.svelte'
  import Profiles from '../Profiles.svelte'
  import FolderPicker from '../FolderPicker.svelte'

  const ctx = getSettings()

  let editFolder = $state(false)   // project-folder picker expanded?
  function openFolderEdit() { editFolder = true }
  // one-click commit: the folder you're viewing in the picker applies immediately
  const commitFolder = (path) => ctx.run(() => api.setProjectFolder(path).then(() => { editFolder = false }))

  // Focus areas — a per-profile persona attribute (settings.json → agent context).
  // Toggling a pill persists immediately for the ACTIVE profile.
  const toggleFocus = (id) => {
    const cur = ctx.s?.focuses || []
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    ctx.run(() => api.setFocuses(next))
  }
</script>

<div class="setsec">Profiles</div>
<Profiles />

<div class="setsec">Project folder</div>
{#if !editFolder}
  <div class="setrowwrap">
    <div class="setrow">
      <span class="sk"><Icon name="folder" size={15} /> {ctx.s.project_folder ? 'Folder' : 'Choose a folder'}</span>
      <span class="sv">{ctx.s.project_folder || 'the assistant can read this folder (read-only)'}</span>
    </div>
    <button class="open" onclick={openFolderEdit}>Change</button>
  </div>
{:else}
  <FolderPicker roots={ctx.s.fs || {}} start={ctx.s.project_folder || (ctx.s.fs && ctx.s.fs.cwd) || ''} busy={ctx.busy} onUse={commitFolder} />
  <div class="keyrow" style="justify-content:flex-end">
    <button class="linkbtn" onclick={() => (editFolder = false)}>Cancel</button>
  </div>
{/if}

<div class="setsec">Focus areas</div>
<div class="focuspills">
  {#each FOCUS as f}
    <button class="focuspill" class:on={(ctx.s.focuses || []).includes(f.id)} disabled={ctx.busy} onclick={() => toggleFocus(f.id)}>
      <Icon name={f.icon} size={13} /> {f.label}
    </button>
  {/each}
</div>
<p class="muted" style="font-size:12px;margin:2px 0 0">What this profile is for — shapes how the assistant helps.</p>

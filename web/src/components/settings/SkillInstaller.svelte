<script>
  // Install control shared by both Skills surfaces (ADR 0017). The SURFACE carries the
  // target: the parent passes an `installer` of already-scoped calls (Global from the
  // Application page, active Profile from the Profiles zone), so this component never
  // decides where a skill lands. Three sources behind one control:
  //   • registry — search skills.sh, Install a hit
  //   • git URL  — discover every SKILL.md in a repo, pick a subset, install
  //   • upload   — same discover→checklist→install from a SKILL.md / zipped folder
  // Git/upload are one-time snapshots (no update action — that's delete + re-install).
  // On any successful install it calls onInstalled() so the parent refreshes its list.
  import Icon from '../Icon.svelte'
  let { installer, onInstalled } = $props()

  // Collapsed by default behind an "Add skill" affordance (like Add model / Add secret);
  // the whole picker only unfolds on demand so it doesn't crowd the skills list.
  let open = $state(false)
  let mode = $state('registry') // 'registry' | 'git' | 'upload'
  let busy = $state(false)
  let err = $state('')

  // registry
  let query = $state('')
  let results = $state([])
  // git / upload discover→pick
  let gitUrl = $state('')
  let file = $state(null)
  let found = $state([])          // [{name, description}]
  let picked = $state(new Set())  // selected names

  const reset = () => { results = []; found = []; picked = new Set(); err = '' }
  const setMode = (m) => { mode = m; reset() }
  // Collapse back to the "Add skill" button, clearing any in-progress picker state.
  const close = () => { open = false; mode = 'registry'; query = ''; gitUrl = ''; file = null; reset() }

  async function guard(fn) {
    err = ''; busy = true
    try { return await fn() }
    catch (e) { err = String(e.message || e); return null }
    finally { busy = false }
  }

  const search = () =>
    guard(async () => { results = (await installer.search(query)).results || [] })

  const installOne = (r) =>
    guard(async () => {
      await installer.install({ install_id: r.install_id })
      onInstalled?.()
    })

  const discover = () =>
    guard(async () => {
      const res = mode === 'git'
        ? await installer.discover(gitUrl)
        : await installer.discoverUpload(file)
      found = res.skills || []
      picked = new Set(found.map((s) => s.name)) // default: all selected
    })

  const toggle = (name) => {
    const next = new Set(picked)
    next.has(name) ? next.delete(name) : next.add(name)
    picked = next
  }

  const installPicked = () =>
    guard(async () => {
      const names = [...picked]
      if (mode === 'git') await installer.install({ git_url: gitUrl, names })
      else await installer.installUpload(file, names)
      reset(); gitUrl = ''; file = null
      onInstalled?.()
    })

  const onFile = (e) => { file = e.target.files?.[0] || null; found = []; picked = new Set() }
</script>

{#if !open}
  <button class="addbtn" onclick={() => (open = true)}>
    <Icon name="plus" size={14} /> Add skill
  </button>
{:else}
<div class="ski">
  <div class="skihead">
    <div class="skitabs">
      <button class="skitab" class:on={mode === 'registry'} onclick={() => setMode('registry')}>Registry</button>
      <button class="skitab" class:on={mode === 'git'} onclick={() => setMode('git')}>Git URL</button>
      <button class="skitab" class:on={mode === 'upload'} onclick={() => setMode('upload')}>Upload</button>
    </div>
    <button class="linkbtn" disabled={busy} onclick={close}>Cancel</button>
  </div>

  {#if err}<p class="skierr">{err}</p>{/if}

  {#if mode === 'registry'}
    <form class="skirow" onsubmit={(e) => { e.preventDefault(); search() }}>
      <input class="skiinput" placeholder="Search skills.sh…" bind:value={query} disabled={busy} />
      <button class="open" disabled={busy || !query.trim()} type="submit">Search</button>
    </form>
    {#each results as r (r.install_id)}
      <div class="skifound">
        <span class="skifmeta">
          <span class="skifname">
            {r.name}
            {#if r.installs}<span class="skiinstalls">{r.installs.toLocaleString()} installs</span>{/if}
          </span>
          <span class="skifdesc">{r.description}</span>
        </span>
        <button class="open" disabled={busy} onclick={() => installOne(r)}>Install</button>
      </div>
    {/each}
  {:else if mode === 'git'}
    <form class="skirow" onsubmit={(e) => { e.preventDefault(); discover() }}>
      <input class="skiinput" placeholder="https://github.com/owner/repo(.git)" bind:value={gitUrl} disabled={busy} />
      <button class="open" disabled={busy || !gitUrl.trim()} type="submit">Install</button>
    </form>
  {:else}
    <div class="skirow">
      <!-- The native <input type=file> renders a browser-default grey button + "no file
           chosen" text that clashes with the app. Hide it and drive it from a full-width
           label styled exactly like the Registry/Git search field, so all three source
           rows share one shape: a bordered field (filename + Browse chip) then the CTA. -->
      <label class="skifile" class:nofile={!file} class:disabled={busy}>
        <input type="file" accept=".zip,.md" onchange={onFile} disabled={busy} />
        <span class="skifilename">{file ? file.name : 'Choose a .md file or zipped skill folder…'}</span>
        <span class="skifilepick">Browse</span>
      </label>
      <button class="open" disabled={busy || !file} onclick={discover}>Install</button>
    </div>
  {/if}

  {#if mode !== 'registry' && found.length > 0}
    <div class="skichecklist">
      {#each found as s (s.name)}
        <label class="skicheck">
          <input type="checkbox" checked={picked.has(s.name)} onchange={() => toggle(s.name)} disabled={busy} />
          <span class="skifname">{s.name}</span>
          <span class="skifdesc">{s.description}</span>
        </label>
      {/each}
      <button class="open on" disabled={busy || picked.size === 0} onclick={installPicked}>
        Install {picked.size} skill{picked.size === 1 ? '' : 's'}
      </button>
    </div>
  {/if}
</div>
{/if}

<style>
  .ski { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 10px; margin-top: 10px; }
  /* Header row: source switcher on the left, Cancel (collapse) on the right. */
  .skihead { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
  /* Source switcher = the app's segmented control (.segbar/.seg), sized to fit
     inside the installer box (no page-edge margins). */
  .skitabs { display: inline-flex; gap: 3px; padding: 3px; background: var(--code); border-radius: var(--radius-pill); }
  .skitab {
    display: inline-flex; align-items: center; justify-content: center;
    font: inherit; font-size: 13px; font-weight: var(--fw-medium);
    color: var(--muted); background: none; border: none; border-radius: var(--radius-pill);
    padding: 5px 12px; cursor: pointer;
    transition: color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
  }
  .skitab:hover { color: var(--text); }
  .skitab.on { color: var(--accent); background: var(--surface); box-shadow: var(--shadow-sm); }
  .skierr { color: var(--danger); font-size: 12px; margin: 0 0 8px; }
  .skirow { display: flex; gap: 8px; align-items: center; }
  /* Text inputs use the one settings input style (border, radius, bg, ink), with the
     standard accent + focus-ring on focus — the same as .keyrow/.mcpform inputs. */
  .skiinput {
    flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: 8px;
    padding: 7px 9px; font: inherit; font-size: 13px; background: var(--bg); color: var(--ink);
  }
  .skiinput:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  /* File picker: native input hidden; this label is a full-width field matching the
     .skiinput search box — filename fills it, a Browse chip sits at the trailing edge,
     and :focus-within lights the accent + focus-ring like the text inputs. */
  .skifile {
    flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; cursor: pointer;
    border: 1px solid var(--line); border-radius: 8px; padding: 5px 6px 5px 11px;
    font: inherit; font-size: 13px; background: var(--bg);
    transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .skifile:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .skifile:focus-within { border-color: var(--accent); box-shadow: var(--focus-ring); }
  .skifile.disabled { opacity: .5; pointer-events: none; }
  .skifile input { display: none; }
  .skifilename { flex: 1; min-width: 0; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* `nofile` (not `empty`) — a bare `.empty` collides with the global full-page
     empty-state rule in app.css (text-align:center; margin-top:18vh), which was
     shoving the whole picker down the box. */
  .skifile.nofile .skifilename { color: var(--muted); }
  /* Trailing "Browse" affordance — a quiet chip that reads as the click target. */
  .skifilepick {
    flex: none; font-size: 12px; font-weight: var(--fw-medium); color: var(--muted);
    border: 1px solid var(--line); border-radius: 6px; padding: 2px 9px; background: var(--surface);
    transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
  }
  .skifile:hover .skifilepick { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .skifound, .skicheck { display: flex; align-items: center; gap: 10px; padding: 6px 2px; border-top: 1px solid var(--line); }
  .skichecklist { margin-top: 8px; }
  .skifmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .skifname { font-size: 13px; font-weight: var(--fw-semibold); color: var(--text); display: flex; align-items: baseline; gap: 8px; }
  .skiinstalls { font-size: 11px; font-weight: var(--fw-regular, 400); color: var(--text-muted); }
  .skifdesc { font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .skicheck { cursor: pointer; }
  .skicheck .skifdesc { flex: 1; }
</style>

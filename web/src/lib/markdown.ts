import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api as P } from './profile.ts'

// A bare relative path the agent emitted (e.g. images/foo.jpg, uploads/bar.png) —
// not absolute/scheme/anchor — refers to a WORKSPACE file. The browser would resolve
// it against the page URL (/app/…) and 404, so serve it via the (profile-scoped) files API.
const isWorkspaceRel = (u: string | null): u is string => !!u && !/^(https?:|data:|blob:|mailto:|#|\/)/i.test(u)
const filesApi = (p: string) => P('/files/raw?path=' + encodeURIComponent(p.replace(/^\.\//, '')))
// The files-raw route is now profile-scoped: /api/p/{pid}/files/raw.
const isFilesRaw = (pathname: string) => /^\/api\/p\/[^/]+\/files\/raw$/.test(pathname)

// Rewrite workspace-relative images/links to the files API, and open external links
// in a new tab. Runs after sanitization so attributes we add aren't re-filtered.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'IMG' && node.hasAttribute('src')) {
    const src = node.getAttribute('src')
    if (isWorkspaceRel(src)) {
      node.setAttribute('src', filesApi(src))
      node.setAttribute('loading', 'lazy')
    }
    return
  }
  if (node.tagName !== 'A' || !node.hasAttribute('href')) return
  const href = node.getAttribute('href')
  if (isWorkspaceRel(href)) {
    // link to a workspace file → open it via the files API
    node.setAttribute('href', filesApi(href))
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
    return
  }
  if (!href || !/^https?:\/\//i.test(href)) return // #anchor / mailto → in place
  try {
    if (new URL(href, window.location.href).host !== window.location.host) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  } catch {}
})

export function renderMarkdown(text: string | null | undefined): string {
  // marked.parse is sync unless async:true is set, which this app never does.
  return DOMPurify.sanitize(marked.parse(text || '') as string)
}

// The image the Viewer is asked to open.
export type OpenedImage = { path: string; name: string; alt: string }

// Make rendered markdown images clickable → a full-size preview. Workspace images
// (served via the files API) open the in-app Viewer through `onOpen({path,name,alt})`;
// anything else opens in a new tab. Idempotent (assigns onclick), so safe to re-run on
// each streaming re-render.
export function bindImages(node: ParentNode, onOpen?: (file: OpenedImage) => void): void {
  for (const img of node.querySelectorAll('img')) {
    img.style.cursor = 'zoom-in'
    img.onclick = () => {
      const src = img.getAttribute('src') || ''
      let path = null
      try {
        const u = new URL(src, window.location.href)
        if (isFilesRaw(u.pathname)) path = u.searchParams.get('path')
      } catch {}
      if (path && onOpen) onOpen({ path, name: path.split('/').pop() ?? '', alt: img.getAttribute('alt') || '' })
      else window.open(src, '_blank', 'noopener')
    }
  }
}

// Turn bare task ids in already-rendered DOM into links that open the task.
const TASK_RE = /\btask-[0-9a-f]{6,}\b/g
export function linkifyDom(node: Node, onOpen?: (taskId: string) => void): void {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.parentElement && n.parentElement.closest('a')
        ? NodeFilter.FILTER_REJECT
        : TASK_RE.test(n.nodeValue || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
  })
  const targets: Node[] = []
  while (walker.nextNode()) targets.push(walker.currentNode)
  for (const tn of targets) {
    const frag = document.createDocumentFragment()
    let last = 0; const s = tn.nodeValue || ''; TASK_RE.lastIndex = 0; let m
    while ((m = TASK_RE.exec(s))) {
      if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)))
      const a = document.createElement('a')
      a.href = '#'; a.className = 'tasklink'; a.textContent = m[0]
      const id = m[0]
      a.addEventListener('click', (e) => { e.preventDefault(); onOpen && onOpen(id) })
      frag.appendChild(a); last = m.index + m[0].length
    }
    if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)))
    tn.parentNode?.replaceChild(frag, tn)
  }
}

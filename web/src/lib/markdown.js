import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Open external (non-local) links in a new tab. Runs after attribute
// sanitization, so attributes we add here aren't re-filtered out. In-app links
// (relative hrefs, #anchors, same-origin) are left to navigate in place.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'A' || !node.hasAttribute('href')) return
  const href = node.getAttribute('href')
  if (!/^https?:\/\//i.test(href)) return // relative / #anchor / mailto → in place
  try {
    if (new URL(href, window.location.href).host !== window.location.host) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  } catch {}
})

export function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}

// Turn bare task ids in already-rendered DOM into links that open the task.
const TASK_RE = /\btask-[0-9a-f]{6,}\b/g
export function linkifyDom(node, onOpen) {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.parentElement && n.parentElement.closest('a')
        ? NodeFilter.FILTER_REJECT
        : TASK_RE.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
  })
  const targets = []
  while (walker.nextNode()) targets.push(walker.currentNode)
  for (const tn of targets) {
    const frag = document.createDocumentFragment()
    let last = 0; const s = tn.nodeValue; TASK_RE.lastIndex = 0; let m
    while ((m = TASK_RE.exec(s))) {
      if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)))
      const a = document.createElement('a')
      a.href = '#'; a.className = 'tasklink'; a.textContent = m[0]
      const id = m[0]
      a.addEventListener('click', (e) => { e.preventDefault(); onOpen && onOpen(id) })
      frag.appendChild(a); last = m.index + m[0].length
    }
    if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)))
    tn.parentNode.replaceChild(frag, tn)
  }
}

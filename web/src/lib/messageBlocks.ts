export const MESSAGE_PREVIEW_LINES = 12

// Keep the reader's anchor in place when a message block changes height.
export async function preserveMessagePosition(anchor: HTMLElement, update: () => void | Promise<void>): Promise<void> {
  const scroller = anchor.closest<HTMLElement>('.thread')
  const top = anchor.getBoundingClientRect().top
  await update()
  if (!scroller || !anchor.isConnected) return
  scroller.scrollTop += anchor.getBoundingClientRect().top - top
  scroller.dispatchEvent(new scroller.ownerDocument.defaultView!.Event('scroll'))
}

type BlockOptions = { id: string; streaming: boolean; state: Map<string, boolean> }

// Add disclosures to sanitized Markdown, retaining the reader's choices across streaming renders.
export function bindMessageBlocks(root: HTMLElement, { id, streaming, state }: BlockOptions): void {
  const doc = root.ownerDocument
  function button(key: string, content: HTMLElement, expanded: boolean, label: string, apply: (open: boolean) => void): HTMLButtonElement {
    const control = doc.createElement('button')
    control.type = 'button'
    control.className = 'message-disclosure-button'
    content.id = `${id}-${key}`
    control.setAttribute('aria-controls', content.id)
    function sync(open: boolean) {
      expanded = open
      control.textContent = `${open ? 'Collapse' : 'Expand'}${key.startsWith('code') ? ' code' : ''}`
      control.setAttribute('aria-label', `${open ? 'Collapse' : 'Expand'} ${label}`)
      control.setAttribute('aria-expanded', String(open))
      apply(open)
    }
    control.onclick = () => {
      void preserveMessagePosition(control, () => {
        state.set(key, !expanded)
        sync(!expanded)
      })
    }
    sync(expanded)
    return control
  }

  for (const [index, pre] of Array.from(root.querySelectorAll('pre')).entries()) {
    const key = `code-${index}`
    const lines = (pre.textContent || '').replace(/\n$/, '').split('\n').length
    const language = pre.querySelector('code')?.className.match(/(?:^|\s)language-([^\s]+)/)?.[1]
    const block = doc.createElement('div')
    block.className = 'message-code'
    const header = doc.createElement('div')
    header.className = 'message-code-header'
    const label = doc.createElement('span')
    label.textContent = `${language || 'Code'} · ${lines} ${lines === 1 ? 'line' : 'lines'}`
    const content = doc.createElement('div')
    content.className = 'message-code-content'
    pre.replaceWith(block)
    content.append(pre)
    const control = button(key, content, state.get(key) ?? (streaming || lines <= MESSAGE_PREVIEW_LINES), 'code block', (open) => {
      content.classList.toggle('is-collapsed', !open)
      content.hidden = !open && lines <= MESSAGE_PREVIEW_LINES
    })
    header.append(label, control)
    block.append(header, content)
  }

  const sections: { level: number; content: HTMLElement }[] = []
  let index = 0
  for (const child of Array.from(root.childNodes)) {
    const level = child.nodeType === 1 ? Number((child as HTMLElement).tagName.match(/^H([1-6])$/)?.[1]) : 0
    if (!level) {
      sections.at(-1)?.content.append(child)
      continue
    }
    while (sections.length && sections.at(-1)!.level >= level) sections.pop()
    const key = `section-${index++}`
    const heading = child as HTMLElement
    const section = doc.createElement('section')
    section.className = 'message-section'
    const header = doc.createElement('div')
    header.className = 'message-section-heading'
    const content = doc.createElement('div')
    content.className = 'message-section-content'
    const control = button(key, content, state.get(key) ?? true, `section: ${heading.textContent}`, (open) => { content.hidden = !open })
    heading.replaceWith(section)
    header.append(heading, control)
    section.append(header, content)
    sections.at(-1)?.content.append(section)
    sections.push({ level, content })
  }
  for (const section of root.querySelectorAll('.message-section')) {
    const content = section.querySelector('.message-section-content')!
    if (content.textContent?.trim() || content.querySelector('img, hr, pre, table')) continue
    section.replaceWith(section.querySelector('.message-section-heading')!.firstElementChild!)
  }
}

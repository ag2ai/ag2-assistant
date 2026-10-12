import type { TokenizerAndRendererExtension, Tokens } from 'marked'

// SVG and MathML blocks, which Markdown would split at blank or indented lines.
const OPEN = /^ {0,3}<(svg|math)(?=[\s>/])/i

// Length of the element opened at `src[0]`, up to its matching close tag; 0 if unclosed.
export function foreignElementLength(src: string, tag: string): number {
  const re = new RegExp(`<(/?)${tag}(?=[\\s>/])[^>]*?(/?)>`, 'gi')
  let depth = 0
  for (let m = re.exec(src); m; m = re.exec(src)) {
    if (m[1]) depth--
    else if (!m[2]) depth++
    if (depth === 0) return re.lastIndex
  }
  return 0
}

// A marked block extension that emits a whole SVG/MathML element as raw HTML.
export const foreignBlock: TokenizerAndRendererExtension = {
  name: 'foreignBlock',
  level: 'block',
  start: (src) => src.match(/^ {0,3}<(svg|math)(?=[\s>/])/im)?.index,
  tokenizer(src): Tokens.Generic | undefined {
    const tag = OPEN.exec(src)?.[1]
    if (!tag) return
    const indent = src.length - src.trimStart().length
    const len = foreignElementLength(src.slice(indent), tag)
    if (!len) return
    const raw = src.slice(0, indent + len)
    return { type: 'foreignBlock', raw, text: raw.trim() }
  },
  renderer: (token) => token.text + '\n',
}

import { mount } from 'svelte'
import BasicA2UIComponent from '../src/components/items/BasicA2UIComponent.svelte'
import type { A2UIComponent } from '../src/lib/a2ui.ts'
import '../src/design/styles.css'
import '../src/app.css'

type Result = { name: string; failures: string[]; scrollWidth: number; clientWidth: number }
const fixtures = document.querySelector('#fixtures')!
const cases: { name: string; target: HTMLElement; scrolls: boolean }[] = []

for (const width of [280, 620]) {
  for (const align of ['start', 'center', 'end', 'stretch']) {
    for (const nesting of ['direct', 'column', 'row', 'card', 'list', 'siblings']) {
      for (const count of [1, 12]) {
        const name = `${width}/${align}/${nesting}/${count}`
        const target = document.createElement('section')
        target.className = 'fixture'
        target.style.width = `${width}px`
        fixtures.append(target)
        const components: A2UIComponent[] = [
          { id: 'root', component: 'Card', child: 'body' },
          { id: 'body', component: 'Column', align, children: ['title', nesting === 'direct' ? 'table' : 'nested'] },
          { id: 'title', component: 'Text', text: name },
          { id: 'nested', component: nesting === 'card' ? 'Card' : nesting === 'list' ? 'List' : nesting === 'row' || nesting === 'siblings' ? 'Row' : 'Column', child: 'table', children: nesting === 'siblings' ? ['table', 'table'] : ['table'], align, grow: true },
          { id: 'table', component: 'Table', variant: count === 1 ? undefined : 'data', density: 'compact', columns: { path: '/columns' }, rows: { path: '/rows' }, cells: { path: './cells' }, header: 'header', lead: count === 1 ? undefined : 'lead', cell: 'cell', columnWidth: 'regular', columnOverflow: 'nowrap' },
          { id: 'lead', component: 'Text', text: 'Weekday' },
          { id: 'header', component: 'Text', text: { path: './label' } },
          { id: 'cell', component: 'Row', children: ['link', 'pill'] },
          { id: 'link', component: 'Link', url: 'https://example.com', child: 'text' },
          { id: 'text', component: 'Text', text: { path: './text' } },
          { id: 'pill', component: 'Text', text: '3.04%', variant: 'pill' },
        ]
        mount(BasicA2UIComponent, { target, props: {
          component: components[0], components,
          data: {
            columns: Array.from({ length: count }, (_, i) => ({ label: `Column ${i}` })),
            rows: [{ cells: Array.from({ length: count }, (_, i) => ({ text: count === 1 ? 'Short' : `Long unbroken content ${i}: ${'x'.repeat(80)}` })) }],
          },
        } })
        cases.push({ name, target, scrolls: count > 1 })
      }
    }
  }
}

await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
const results: Result[] = cases.map(({ name, target, scrolls }) => {
  const failures: string[] = []
  const wrap = target.querySelector<HTMLElement>('.a2ui-tablewrap')!
  for (const box of [target, ...target.querySelectorAll<HTMLElement>('.a2ui-basic-card, .a2ui-basic-col, .a2ui-basic-row, .a2ui-list')]) {
    if (box.closest('.a2ui-table')) continue
    if (box.scrollWidth > box.clientWidth + 1) failures.push(`${box.className} overflows by ${box.scrollWidth - box.clientWidth}px`)
  }
  const bounds = wrap.getBoundingClientRect()
  const frame = target.getBoundingClientRect()
  if (bounds.left < frame.left || bounds.right > frame.right) failures.push('Table frame escapes the fixture')
  if ((wrap.scrollWidth > wrap.clientWidth + 1) !== scrolls) failures.push('Wrong scroll behavior')
  const tables = target.querySelectorAll('.a2ui-tablewrap')
  if (name.startsWith('620/') && name.endsWith('/siblings/1') && tables[0].getBoundingClientRect().top !== tables[1].getBoundingClientRect().top) failures.push('Short sibling tables no longer share a row')
  if (scrolls) {
    wrap.scrollLeft = wrap.scrollWidth
    if (wrap.scrollLeft < 1) failures.push('Wide table cannot scroll')
    const last = wrap.querySelector<HTMLElement>('.a2ui-td:last-child')!.getBoundingClientRect()
    if (last.right > bounds.right + 1) failures.push('Last column cannot be reached')
    wrap.scrollLeft = 0
  }
  return { name, failures, scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth }
})

const sizing: Result[] = []
for (const frameWidth of [280, 620, 1000]) {
  const target = document.createElement('section')
  target.className = 'fixture'; target.style.width = `${frameWidth}px`; fixtures.append(target)
  const components: A2UIComponent[] = [
    { id: 'root', component: 'Grid', columns: 2, minColumnWidth: 'sm', gap: 'sm', width: 'fill', children: ['fit', 'fill'] },
    { id: 'fit', component: 'Card', width: 'content', grow: true, child: 'table-fit' },
    { id: 'fill', component: 'Card', width: 'fill', child: 'table-fill' },
    { id: 'table-fit', component: 'Table', width: 'content', columns: { path: '/columns' }, rows: { path: '/rows' }, cells: { path: './cells' }, cell: 'text', columnWidth: 'narrow' },
    { id: 'table-fill', component: 'Table', width: 'fill', columns: { path: '/columns' }, rows: { path: '/rows' }, cells: { path: './cells' }, cell: 'text', columnWidth: 'narrow' },
    { id: 'text', component: 'Text', text: { path: '.' } },
  ]
  mount(BasicA2UIComponent, { target, props: { component: components[0], components,
    data: { columns: [{}, {}], rows: [{ cells: ['A', 'B'] }] } } })
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  const grid = target.querySelector<HTMLElement>('.a2ui-grid')!
  const cards = Array.from(grid.children) as HTMLElement[]
  const trackWidths = getComputedStyle(grid).gridTemplateColumns.split(' ').map(parseFloat)
  const failures: string[] = []
  const expectedColumns = frameWidth < 524 ? 1 : 2
  if (trackWidths.length !== expectedColumns) failures.push(`Expected ${expectedColumns} columns; got ${trackWidths}`)
  if (trackWidths.some(width => Math.abs(width - trackWidths[0]) > 1)) failures.push('Unequal Grid columns')
  if (cards[0].getBoundingClientRect().width >= trackWidths[0] - 1) failures.push('Content Card stretched to the track')
  if (Math.abs(cards[1].getBoundingClientRect().width - trackWidths[0]) > 1) failures.push('Fill Card did not fill the track')
  const tables = cards.map(card => card.querySelector<HTMLElement>('.a2ui-tablewrap')!)
  if (tables[0].clientWidth > 140) failures.push('Content Table stretched')
  if (tables[1].clientWidth !== cards[1].clientWidth - 20) failures.push('Fill Table did not fill Card content')
  if (target.scrollWidth > target.clientWidth + 1) failures.push('Grid escaped its container')
  sizing.push({ name: `sizing/${frameWidth}`, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
}
Object.assign(window, { layoutResults: [...results, ...sizing] })

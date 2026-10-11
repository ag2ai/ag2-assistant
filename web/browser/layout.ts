import { mount } from 'svelte'
import BasicA2UIComponent from '../src/components/items/BasicA2UIComponent.svelte'
import A2UISurface from '../src/components/items/A2UISurface.svelte'
import A2UIComposing from '../src/components/items/A2UIComposing.svelte'
import SelectFixture from './SelectFixture.svelte'
import { thread } from '../src/store.ts'
import type { ThreadItem } from '../src/schemas/events.ts'
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


const calendars: Result[] = []
for (const frameWidth of [264, 620]) {
  for (const cellSize of ['sm', 'md', 'lg']) {
    for (const locale of ['en', 'ru']) {
      for (const end of ['2026-10-31', '2027-10-01']) {
        const name = `calendar/${frameWidth}/${cellSize}/${locale}/${end}`
        const target = document.createElement('section')
        target.className = 'fixture'; target.style.width = `${frameWidth}px`; fixtures.append(target)
        const nodes: A2UIComponent[] = [
          { id: 'root', component: 'Card', width: 'content', child: 'body' },
          { id: 'body', component: 'Column', align: 'start', children: ['calendar'] },
          { id: 'calendar', component: 'CalendarHeatmap', startDate: '2026-10-01', endDate: end, days: { path: '/days' }, cellSize, locale },
        ]
        mount(BasicA2UIComponent, { target, props: { component: nodes[0], components: nodes, data: { days: [
          { date: '2026-10-04', count: 1, label: 'One session' }, { date: '2026-10-06', count: 3 },
          { date: '2026-10-08', status: 'analysis' }, { date: '2026-10-09', status: 'pause' },
          { date: '2026-10-10', count: 0 }, { date: '2026-10-11', status: 'upcoming' },
        ] } } })
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
        const failures: string[] = []
        const card = target.querySelector<HTMLElement>('.a2ui-basic-card')!
        const scroll = target.querySelector<HTMLElement>('.calendar-scroll')!
        const bounds = card.getBoundingClientRect(), container = target.getBoundingClientRect()
        if (bounds.left < container.left - 1 || bounds.right > container.right + 1) failures.push('Calendar Card escapes its container')
        if (target.scrollWidth > target.clientWidth + 1) failures.push('Calendar leaks horizontal overflow')
        if (end === '2026-10-31' && card.clientWidth > 250) failures.push('Monthly heatmap is not compact')
        if ((scroll.scrollWidth > scroll.clientWidth + 1) !== (end !== '2026-10-31')) failures.push('Wrong calendar scroll behavior')
        const cell = target.querySelector<HTMLElement>('[data-date="2026-10-06"]')!
        if (cell.clientWidth > 18 || cell.dataset.status !== 'activity') failures.push('Activity day or square sizing is wrong')
        if (target.querySelector('[data-date="2026-10-08"]')?.getAttribute('data-status') !== 'analysis') failures.push('Analysis became activity')
        if (target.querySelector('[data-date="2026-10-02"]')?.getAttribute('data-status') !== 'unknown') failures.push('Missing data became missed')
        if (target.querySelector('[data-date="2026-10-04"]')?.getAttribute('aria-label') !== 'One session') failures.push('Accessible day label was lost')
        if (target.querySelector('[role="alert"]')) failures.push('Calendar reported a render error')
        if (end !== '2026-10-31') {
          scroll.scrollLeft = scroll.scrollWidth
          if (scroll.scrollLeft < 1) failures.push('Annual calendar cannot scroll')
          const last = target.querySelector<HTMLElement>('[data-date="2027-10-01"]')!.getBoundingClientRect()
          if (last.right > scroll.getBoundingClientRect().right + 1) failures.push('Last day is unreachable')
          scroll.scrollLeft = 0
        }
        calendars.push({ name, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
      }
    }
  }
}
const metrics: Result[] = []
for (const [value, expected] of [[17, '17'], [0, '0'], [1234, '1,234'], [17.5, '17.5']] as const) {
  const target = document.createElement('section')
  target.className = 'fixture'; target.style.width = '280px'; fixtures.append(target)
  const component: A2UIComponent = {
    id: 'metric', component: 'Metric', value: { path: '/days' }, unit: 'дней', delta: 2,
  }
  mount(BasicA2UIComponent, { target, props: { component, components: [component], data: { days: value } } })
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
  const failures: string[] = []
  if (target.querySelector('.a2ui-metric-value')?.textContent !== `${expected}дней`) failures.push('Metric value or unit is incorrect')
  if (target.querySelector('.a2ui-metric-delta')?.textContent !== '▲+2') failures.push('Metric change has fractional padding')
  metrics.push({ name: `metric/${value}`, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
}
const surfaces: Result[] = []
for (const frameWidth of [280, 620]) {
  for (const variant of ['card', 'feature', 'column']) {
    for (const passive of [false, true]) {
      const target = document.createElement('section')
      target.className = 'fixture'; target.style.width = `${frameWidth}px`; fixtures.append(target)
      const nodes: A2UIComponent[] = [
        { id: 'root', component: variant === 'column' ? 'Column' : 'Card', variant: variant === 'feature' ? 'feature' : undefined, width: variant === 'card' ? 'content' : 'fill', child: 'body', children: ['title', 'input', 'value'] },
        { id: 'body', component: 'Column', children: ['title', 'input', 'value'] },
        { id: 'title', component: 'Text', text: 'English activity', variant: 'h4' },
        { id: 'input', component: 'TextField', label: 'Note', value: { path: '/note' } },
        { id: 'value', component: 'Text', text: { path: '/note' } },
      ]
      const item: Extract<ThreadItem, { kind: 'a2ui' }> = {
        id: 1, kind: 'a2ui', surfaceId: `native-${frameWidth}-${variant}-${passive}`,
        title: 'English activity', catalogId: 'test-catalog', component: nodes[0], components: nodes, data: { note: 'Initial note' },
      }
      mount(A2UISurface, { target, props: { item, passive } })
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      const failures: string[] = []
      const wrapper = target.querySelector<HTMLElement>('.a2ui-surface')!
      const style = getComputedStyle(wrapper)
      if (style.borderTopWidth !== '0px' || style.backgroundColor !== 'rgba(0, 0, 0, 0)' || style.paddingLeft !== '0px' || style.boxShadow !== 'none') failures.push('Surface has decorative chrome')
      if (target.textContent?.includes('Overview') || target.textContent?.includes('AG2 catalog')) failures.push('Generic header is visible')
      if (target.querySelectorAll('.a2ui-basic-card').length !== (variant === 'column' ? 0 : 1)) failures.push('Authored frame count changed')
      if (target.querySelectorAll('.a2ui-t-h4').length !== 1 || wrapper.getAttribute('aria-label') !== item.title) failures.push('Card title was duplicated or lost')
      if (!!target.querySelector('.instance-save-trigger') === passive) failures.push('Wrong Save instance visibility')
      const input = target.querySelector<HTMLInputElement>('input')!
      if (!passive) {
        const save = target.querySelector<HTMLElement>('.instance-save-trigger')!
        if (save.title !== 'Save instance' || save.getAttribute('aria-label') !== 'Save instance' || save.textContent?.trim()) failures.push('Save icon or tooltip is incorrect')
        const bounds = save.getBoundingClientRect(), frame = wrapper.getBoundingClientRect()
        if (bounds.right > frame.right || Math.abs(frame.right - bounds.right - 7) > 1 || Math.abs(bounds.top - frame.top - 7) > 1) failures.push('Save action is not in the top right corner')
        const card = wrapper.querySelector('.a2ui-basic-card')?.getBoundingClientRect()
        if (card && Math.abs(card.right - bounds.right - 7) > 1) failures.push('Save action is detached from the authored Card')
        save.focus()
        if (getComputedStyle(save).opacity !== '1') failures.push('Save action is hidden from keyboard focus')
        save.blur()
      }
      if (input.disabled !== passive) failures.push('Wrong input disabled state')
      if (!passive) {
        input.value = 'Updated note'; input.dispatchEvent(new Event('input', { bubbles: true }))
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
        if (!Array.from(target.querySelectorAll('.a2ui-text')).some(el => el.textContent === 'Updated note')) failures.push('Input data binding stopped updating')
      }
      if (wrapper.scrollWidth > wrapper.clientWidth + 1 || target.scrollWidth > target.clientWidth + 1) failures.push('Surface overflows')
      surfaces.push({ name: `surface/${frameWidth}/${variant}/${passive}`, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
    }
  }
}
const composingTarget = document.createElement('section')
composingTarget.className = 'fixture'; composingTarget.style.width = '280px'; fixtures.append(composingTarget)
mount(A2UIComposing, { target: composingTarget })
await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
const composing = composingTarget.querySelector<HTMLElement>('[role="status"]')!
const composingStyle = getComputedStyle(composing)
const composingFailures: string[] = []
if (composingStyle.borderTopWidth !== '0px' || composingStyle.backgroundColor !== 'rgba(0, 0, 0, 0)' || composing.textContent?.includes('Overview')) composingFailures.push('Composing placeholder has generic chrome')
if (composingTarget.scrollWidth > composingTarget.clientWidth + 1) composingFailures.push('Composing placeholder overflows')
surfaces.push({ name: 'surface/composing', failures: composingFailures, scrollWidth: composingTarget.scrollWidth, clientWidth: composingTarget.clientWidth })
const draftMenus: Result[] = []
const latestDraft: Extract<ThreadItem, { kind: 'a2ui' }> = {
  id: 100, kind: 'a2ui', surfaceId: 'latest-draft', title: 'Native card',
  component: { id: 'root', component: 'Card' }, data: {}, draft: { id: 'native-draft', version: 2, name: 'Native card' },
}
thread.set({ id: null, kind: 'chat', chat: 'browser-fixture', items: [latestDraft], busy: false })
for (const width of [280, 620]) {
  for (const state of ['current', 'obsolete', 'passive']) {
    const target = document.createElement('section')
    target.className = 'fixture'; target.style.width = `${width}px`; fixtures.append(target)
    const nodes: A2UIComponent[] = [
      { id: 'root', component: 'Card', width: 'content', child: 'title' },
      { id: 'title', component: 'Text', text: 'Native card', variant: 'h4' },
    ]
    const item = { ...latestDraft, component: nodes[0], components: nodes, draft: { ...latestDraft.draft!, version: state === 'obsolete' ? 1 : 2 } }
    mount(A2UISurface, { target, props: { item, passive: state === 'passive' } })
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    const failures: string[] = []
    const definition = target.querySelector<HTMLButtonElement>('.definition-save-trigger')
    if (!!definition !== (state === 'current')) failures.push('Wrong definition action availability')
    if (definition) {
      if (getComputedStyle(definition).opacity !== '1' || definition.title !== 'Save definition') failures.push('Definition action is not permanently visible with its tooltip')
      const instance = target.querySelector<HTMLElement>('.instance-save-trigger')!.getBoundingClientRect()
      if (instance.right > definition.getBoundingClientRect().left) failures.push('Save actions overlap')
      definition.scrollIntoView({ block: 'center' }); definition.click()
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      const menu = target.querySelector<HTMLElement>('[popover][aria-label="Save definition"]')!
      const bounds = menu.getBoundingClientRect()
      if (!menu.matches(':popover-open') || definition.getAttribute('aria-expanded') !== 'true') failures.push('Definition dropdown did not open')
      if (bounds.left < 0 || bounds.right > window.innerWidth || bounds.top < 0 || bounds.bottom > window.innerHeight) failures.push('Save dropdown escapes the viewport')
      const primary = menu.querySelector<HTMLButtonElement>('.card-save-actions .open.primary')!
      const secondary = menu.querySelector<HTMLButtonElement>('.card-save-actions .open:not(.primary)')!
      if (getComputedStyle(primary).borderRadius === '0px' || getComputedStyle(primary).color === getComputedStyle(secondary).color) failures.push('Save menu buttons do not use the shared design system')
      const inputs = menu.querySelectorAll<HTMLInputElement>('input')
      if (inputs[0].value !== 'Native card' || inputs[1].value !== 'native-card.card.yaml') failures.push('Definition form defaults were lost')
      inputs[0].focus()
      menu.querySelector<HTMLButtonElement>('.card-save-actions button[type="button"]')!.click()
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      if (menu.matches(':popover-open') || definition.getAttribute('aria-expanded') !== 'false') failures.push('Definition dropdown did not close')
      if (document.activeElement !== definition) failures.push('Definition dropdown lost keyboard focus on close')
    }
    draftMenus.push({ name: `definition-menu/${width}/${state}`, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
  }
}
const selects: Result[] = []
for (const width of [160, 340]) {
  for (const disabled of [false, true]) {
    const target = document.createElement('section')
    target.className = 'fixture'; target.style.width = `${width}px`; fixtures.append(target)
    mount(SelectFixture, { target, props: { disabled } })
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    const failures: string[] = []
    const select = target.querySelector<HTMLSelectElement>('select')!
    const form = target.querySelector<HTMLFormElement>('form')!
    const style = getComputedStyle(select)
    if (target.querySelector<HTMLSelectElement>('[data-test="default"]')?.value !== 'first') failures.push('Unbound Select lost its native default selection')
    if (style.appearance !== 'none' || style.backgroundImage === 'none' || style.backgroundPosition !== 'calc(100% - 10px) 50%' || style.paddingRight !== '32px') failures.push('Shared Select lost its chevron spacing')
    if (target.scrollWidth > target.clientWidth + 1) failures.push('Select escapes its container')
    if (select.labels?.[0]?.textContent?.trim().startsWith('Directory') !== true || select.dataset.test !== 'directory' || !select.required || select.disabled !== disabled) failures.push('Native select attributes or label were lost')
    if (disabled) {
      select.focus()
      if (document.activeElement === select || new FormData(form).has('directory')) failures.push('Disabled Select accepts focus or submits a value')
    } else {
      if (form.checkValidity()) failures.push('Required Select accepts its empty option')
      select.value = 'drafts'; select.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      if (target.querySelector('[data-selection]')?.textContent !== 'drafts' || target.querySelector('[data-change]')?.textContent !== 'drafts' || new FormData(form).get('directory') !== 'drafts' || !form.checkValidity()) failures.push('Selection binding, change event or form value was lost')
      target.querySelector<HTMLButtonElement>('button')!.click()
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      if (select.value !== '') failures.push('Select ignores external bound value changes')
      select.focus()
      if (document.activeElement !== select || getComputedStyle(select).boxShadow === 'none') failures.push('Select has no visible keyboard focus')
      select.blur()
    }
    selects.push({ name: `select/${width}/${disabled}`, failures, scrollWidth: target.scrollWidth, clientWidth: target.clientWidth })
  }
}
Object.assign(window, { layoutResults: [...results, ...sizing, ...calendars, ...metrics, ...surfaces, ...draftMenus, ...selects] })

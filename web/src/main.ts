import './design/styles.css'
import './design/palette.js'
import './app.css'
import './components/items/broadsheet.css' // shared editorial-surface shell (.bs / .bs-*)
import { mount } from 'svelte'
import App from './App.svelte'

// index.html always ships #app; a miss means the shell was served wrong, and
// failing loudly beats mounting into nothing.
const target = document.getElementById('app')
if (!target) throw new Error('mount target #app is missing from the page shell')

export default mount(App, { target })

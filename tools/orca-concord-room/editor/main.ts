// Center-tab Concord Room. Orca's custom editors are script modules, so this loads the same page
// (panel/index.html) and logic (panel/room.js) the sidebar panel uses, instead of keeping a second copy.
const text = await (await fetch(new URL('../panel/index.html', import.meta.url))).text()
const page = new DOMParser().parseFromString(text, 'text/html')
for (const style of page.querySelectorAll('style')) document.head.append(style.cloneNode(true))
for (const el of [...page.body.children]) if (el.tagName !== 'SCRIPT') document.body.append(el.cloneNode(true))
await import('../panel/avatars.js')
await import('../panel/mentions.js')
await import('../panel/room.js')

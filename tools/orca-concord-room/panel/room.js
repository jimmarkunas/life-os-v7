(() => {
  const $ = (id) => document.getElementById(id)
  const emptyEl = $('empty'), list = $('messages'), toSel = $('to'), text = $('text'), sendBtn = $('send'), note = $('note'), jump = $('jump')
  const nodes = new Map()
  let stick = true, lastMsgs = [], me = '', sending = false, lastRoster = '', chosenByUser = false, nicks = {}, order = [], lastMessages = []

  const kind = (id) => id.split(':')[0]
  const brand = (id) => ({ 'claude-code': 'claude', codex: 'codex', human: 'human' }[kind(id)] || 'other')
  const BASE = { claude: 'Claude', codex: 'Codex', human: 'Human', other: 'Agent' }
  // Simplified marks, drawn here (not the official artwork).
  const ICONS = {
    claude: () => { const r = [0, 45, 90, 135, 180, 225, 270, 315].map((a) => `<rect x="-1.2" y="-10.5" width="2.4" height="7.6" rx="1.2" transform="rotate(${a})"/>`).join(''); return `<svg viewBox="-12 -12 24 24" fill="#f2a65a" aria-hidden="true">${r}</svg>` },
    codex: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="19" height="19" rx="6"/><path d="M8 9l3.5 3L8 15M13 16h3.5"/></svg>',
    human: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#4d8dff" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="3.6"/><path d="M4.5 20c.8-4 3.7-6 7.5-6s6.7 2 7.5 6"/></svg>',
    other: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#9aa0a6" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>'
  }
  const icon = (id) => ICONS[brand(id)]()
  // What an agent is called: your nickname if you gave one, else "Claude" / "Codex" / "You",
  // numbered when several of the same kind exist (in order of first appearance).
  function nameOf(id) {
    if (nicks[id]) return nicks[id]
    const b = brand(id)
    if (b === 'human') return id === me ? 'You' : 'Human'
    const same = order.filter((x) => brand(x) === b)
    return same.length > 1 ? `${BASE[b]} ${same.indexOf(id) + 1}` : BASE[b]
  }
  function noteIds(ids) { ids.forEach((id) => { if (id && !order.includes(id)) order.push(id) }) }
  const clock = (iso) => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  const nearBottom = () => list.scrollHeight - list.scrollTop - list.clientHeight < 48
  const toBottom = () => { list.scrollTop = list.scrollHeight; stick = true; jump.style.display = 'none' }

  list.addEventListener('scroll', () => { stick = nearBottom(); if (stick) jump.style.display = 'none' })
  jump.addEventListener('click', toBottom)

  function setStatus(label, cls) { const s = $('status'); s.textContent = label; s.className = 'badge' + (cls ? ' ' + cls : '') }
  function setBanner(msg) { const b = $('banner'); b.style.display = msg ? 'block' : 'none'; b.textContent = msg || '' }
  function setNote(msg, cls) { note.textContent = msg || ''; note.className = cls || '' }

  function renderMessage(m) {
    let el = nodes.get(m.id)
    const isMine = m.from === me || kind(m.from) === 'human'
    if (!el) {
      el = document.createElement('div')
      el.className = 'msg ' + brand(m.from) + ' ' + (isMine ? 'mine' : 'theirs')
      el.innerHTML = '<div class="av"></div><div class="col"><div class="who"><span class="from"></span> <span class="to"></span></div><div class="bubble"></div><div class="meta"><span class="time"></span><span class="st"></span><span class="rep"></span><span class="id"></span></div></div>'
      el.querySelector('.av').innerHTML = icon(m.from)
      el.querySelector('.bubble').textContent = m.content
      el.querySelector('.time').textContent = clock(m.at)
      el.querySelector('.rep').textContent = m.replyTo ? '↩ reply' : ''
      el.querySelector('.id').textContent = m.id.slice(0, 8)
      nodes.set(m.id, el)
    }
    el.querySelector('.from').textContent = nameOf(m.from)
    el.querySelector('.to').textContent = '→ ' + nameOf(m.to)
    el.title = `${m.from} → ${m.to}\n${m.id}`
    const st = el.querySelector('.st')
    const label = m.receipt || m.status
    if (st.textContent !== label) { st.textContent = label; st.className = 'st st-' + (m.status || '') }
    return el
  }

  function renderMessages(messages) {
    if (messages.length === 0) { emptyEl.textContent = 'No messages yet. Pick an agent below and say hello.'; if (!emptyEl.isConnected) list.replaceChildren(emptyEl); return }
    if (emptyEl.isConnected) emptyEl.remove()
    const before = nodes.size
    let added = 0
    messages.forEach((m, i) => {
      const el = renderMessage(m)
      if (list.children[i] !== el) { list.insertBefore(el, list.children[i] || null); }
    })
    added = nodes.size - before
    if (stick) toBottom()
    else if (added > 0) jump.style.display = 'block'
  }

  function renderRoster(agents) {
    const targets = agents.filter((a) => kind(a.id) !== 'human' && a.id !== me)
    noteIds(agents.map((a) => a.id))
    const key = JSON.stringify(targets) + me + JSON.stringify(nicks) + order.join()
    if (key === lastRoster) return
    lastRoster = key
    $('chips').replaceChildren(...targets.map((a) => {
      const c = document.createElement('span'); c.className = 'chip ' + brand(a.id) + (a.state.startsWith('live') ? ' live' : '')
      c.title = `${a.id}\nClick the name to rename`
      const dot = document.createElement('i'); const ic = document.createElement('span'); ic.innerHTML = icon(a.id)
      const label = document.createElement('span'); label.textContent = nameOf(a.id)
      const st = document.createElement('span'); st.className = 'st'; st.textContent = a.state.split('/')[0]
      label.addEventListener('click', () => startRename(c, label, a.id))
      c.append(ic.firstChild, label, dot, st); return c
    }))
    const prev = toSel.value
    toSel.replaceChildren(...(targets.length ? targets : [{ id: '', state: 'no agents present' }]).map((a) => {
      const o = document.createElement('option'); o.value = a.id; o.textContent = a.id ? `${nameOf(a.id)} (${a.state.split('/')[0]})` : 'No agents present'; return o
    }))
    toSel.disabled = targets.length === 0
    if (targets.some((a) => a.id === prev)) toSel.value = prev
    updateSend()
  }

  function startRename(chip, label, id) {
    const input = document.createElement('input'); input.value = nicks[id] || nameOf(id); input.maxLength = 32; input.setAttribute('aria-label', 'Agent name')
    label.replaceWith(input); input.focus(); input.select()
    let done = false
    const finish = async (save) => {
      if (done) return; done = true
      if (save) {
        try { const out = await orca.plugin.request('concord.rename', { id, name: input.value }, { timeoutMs: 8000 }); nicks = out.nicknames || {} }
        catch (e) { setNote((e && e.message) || String(e), 'bad') }
      }
      lastRoster = ''; await refresh()
    }
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false) })
    input.addEventListener('blur', () => finish(true))
  }

  function updateSend() { sendBtn.disabled = sending || !toSel.value || !text.value.trim() }
  function autosize() { text.style.height = 'auto'; text.style.height = Math.min(text.scrollHeight, 140) + 'px' }

  const surface = (orca && orca.document) ? 'editor' : 'panel'
  const ask = (name, input, ms) => {
    if (!orca.plugin || typeof orca.plugin.request !== 'function') return Promise.reject(new Error(`No worker bridge in this ${surface} (orca.plugin is ${typeof orca.plugin}).`))
    return Promise.race([orca.plugin.request(name, input, { timeoutMs: ms }), new Promise((_, rej) => setTimeout(() => rej(new Error(`The worker did not answer "${name}" within ${Math.round(ms / 1000)}s (${surface} surface).`)), ms + 2000))])
  }
  let fetching = false, repoRootShown = ''
  async function refresh() {
    if (fetching) return
    fetching = true
    try {
      const snap = await ask('concord.messages', {}, 12000)
      setBanner(''); setStatus('Live', 'ok'); $('status').title = snap.repoRoot
      try {
        const people = await ask('concord.agents', {}, 12000)
        me = people.me; nicks = people.nicknames || {}; $('me').textContent = 'Sending as ' + nameOf(me) + ' (' + me + ')'
        noteIds(snap.messages.flatMap((m) => [m.from, m.to]))
        renderRoster(people.agents)
      } catch (e) { /* roster is optional for reading */ }
      noteIds(snap.messages.flatMap((m) => [m.from, m.to]))
      lastMsgs = snap.messages; repoRootShown = snap.repoRoot
      renderMessages(snap.messages)
    } catch (e) {
      setStatus('Unavailable', 'bad'); setBanner((e && e.message) || String(e))
    } finally { fetching = false }
  }

  async function send() {
    if (sendBtn.disabled) return
    const to = toSel.value, content = text.value
    sending = true; updateSend(); text.disabled = true; setNote('Sending…')
    try {
      const out = await orca.plugin.request('concord.send', { to, content }, { timeoutMs: 20000 })
      text.value = ''; autosize()
      setNote(`Sent to ${to} (${out.status}${out.delivery ? ', ' + out.delivery : ''}). "queued" is not "delivered" — watch the receipt.`, 'ok')
      stick = true
      await refresh(); toBottom()
    } catch (e) {
      setNote((e && e.message) || String(e), 'bad')
    } finally { sending = false; text.disabled = false; updateSend(); text.focus() }
  }

  text.addEventListener('input', () => { autosize(); updateSend() })
  text.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() } })
  toSel.addEventListener('change', () => { chosenByUser = true; updateSend() })
  sendBtn.addEventListener('click', send)

  // ---- Create a Jira ticket (through Orca's own Jira connection; only on the Create click) ----
  const sheet = $('sheet'), jproject = $('jproject'), jtype = $('jtype'), jtitle = $('jtitle'), jbody = $('jbody'), jcount = $('jcount'), jlabels = $('jlabels'), jparent = $('jparent'), jcreate = $('jcreate'), jnote = $('jnote')
  let projectsLoaded = false, jbusy = false
  const setJNote = (m, c) => { jnote.textContent = m || ''; jnote.className = c || '' }
  const excerpt = () => {
    const n = Math.max(0, Math.min(40, parseInt(jcount.value, 10) || 0))
    const rows = lastMsgs.slice(-n).map((m) => `- [${clock(m.at)}] ${nameOf(m.from)} → ${nameOf(m.to)} (${m.receipt || m.status}): ${m.content.replace(/\s+/g, ' ').slice(0, 600)}`)
    return `Created from the Concord Room${repoRootShown ? ' (' + repoRootShown + ')' : ''}.\n\n` + (rows.length ? 'Recent conversation:\n' + rows.join('\n') + '\n' : '')
  }
  const updateCreate = () => { jcreate.disabled = jbusy || !jproject.value || !jtype.value || !jtitle.value.trim() }
  async function loadTypes() {
    jtype.replaceChildren(); updateCreate()
    if (!jproject.value) return
    try {
      const out = await orca.plugin.request('jira.types', { project: jproject.value }, { timeoutMs: 20000 })
      jtype.replaceChildren(...out.types.map((t) => { const o = document.createElement('option'); o.value = t.id; o.textContent = t.name; return o }))
      const task = out.types.find((t) => t.name === 'Task'); if (task) jtype.value = task.id
    } catch (e) { setJNote((e && e.message) || String(e), 'bad') }
    updateCreate()
  }
  async function openSheet() {
    sheet.classList.add('open'); setJNote(''); jbody.value = excerpt(); jtitle.focus()
    if (!projectsLoaded) {
      try {
        const out = await orca.plugin.request('jira.projects', {}, { timeoutMs: 20000 })
        jproject.replaceChildren(...out.projects.map((p) => { const o = document.createElement('option'); o.value = p.key; o.textContent = `${p.key} — ${p.name}`; return o }))
        if (out.projects.some((p) => p.key === 'LIFE')) jproject.value = 'LIFE'
        projectsLoaded = true
      } catch (e) { setJNote('Could not load Jira projects from Orca: ' + ((e && e.message) || e), 'bad'); return }
    }
    await loadTypes()
  }
  async function createTicket() {
    if (jcreate.disabled) return
    jbusy = true; updateCreate(); setJNote('Creating…')
    try {
      const out = await orca.plugin.request('jira.create', { confirm: true, project: jproject.value, typeId: jtype.value, title: jtitle.value, body: jbody.value, labels: jlabels.value.split(/\s+/).filter(Boolean), parent: jparent.value.trim() }, { timeoutMs: 40000 })
      setJNote('Created ' + out.key + (out.url ? ' — ' + out.url : ''), 'ok')
      jtitle.value = ''
      if (out.url && orca.host && orca.host.call) { const b = document.createElement('button'); b.textContent = 'Open ' + out.key; b.type = 'button'; b.addEventListener('click', () => orca.host.call('shell.openExternal', { url: out.url }).catch(() => {})); jnote.append(' ', b) }
    } catch (e) { setJNote((e && e.message) || String(e), 'bad') }
    finally { jbusy = false; updateCreate() }
  }
  $('jiraBtn').addEventListener('click', openSheet)
  $('jcancel').addEventListener('click', () => sheet.classList.remove('open'))
  $('jrefill').addEventListener('click', () => { jbody.value = excerpt() })
  jproject.addEventListener('change', loadTypes)
  jtitle.addEventListener('input', updateCreate)
  jcreate.addEventListener('click', createTicket)
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') sheet.classList.remove('open') })

  // Custom editor tab: Orca expects the editor to ask for its (empty, read-only) document once.
  try { if (orca.document && orca.document.get) orca.document.get().catch(() => {}) } catch (e) { /* panel surface has no document */ }

  refresh(); setInterval(refresh, 3000)
})()

(() => {
  const $ = (id) => document.getElementById(id)
  const emptyEl = $('empty'), list = $('messages'), toSel = $('to'), text = $('text'), sendBtn = $('send'), note = $('note'), jump = $('jump')
  const nodes = new Map()
  let stick = true, lastMsgs = [], me = '', sending = false, lastRoster = '', chosenByUser = false, nicks = {}, order = [], lastMessages = []
  let hidden = [], showHidden = false, mentionAgents = [], peopleAgents = [], wake = {}
  // Concord can wake an idle agent only when its adapter reports "idle" (the worker reads `concord adapters status`); a kind it says nothing about is not marked.
  const pullOnly = (id) => wake[kind(id)] === false
  const PULL_TIP = "Concord can't wake this agent while it is idle: it reads messages the next time it takes a turn."
  // @mentions: pure helpers in mentions.js. If that file failed to load, the room still works with the "To" box alone.
  const RM = window.RoomMentions || { active: () => null, candidates: () => [], insert: (t, c) => ({ text: t, caret: c }), parse: () => [] }

  const kind = (id) => id.split(':')[0]
  const brand = (id) => ({ 'claude-code': 'claudecode', codex: 'codex', human: 'human', 'claude-chat': 'claudechat', 'chatgpt-chat': 'chatgpt', relay: 'other' }[kind(id)] || 'other')
  const BASE = { claudecode: 'Claude Code', claudechat: 'Claude Chat', chatgpt: 'ChatGPT', codex: 'Codex', human: 'Human', other: 'Agent' }
  const SENDER = { 'chatgpt-chat': ['chatgpt', 'ChatGPT'], 'claude-chat': ['claudechat', 'Claude Chat'] }
  // Simplified marks, drawn here (not the official artwork).
  const ICONS = {
    claude: () => { const r = [0, 45, 90, 135, 180, 225, 270, 315].map((a) => `<rect x="-1.2" y="-10.5" width="2.4" height="7.6" rx="1.2" transform="rotate(${a})"/>`).join(''); return `<svg viewBox="-12 -12 24 24" fill="#f2a65a" aria-hidden="true">${r}</svg>` },
    codex: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="19" height="19" rx="6"/><path d="M8 9l3.5 3L8 15M13 16h3.5"/></svg>',
    human: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#ff2fae" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="3.6"/><path d="M4.5 20c.8-4 3.7-6 7.5-6s6.7 2 7.5 6"/></svg>',
    other: () => '<svg viewBox="0 0 24 24" fill="none" stroke="#9aa0a6" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>'
  }
  ICONS.claudecode = ICONS.claude; ICONS.claudechat = ICONS.claude; ICONS.chatgpt = ICONS.other
  const AV = window.ROOM_AVATARS || {}, AVS = window.ROOM_AVATARS_SMALL || {}
  // Avatars are rendered at exact 2x sizes (52 px shown at 26 px, 32 px shown at 16 px), so the browser never rescales them.
  const iconB = (b, small) => { const set = small ? AVS : AV; return set[b] ? `<img class="avimg${b === 'claudecode' ? ' pix' : ''}" alt="" width="${small ? 16 : 26}" height="${small ? 16 : 26}" src="${set[b]}">` : (ICONS[b] || ICONS.other)() }
  const icon = (id, small) => iconB(brand(id), small)
  // What an agent is called: your nickname if you gave one, else "Claude" / "Codex" / "You",
  // numbered when several of the same kind exist (in order of first appearance).
  function nameOf(id) {
    if (nicks[id]) return nicks[id]
    const b = brand(id)
    if (kind(id) === 'relay') return 'Handoff relay'
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
    if (messages.length === 0) { emptyEl.textContent = toSel.disabled ? 'No agents here yet. Use ＋ Agent (top right) to start Claude Code or Codex; it joins this room.' : 'No messages yet. Pick an agent below and say hello.'; if (!emptyEl.isConnected) list.replaceChildren(emptyEl); return }
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
    peopleAgents = agents
    // Live agents first, then idle, then away; the box defaults to the first one, so a message goes to an agent that can hear it.
    const rank = (a) => ({ live: 0, idle: 1 }[a.state.split('/')[0]] ?? 2)
    const everyone = agents.filter((a) => kind(a.id) !== 'human' && kind(a.id) !== 'relay' && a.id !== me).sort((x, y) => rank(x) - rank(y))
    const targets = everyone.filter((a) => !hidden.includes(a.id))
    const gone = everyone.filter((a) => hidden.includes(a.id))
    const live = (a) => a.state.split('/')[0]
    mentionAgents = targets.map((a) => ({ id: a.id, name: nameOf(a.id), state: live(a) + (pullOnly(a.id) ? ' · pull-only' : ''), pull: pullOnly(a.id), away: live(a) === 'away' }))
    noteIds(agents.map((a) => a.id))
    const key = JSON.stringify(targets) + JSON.stringify(gone) + showHidden + me + JSON.stringify(nicks) + JSON.stringify(wake) + order.join()
    if (key === lastRoster) return
    lastRoster = key
    const removedBtn = $('removedBtn')
    removedBtn.hidden = gone.length === 0
    removedBtn.textContent = showHidden ? 'Hide removed agents' : `${gone.length} removed · show`
    const chip = (a, isGone) => {
      const c = document.createElement('span'); c.className = 'chip ' + brand(a.id) + (a.state.startsWith('live') ? ' live' : '') + (isGone ? ' gone' : '')
      c.title = (isGone ? `${a.id}\nRemoved from the room` : `${a.id}\nClick the name to rename`) + (pullOnly(a.id) ? `\n${PULL_TIP}` : '')
      const dot = document.createElement('i'); const ic = document.createElement('span'); ic.innerHTML = icon(a.id, true)
      const label = document.createElement('span'); label.textContent = nameOf(a.id)
      const st = document.createElement('span'); st.className = 'st'; st.textContent = live(a) + (pullOnly(a.id) ? ' · pull-only' : '')
      const x = document.createElement('button'); x.type = 'button'; x.className = 'x'
      x.textContent = isGone ? '↺' : '×'
      x.title = isGone ? 'Bring back into the room' : 'Remove from the room. It keeps running; this only hides it here.'
      x.setAttribute('aria-label', `${isGone ? 'Bring back' : 'Remove'} ${nameOf(a.id)}`)
      x.addEventListener('click', () => setHidden(a.id, !isGone))
      if (!isGone) label.addEventListener('click', () => startRename(c, label, a.id))
      c.append(ic.firstChild, label, dot, st, x); return c
    }
    $('chips').replaceChildren(...targets.map((a) => chip(a, false)), ...(showHidden ? gone.map((a) => chip(a, true)) : []))
    const prev = toSel.value
    toSel.replaceChildren(...(targets.length ? targets : [{ id: '', state: 'no agents present' }]).map((a) => {
      const o = document.createElement('option'); o.value = a.id; o.textContent = a.id ? `${nameOf(a.id)} (${live(a)}${pullOnly(a.id) ? ', pull-only' : ''})` : 'No agents present'; return o
    }))
    toSel.disabled = targets.length === 0
    if (chosenByUser && targets.some((a) => a.id === prev)) toSel.value = prev
    else if (targets.length) toSel.value = targets[0].id
    updateSend()
  }

  // Remove an agent from the room (or bring it back): only hides it here, kept in the plugin's settings. The agent itself keeps running.
  async function setHidden(id, flag) {
    try {
      const out = await ask('concord.hide', { id, hidden: flag }, 8000)
      hidden = out.hidden || []; lastRoster = ''; renderRoster(peopleAgents)
      setNote(flag ? `${nameOf(id)} removed from the room. It keeps running; messages already sent stay in the history.` : `${nameOf(id)} is back in the room.`, 'ok')
    } catch (e) { setNote((e && e.message) || String(e), 'bad') }
  }
  $('removedBtn').addEventListener('click', () => { showHidden = !showHidden; lastRoster = ''; renderRoster(peopleAgents) })

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

  // Who a message goes to: everyone @-mentioned in it, else the agent chosen in the "To" box.
  const mentioned = () => RM.parse(text.value, mentionAgents)
  const recipients = () => { const ids = mentioned(); return ids.length ? ids : (toSel.value ? [toSel.value] : []) }
  function updateSend() {
    sendBtn.disabled = sending || recipients().length === 0 || !text.value.trim()
    const ids = mentioned(); $('toline').textContent = ids.length ? 'Sending to: ' + ids.map(nameOf).join(', ') : ''
    const slow = recipients().filter(pullOnly)
    $('warn').textContent = slow.length ? `${slow.map(nameOf).join(', ')} can't be woken while idle: ${slow.length === 1 ? 'it reads' : 'they read'} this the next time ${slow.length === 1 ? 'it takes' : 'they take'} a turn.` : ''
  }
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
        me = people.me; nicks = people.nicknames || {}; hidden = people.hidden || []; wake = people.wake || {}; $('me').textContent = 'Sending as ' + nameOf(me) + ' (' + me + ')'
        noteIds(snap.messages.flatMap((m) => [m.from, m.to]))
        rosterAgents = people.agents.filter((a) => kind(a.id) !== 'human' && kind(a.id) !== 'relay' && a.id !== me && !hidden.includes(a.id))
        renderRoster(people.agents)
      } catch (e) { /* roster is optional for reading */ }
      noteIds(snap.messages.flatMap((m) => [m.from, m.to]))
      lastMsgs = snap.messages; repoRootShown = snap.repoRoot
      void refreshHandoffs()
      if (Date.now() - linkedAt > 30000) void refreshLinked()
      renderMessages(snap.messages)
    } catch (e) {
      setStatus('Unavailable', 'bad'); setBanner((e && e.message) || String(e))
    } finally { fetching = false }
  }

  async function send() {
    if (sendBtn.disabled) return
    const to = recipients(), content = text.value
    sending = true; updateSend(); text.disabled = true; setNote('Sending…')
    const ok = [], bad = []
    try {
      for (const id of to) {
        try {
          const out = await orca.plugin.request('concord.send', { to: id, content }, { timeoutMs: 20000 })
          ok.push(`${nameOf(id)} (${out.status}${out.delivery ? ', ' + out.delivery : ''})`)
        } catch (e) { bad.push(`${nameOf(id)}: ${(e && e.message) || e}`) }
      }
      if (ok.length) { text.value = ''; autosize(); closeMention() }
      const names = (ids) => ids.map(nameOf).join(', ')
      const slow = to.filter(pullOnly)
      const away = to.filter((id) => !pullOnly(id) && (mentionAgents.find((a) => a.id === id) || {}).away)
      const one = slow.length === 1
      const idle = (slow.length ? ` ${names(slow)} can't be woken while idle, so ${one ? 'it reads' : 'they read'} this the next time ${one ? 'it takes' : 'they take'} a turn. Type in ${one ? 'its' : 'their'} own chat or terminal to make ${one ? 'it' : 'them'} act now.` : '')
        + (away.length ? ` ${names(away)} ${away.length === 1 ? 'is' : 'are'} away and may not see this until active again.` : '')
      if (bad.length) setNote(`${ok.length ? 'Sent to ' + ok.join(', ') + '. ' : ''}Failed: ${bad.join('; ')}`, 'bad')
      else setNote(`Sent to ${ok.join(', ')}. "queued" is not "delivered", so watch the receipt.${idle}`, 'ok')
      stick = true
      await refresh(); toBottom()
    } finally { sending = false; text.disabled = false; updateSend(); text.focus() }
  }

  // ---- @mention popup: type @ to pick agents (or everyone); the message goes to each one mentioned. ----
  const menu = $('mention')
  let mItems = [], mIdx = 0, mStart = 0
  function closeMention() { menu.classList.remove('open'); mItems = [] }
  function showMention() {
    const a = RM.active(text.value, text.selectionStart)
    if (!a || mentionAgents.length === 0) { closeMention(); return }
    mItems = RM.candidates(a.query, mentionAgents); mStart = a.start
    if (mItems.length === 0) { closeMention(); return }
    mIdx = Math.min(mIdx, mItems.length - 1)
    menu.replaceChildren(...mItems.map((it, i) => {
      const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'option'); b.className = i === mIdx ? 'on' : ''
      const n = document.createElement('span'); n.textContent = '@' + it.label
      const h = document.createElement('span'); h.className = 'st'; h.textContent = it.hint
      b.append(n, h); b.addEventListener('mousedown', (e) => { e.preventDefault(); pickMention(i) }); return b
    }))
    menu.classList.add('open')
  }
  function pickMention(i) {
    const it = mItems[i]; if (!it) return
    const r = RM.insert(text.value, text.selectionStart, mStart, it.label)
    text.value = r.text; text.setSelectionRange(r.caret, r.caret)
    closeMention(); autosize(); updateSend(); text.focus()
  }
  text.addEventListener('input', () => { autosize(); mIdx = 0; showMention(); updateSend() })
  text.addEventListener('click', showMention)
  text.addEventListener('blur', () => setTimeout(closeMention, 120))
  text.addEventListener('keydown', (e) => {
    if (menu.classList.contains('open')) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); mIdx = (mIdx + (e.key === 'ArrowDown' ? 1 : -1) + mItems.length) % mItems.length; showMention(); return }
      if (((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') && !e.isComposing) { e.preventDefault(); pickMention(mIdx); return }
      if (e.key === 'Escape') { e.preventDefault(); closeMention(); return }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
  })
  toSel.addEventListener('change', () => { chosenByUser = true; updateSend() })
  sendBtn.addEventListener('click', send)


  // ---- Handoffs: pointers from Claude Chat / ChatGPT, delivered only after Jim clicks Accept ----
  const hoBox = $('handoffs')
  let hoJson = '', rosterAgents = [], busyIds = new Set(), hoTerminals = []
  const STATUS_TEXT = { typed: 'typed into terminal', delivered: 'delivered', queued: 'queued, not delivered', started: 'new chat started', rejected: 'rejected', invalid: 'invalid', duplicate: 'duplicate' }
  async function refreshHandoffs() {
    try {
      const out = await ask('handoff.list', {}, 12000)
      hoTerminals = out.terminals || []
      const json = JSON.stringify(out.handoffs) + JSON.stringify(hoTerminals) + JSON.stringify(rosterAgents) + lastMsgs.map((m) => m.id + (m.receipt || m.status)).join() + JSON.stringify(nicks) + [...busyIds].join()
      if (json === hoJson) return
      hoJson = json; renderHandoffs(out.handoffs)
    } catch (e) {
      hoJson = ''
      const msg = (e && e.message) || ''
      if (/policy/i.test(msg)) { hoBox.className = ''; hoBox.replaceChildren(); return }   // handoffs are off: no policy file
      hoBox.className = 'on'; const t = document.createElement('div'); t.className = 'hint'; t.textContent = 'Handoffs unavailable: ' + msg; hoBox.replaceChildren(t)
    }
  }
  function targetOptions(h) {
    const opts = []
    if (h.target.kind === 'agent') opts.push({ v: JSON.stringify({ kind: 'agent', id: h.target.id }), t: `${nameOf(h.target.id)} (named by the sender)` })
    else if (h.target.kind === 'new-chat') opts.push({ v: JSON.stringify({ kind: 'new-chat', agent: h.target.agent }), t: `New ${h.target.agent === 'codex' ? 'Codex' : 'Claude Code'} chat (named by the sender)` })
    else {
      for (const a of rosterAgents) opts.push({ v: JSON.stringify({ kind: 'agent', id: a.id }), t: `${nameOf(a.id)} (${a.state.split('/')[0]})` })
      for (const t of hoTerminals) opts.push({ v: JSON.stringify({ kind: 'terminal', handle: t.handle }), t: `Terminal: ${t.name} (${t.agentType}${t.eligible ? ', ready' : ', not ready: ' + t.reason})`, d: !t.eligible })
      opts.push({ v: JSON.stringify({ kind: 'new-chat', agent: 'claude' }), t: 'New Claude Code chat in this repo' })
      opts.push({ v: JSON.stringify({ kind: 'new-chat', agent: 'codex' }), t: 'New Codex chat in this repo' })
      opts.unshift({ v: '', t: 'Choose where to send…' })
    }
    return opts
  }
  function renderHandoffs(list) {
    const pending = list.filter((h) => h.status === 'pending')
    const rest = list.filter((h) => h.status !== 'pending').slice(0, 4)
    if (!pending.length && !rest.length) { hoBox.className = ''; hoBox.replaceChildren(); return }
    hoBox.className = 'on'
    const nodes = []
    if (pending.length) { const t = document.createElement('div'); t.className = 'hint'; t.textContent = `${pending.length} handoff${pending.length > 1 ? 's' : ''} waiting for you. Nothing is sent until you accept. Synthetic local source, unsigned.`; nodes.push(t) }
    for (const h of pending) nodes.push(handoffCard(h))
    for (const h of rest) nodes.push(handoffDone(h))
    hoBox.replaceChildren(...nodes)
  }
  function handoffCard(h) {
    const [b, label] = SENDER[h.sender] || ['other', h.sender]
    const el = document.createElement('div'); el.className = 'ho ' + b
    const top = document.createElement('div'); top.className = 'top'
    const av = document.createElement('div'); av.className = 'av'; av.innerHTML = iconB(b)
    const who = document.createElement('div'); who.className = 'who2'; who.textContent = `${label} hands off work`
    const key = document.createElement('span'); key.className = 'key ' + (h.jiraStatus === 'Agent Go' ? 'ok' : 'warn')
    key.textContent = `${h.jiraKey} · ${h.jiraStatus || 'status unknown'}`; key.title = h.jiraStatus === 'Agent Go' ? 'Released for Agent Go' : 'Not released for Agent Go: the receiving agent must refuse to do work'
    top.append(av, who, key)
    const scope = document.createElement('div'); scope.className = 'scope'; scope.textContent = h.scope
    const brief = document.createElement('div'); brief.className = 'brief'; brief.textContent = h.brief; brief.title = h.brief
    const act = document.createElement('div'); act.className = 'act'
    const sel = document.createElement('select'); sel.setAttribute('aria-label', 'Deliver to')
    const opts = targetOptions(h)
    for (const o of opts) { const op = document.createElement('option'); op.value = o.v; op.textContent = o.t; if (o.d) op.disabled = true; sel.append(op) }
    sel.disabled = h.target.kind !== 'any'
    const go = document.createElement('button'); go.className = 'go'; go.type = 'button'; go.textContent = 'Accept & send'
    const no = document.createElement('button'); no.type = 'button'; no.textContent = 'Reject'
    const note = document.createElement('div'); note.className = 'note'
    const run = async (name, input, okText) => {
      busyIds.add(h.id); go.disabled = no.disabled = true; note.className = 'note'; note.textContent = 'Working…'
      try { const out = await ask(name, input, 40000); busyIds.delete(h.id); hoJson = ''; await refreshHandoffs(); return out }
      catch (e) { busyIds.delete(h.id); go.disabled = no.disabled = false; note.className = 'note bad'; note.textContent = (e && e.message) || String(e) }
    }
    go.addEventListener('click', () => { if (sel.value) run('handoff.accept', { id: h.id, target: JSON.parse(sel.value) }) })
    no.addEventListener('click', () => run('handoff.reject', { id: h.id }))
    if (!opts.length || !sel.value) go.disabled = true
    sel.addEventListener('change', () => { go.disabled = !sel.value })
    act.append(sel, go, no)
    el.append(top, scope, brief, act, note)
    return el
  }
  function handoffDone(h) {
    const el = document.createElement('div'); el.className = 'ho done'
    const live = h.messageId ? lastMsgs.find((m) => m.id === h.messageId) : null
    const badge = document.createElement('span'); badge.className = 'key'; badge.textContent = (STATUS_TEXT[h.status] || h.status) + (live && (live.receipt || live.status) ? ' → ' + (live.receipt || live.status) : '')
    const text = document.createElement('span'); text.textContent = h.jiraKey ? `${h.jiraKey} — ${h.detail || ''}` : (h.detail || h.id)
    el.append(badge, text); return el
  }

  // ---- Pull in a Jira issue: copies what Orca's Tasks view does (browse, open, link to the workspace, start a chat). Never writes to Jira. ----
  const sheet = $('sheet'), jlist = $('jlist'), jdetail = $('jdetail'), jq = $('jq'), jnote = $('jnote'), jctx = $('jiractx')
  let jfilter = 'assigned', jsel = '', linked = [], jtimer = null, jseq = 0, linkedAt = 0
  const setJNote = (m, c) => { jnote.textContent = m || ''; jnote.className = c || '' }
  const pillClass = (cat) => cat === 'done' ? 'pill ok' : cat === 'indeterminate' ? 'pill warn' : 'pill'
  const isLinked = (k) => linked.some((x) => x.key === k)
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n }

  async function refreshLinked() {
    try { const out = await ask('jira.linked', {}, 12000); linked = out.issues || []; linkedAt = Date.now() } catch (e) { linked = [] }
    renderCtx()
  }
  function renderCtx() {
    if (!linked.length) { jctx.className = ''; jctx.replaceChildren(); return }
    jctx.className = 'on'
    const nodes = [el('span', 'lbl', 'Pulled in:')]
    for (const x of linked) { const b = el('button', '', `${x.key} · ${x.title}`); b.type = 'button'; b.title = 'Open ' + x.key; b.addEventListener('click', () => openSheet(x.key)); nodes.push(b) }
    jctx.replaceChildren(...nodes)
  }

  async function loadList() {
    const mine = ++jseq
    jlist.replaceChildren(el('div', 'hint', 'Loading…'))
    try {
      const out = await ask('jira.list', { filter: jfilter, query: jq.value }, 25000)
      if (mine !== jseq) return
      if (!out.issues.length) { jlist.replaceChildren(el('div', 'hint', jq.value.trim() ? 'No matching issues.' : 'No issues here.')); return }
      jlist.replaceChildren(...out.issues.map((i) => {
        const row = el('div', 'jrow' + (i.key === jsel ? ' sel' : ''))
        row.append(el('span', 'k', i.key), el('span', 't', i.title), el('span', pillClass(i.statusCategory), i.status || '—'))
        row.title = `${i.key} — ${i.title}`
        row.addEventListener('click', () => selectIssue(i.key))
        return row
      }))
    } catch (e) { if (mine === jseq) jlist.replaceChildren(el('div', 'hint', (e && e.message) || String(e))) }
  }

  async function selectIssue(key) {
    jsel = key; setJNote(''); for (const r of jlist.children) r.classList.toggle('sel', r.firstChild && r.firstChild.textContent === key)
    jdetail.className = 'on'; jdetail.replaceChildren(el('div', 'hint', 'Loading ' + key + '…'))
    try { renderDetail((await ask('jira.issue', { key }, 20000)).issue) } catch (e) { jdetail.replaceChildren(el('div', 'hint', (e && e.message) || String(e))) }
  }

  function renderDetail(i) {
    const meta = el('div', 'meta2'); meta.append(el('span', pillClass(i.statusCategory), i.status || '—'), el('span', '', i.type), el('span', '', i.project))
    if (i.parent) meta.append(el('span', '', 'in ' + i.parent)); for (const l of i.labels) meta.append(el('span', 'pill', l))
    const desc = el('div', 'desc', i.description || '(no description)')
    const row1 = el('div', 'row2')
    const link = el('button', 'primary', isLinked(i.key) ? 'Pulled in ✓ — unlink' : 'Pull into this room'); link.type = 'button'
    link.addEventListener('click', async () => {
      link.disabled = true
      try { await ask(isLinked(i.key) ? 'jira.unlink' : 'jira.link', { key: i.key }, 20000); await refreshLinked(); setJNote(isLinked(i.key) ? `${i.key} is linked to this workspace, the way Orca's own Tasks view links it.` : `${i.key} was unlinked.`, 'ok'); renderDetail(i) }
      catch (e) { setJNote((e && e.message) || String(e), 'bad'); link.disabled = false }
    })
    row1.append(link)
    if (i.url) { const open = el('button', '', 'Open in Jira'); open.type = 'button'; open.addEventListener('click', () => orca.host.call('shell.openExternal', { url: i.url }).catch(() => {})); row1.append(open) }
    const row2 = el('div', 'row2'); row2.append(el('span', 'hint', 'Start a chat from it:'))
    const agent = el('select'); agent.setAttribute('aria-label', 'Agent'); for (const [v, t] of [['claude', 'Claude Code'], ['codex', 'Codex']]) { const o = el('option', '', t); o.value = v; agent.append(o) }
    const tpl = el('select'); tpl.setAttribute('aria-label', 'Prompt template'); for (const [v, t] of [['review', 'Review (read-only)'], ['plan', 'Plan (no changes)'], ['implement', 'Implement (needs Agent Go + claim)']]) { const o = el('option', '', t); o.value = v; tpl.append(o) }
    const go = el('button', '', 'Start chat'); go.type = 'button'
    go.addEventListener('click', async () => {
      go.disabled = true; setJNote('Starting…')
      try { const out = await ask('jira.startChat', { key: i.key, agent: agent.value, template: tpl.value }, 40000); setJNote(`Started: ${out.detail}`, 'ok') }
      catch (e) { setJNote((e && e.message) || String(e), 'bad') } finally { go.disabled = false }
    })
    row2.append(agent, tpl, go)
    jdetail.className = 'on'; jdetail.replaceChildren(el('h3', '', `${i.key} — ${i.title}`), meta, desc, row1, row2)
  }

  function openSheet(key) {
    sheet.classList.add('open'); setJNote('')
    if (key) { jq.value = key; jdetail.className = ''; jdetail.replaceChildren(); jsel = '' }
    loadList().then(() => { if (key) selectIssue(key) }); jq.focus()
  }
  $('jiraBtn').addEventListener('click', () => openSheet())

  // ---- Agent menu: start Claude Code or Codex (chat or terminal) in this room's repository, the way Orca's own + menu launches an agent. It joins the room and waits. ----
  const amenu = $('amenu'), agentBtn = $('agentBtn')
  const setMenu = (open) => { amenu.classList.toggle('open', open); agentBtn.setAttribute('aria-expanded', String(open)) }
  agentBtn.addEventListener('click', (e) => { e.stopPropagation(); setMenu(!amenu.classList.contains('open')) })
  document.addEventListener('click', (e) => { if (!amenu.contains(e.target)) setMenu(false) })
  amenu.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-agent]'); if (!b) return
    const label = `${b.dataset.agent === 'codex' ? 'Codex' : 'Claude Code'} ${b.dataset.surface}`
    const items = amenu.querySelectorAll('button')
    for (const x of items) x.disabled = true
    setMenu(false); setNote(`Starting ${label}…`)
    try { await ask('agent.start', { agent: b.dataset.agent, surface: b.dataset.surface }, 40000); setNote(`Started ${label}. It shows up here once it joins.`, 'ok') }
    catch (err) { setNote(`Could not start ${label}: ${(err && err.message) || err}`, 'bad') }
    finally { for (const x of items) x.disabled = false; refresh() }
  })
  $('jclose').addEventListener('click', () => sheet.classList.remove('open'))
  $('jtabs').addEventListener('click', (e) => { const b = e.target.closest('button[data-f]'); if (!b) return; jfilter = b.dataset.f; jq.value = ''; for (const x of $('jtabs').children) x.classList.toggle('on', x === b); loadList() })
  jq.addEventListener('input', () => { clearTimeout(jtimer); jtimer = setTimeout(loadList, 350) })
  jq.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(jtimer); loadList() } })
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { sheet.classList.remove('open'); setMenu(false) } })
  void refreshLinked()

  // Custom editor tab: Orca expects the editor to ask for its (empty, read-only) document once.
  try { if (orca.document && orca.document.get) orca.document.get().catch(() => {}) } catch (e) { /* panel surface has no document */ }

  refresh(); setInterval(refresh, 3000)
})()

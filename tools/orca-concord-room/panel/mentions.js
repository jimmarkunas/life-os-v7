// @mentions for the Concord Room composer. Pure helpers (no DOM, no network): loaded before room.js and unit-tested with node:vm.
// An "agent" here is { id, name, state }: present, not removed from the room, and not you.
window.RoomMentions = (() => {
  const EVERYONE = ['everyone', 'all']

  // The "@..." being typed right now, if the caret is inside one: the text before the caret ends with "@" and some non-space
  // characters, and that "@" starts the text or follows whitespace (so an e-mail address is not a mention).
  function active(text, caret) {
    const before = text.slice(0, caret)
    const m = /(^|\s)@([^\s@]*)$/.exec(before)
    return m ? { start: before.length - m[2].length - 1, query: m[2] } : null
  }

  // What can be picked for what has been typed after the "@": "everyone" first, then agents whose name starts with it.
  function candidates(query, agents) {
    const q = query.toLowerCase()
    const items = []
    if (agents.length > 0 && EVERYONE.some((w) => w.startsWith(q))) items.push({ everyone: true, label: 'everyone', hint: `${agents.length} present` })
    for (const a of agents) if (a.name.toLowerCase().startsWith(q)) items.push({ id: a.id, label: a.name, hint: a.state })
    return items
  }

  // Replace the "@query" with "@Name " and say where the caret goes.
  function insert(text, caret, start, label) {
    return { text: text.slice(0, start) + '@' + label + ' ' + text.slice(caret), caret: start + label.length + 2 }
  }

  // Everyone @-mentioned in the finished text, as agent ids. Longest names first so "@Claude 2" is not read as "@Claude";
  // a name must end at the end of the text or at a space or punctuation. "@everyone" / "@all" means every agent given.
  function parse(text, agents) {
    const names = [...agents.map((a) => ({ id: a.id, name: a.name })), ...EVERYONE.map((w) => ({ everyone: true, name: w }))]
      .sort((x, y) => y.name.length - x.name.length)
    const ids = new Set()
    let everyone = false
    const re = /(^|\s)@/g
    let m
    while ((m = re.exec(text))) {
      const rest = text.slice(m.index + m[0].length).toLowerCase()
      const hit = names.find((n) => rest.startsWith(n.name.toLowerCase()) && (rest.length === n.name.length || /[\s.,!?;:)\]]/.test(rest[n.name.length])))
      if (!hit) continue
      if (hit.everyone) everyone = true
      else ids.add(hit.id)
    }
    return everyone ? agents.map((a) => a.id) : [...ids]
  }

  return { active, candidates, insert, parse }
})()

declare module 'orca' {
  interface PluginRequests {
    'concord.messages': { input: Record<string, never>; output: { repoRoot: string; messages: Array<{ id: string; from: string; to: string; content: string; at: string; status: string; replyTo: string | null; receipt: string | null }> } }
    'concord.agents': { input: Record<string, never>; output: { agents: Array<{ id: string; state: string }>; me: string } }
    'concord.send': { input: { to: string; content: string }; output: { messageId: string; status: string; delivery: string; outlook: string; from: string; to: string } }
  }
}
export {}

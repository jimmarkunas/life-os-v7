export {}
declare module 'orca' {
  interface PluginRequests {
    'concord.messages': { input: Record<string, never>; output: { repoRoot: string; messages: Array<{ id: string; from: string; to: string; content: string; at: string; status: string; replyTo: string | null; receipt: string | null }> } }
  }
}

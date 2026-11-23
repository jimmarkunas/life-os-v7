#!/bin/sh
# Prints which LIFE OS agent this session is: OC (Claude Code inside VS Code)
# or MC (Claude Code inside the Claude Mac app). The app decides the name,
# never the machine. Prints UNKNOWN when no signal is present; then take the
# name from Jim's start prompt, or ask him once at session start.
bundle="${__CFBundleIdentifier:-}"
entry="${CLAUDE_CODE_ENTRYPOINT:-}"
name=UNKNOWN
if [ -n "${VSCODE_PID:-}${VSCODE_IPC_HOOK_CLI:-}${VSCODE_IPC_HOOK:-}" ] || [ "${TERM_PROGRAM:-}" = vscode ] \
   || case "$bundle" in com.microsoft.VSCode*) true;; *) false;; esac \
   || case "$entry" in *vscode*) true;; *) false;; esac; then
  name=OC
elif case "$bundle" in com.anthropic.*) true;; *) false;; esac \
   || case "$entry" in *desktop*) true;; *) false;; esac; then
  name=MC
fi
echo "$name"
echo "signals: bundle=${bundle:-none} entrypoint=${entry:-none} term=${TERM_PROGRAM:-none}" >&2

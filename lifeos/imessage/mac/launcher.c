/* COMM-1.4 local proof: the one signed executable that holds the Messages privacy grant (Full Disk Access) on the Mac Mini.

   launchd starts it per connection on the LaunchAgent's Unix socket (inetd style: the connection is stdin/stdout). It takes no arguments and reads no
   environment: it spawns the fixed, root-owned attention tool with a fixed argv and a minimal environment, keeps that child as its own (so macOS attributes the
   child's access to this binary, never to OpenClaw.app, Terminal or python3), and kills it after a fixed deadline. */
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#define TOOL "/usr/local/libexec/lifeos-imessage/attention_mcp.py"
#define DEADLINE_TICKS 100                                    /* 100 x 100 ms = 10 s per connection */

int main(void) {
    char *argv[] = {"/usr/bin/python3", "-I", "-S", "-B", TOOL, NULL};
    char *envp[] = {"PATH=/usr/bin:/bin", "LANG=en_US.UTF-8", NULL};
    pid_t pid;
    int status;
    if (posix_spawn(&pid, argv[0], NULL, NULL, argv, envp) != 0)
        return 1;
    for (int i = 0; i < DEADLINE_TICKS; i++) {
        pid_t done = waitpid(pid, &status, WNOHANG);
        if (done == pid)
            return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
        if (done < 0)
            return 1;
        usleep(100000);
    }
    kill(pid, SIGKILL);
    waitpid(pid, &status, 0);
    return 1;
}

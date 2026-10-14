/* COMM-1.4: the loopback relay between Tailscale Serve and the signed Messages helper's Unix socket. It holds no privacy grant and never touches Messages.

   launchd starts it per connection on 127.0.0.1 (inetd style: the connection is stdin/stdout). Tailscale Serve forwards each tailnet connection here
   with a PROXY protocol v2 header carrying the WireGuard-authenticated source address. The relay:
     1. requires a well-formed v2 PROXY header for TCP over IPv4/IPv6 whose source is exactly the Oracle Gateway (fixed at build time, never in Git);
     2. requires the request to be exactly "attention\n";
     3. sends the one fixed tools/call to the helper's socket and returns its single reply line, at most MAX_REPLY bytes.
   Anything else closes the connection with no output. No arguments, no environment input, no caller-chosen path, command or query; a fixed deadline. */
#include <arpa/inet.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#ifndef ALLOWED_V4
#error "build with -DALLOWED_V4=\"<Oracle tailnet IPv4>\""
#endif
#ifndef ALLOWED_V6
#error "build with -DALLOWED_V6=\"<Oracle tailnet IPv6>\""
#endif

#define SOCKET_IN_HOME "/Library/Application Support/LifeOS/imessage-attention.sock"
#define REQUEST "attention\n"
#define CALL "{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\", \"params\": {\"name\": \"imessage_attention\", \"arguments\": {}}}\n"
#define MAX_PROXY_TAIL 216                                    /* v2 address block plus room for Serve's TLVs; anything larger is refused */
#define MAX_REPLY 2048
#define DEADLINE_SECONDS 8

static const unsigned char SIG[12] = {0x0D, 0x0A, 0x0D, 0x0A, 0x00, 0x0D, 0x0A, 0x51, 0x55, 0x49, 0x54, 0x0A};

static int read_full(int fd, unsigned char *buf, size_t n) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r <= 0)
            return -1;
        got += (size_t)r;
    }
    return 0;
}

static int write_full(int fd, const char *buf, size_t n) {
    while (n > 0) {
        ssize_t w = write(fd, buf, n);
        if (w <= 0)
            return -1;
        buf += w;
        n -= (size_t)w;
    }
    return 0;
}

static int source_is_oracle(void) {
    unsigned char head[16], tail[MAX_PROXY_TAIL], want[16];
    if (read_full(0, head, sizeof head) != 0 || memcmp(head, SIG, sizeof SIG) != 0 || head[12] != 0x21)
        return 0;                                             /* not PROXY v2 with the PROXY command (LOCAL is refused) */
    size_t len = ((size_t)head[14] << 8) | head[15];
    if (len > sizeof tail || read_full(0, tail, len) != 0)
        return 0;
    if (head[13] == 0x11)                                     /* TCP over IPv4: src(4) dst(4) sport(2) dport(2) */
        return len >= 12 && inet_pton(AF_INET, ALLOWED_V4, want) == 1 && memcmp(tail, want, 4) == 0;
    if (head[13] == 0x21)                                     /* TCP over IPv6: src(16) dst(16) sport(2) dport(2) */
        return len >= 36 && inet_pton(AF_INET6, ALLOWED_V6, want) == 1 && memcmp(tail, want, 16) == 0;
    return 0;
}

static int request_is_attention(void) {
    char req[sizeof REQUEST];
    size_t n = 0;
    while (n < sizeof REQUEST - 1) {                          /* read only up to the fixed request length: longer input never matches */
        if (read(0, req + n, 1) != 1)
            return 0;
        if (req[n++] == '\n')
            break;
    }
    return n == sizeof REQUEST - 1 && memcmp(req, REQUEST, n) == 0;
}

int main(void) {
    char reply[MAX_REPLY + 1];
    size_t got = 0;
    struct sockaddr_un addr;
    struct passwd *me = getpwuid(getuid());
    signal(SIGPIPE, SIG_IGN);
    alarm(DEADLINE_SECONDS);
    if (!source_is_oracle() || !request_is_attention() || me == NULL || me->pw_dir == NULL)
        return 1;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
#ifdef TEST_HELPER_SOCKET                                     /* tests only: the installer never defines it */
    if (snprintf(addr.sun_path, sizeof addr.sun_path, "%s", TEST_HELPER_SOCKET) >= (int)sizeof addr.sun_path)
        return 1;
#else
    if (snprintf(addr.sun_path, sizeof addr.sun_path, "%s%s", me->pw_dir, SOCKET_IN_HOME) >= (int)sizeof addr.sun_path)
        return 1;
#endif
    int helper = socket(AF_UNIX, SOCK_STREAM, 0);
    if (helper < 0 || connect(helper, (struct sockaddr *)&addr, sizeof addr) != 0 || write_full(helper, CALL, sizeof CALL - 1) != 0)
        return 1;
    shutdown(helper, SHUT_WR);
    for (;;) {
        ssize_t r = read(helper, reply + got, MAX_REPLY + 1 - got);
        if (r < 0)
            return 1;
        if (r == 0)
            break;
        got += (size_t)r;
        if (got > MAX_REPLY)
            return 1;                                         /* oversized: nothing is forwarded */
    }
    close(helper);
    if (got == 0 || reply[got - 1] != '\n' || memchr(reply, '\n', got) != reply + got - 1)
        return 1;                                             /* exactly one complete line */
    return write_full(1, reply, got) == 0 ? 0 : 1;
}

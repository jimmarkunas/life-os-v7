/* COMM-1.4: the loopback relay between Tailscale Serve and the signed Messages helper's Unix socket. It holds no privacy grant and never touches Messages.

   launchd starts it per connection on 127.0.0.1 (inetd style: the connection is stdin/stdout). Tailscale Serve forwards each tailnet connection here
   with a PROXY protocol v2 header carrying the WireGuard-authenticated source address. The relay:
     1. requires a well-formed v2 PROXY header for TCP over IPv4/IPv6 whose source is exactly the Oracle Gateway (fixed at build time, never in Git);
     2. requires one request line from the fixed grammar: "attention", "counts", "recent <1-72>" or "conversation <1-999999999>" (digits only, no
        leading zero), at most MAX_REQUEST bytes;
     3. builds the matching tools/call itself from the validated integer and returns the helper's single reply line, at most MAX_REPLY bytes.
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
#define CALL_HEAD "{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\", \"params\": "
#define MAX_PROXY_TAIL 216                                    /* v2 address block plus room for Serve's TLVs; anything larger is refused */
#define MAX_REQUEST 32
#define MAX_REPLY 262144                                      /* 50 messages of at most 2000 characters, JSON-escaped */
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

static long number(const char *digits, long high) {         /* 1..high from plain decimal digits, no sign, no leading zero; -1 otherwise */
    long n = 0;
    if (digits[0] < '1' || digits[0] > '9')
        return -1;
    for (const char *c = digits; *c; c++) {
        if (*c < '0' || *c > '9' || (n = n * 10 + (*c - '0')) > high)
            return -1;
    }
    return n;
}

static int request_to_call(char *call, size_t size) {       /* read one request line and write the matching fixed call; 0 when it is not in the grammar */
    char req[MAX_REQUEST + 1];
    size_t n = 0;
    long arg;
    for (;;) {
        if (n == MAX_REQUEST || read(0, req + n, 1) != 1)
            return 0;                                         /* longer than any valid request, or the caller stopped: refused */
        if (req[n] == '\n')
            break;
        n++;
    }
    req[n] = '\0';
    if (strcmp(req, "attention") == 0)
        return snprintf(call, size, CALL_HEAD "{\"name\": \"imessage_attention\", \"arguments\": {}}}\n") < (int)size;
    if (strcmp(req, "counts") == 0)
        return snprintf(call, size, CALL_HEAD "{\"name\": \"imessage_counts\", \"arguments\": {}}}\n") < (int)size;
    if (strncmp(req, "recent ", 7) == 0 && (arg = number(req + 7, 72)) > 0)
        return snprintf(call, size, CALL_HEAD "{\"name\": \"imessage_recent\", \"arguments\": {\"hours\": %ld}}}\n", arg) < (int)size;
    if (strncmp(req, "conversation ", 13) == 0 && (arg = number(req + 13, 999999999L)) > 0)
        return snprintf(call, size, CALL_HEAD "{\"name\": \"imessage_conversation\", \"arguments\": {\"chat\": %ld}}}\n", arg) < (int)size;
    return 0;
}

static char reply[MAX_REPLY + 1];

int main(void) {
    char call[256];
    size_t got = 0;
    struct sockaddr_un addr;
    struct passwd *me = getpwuid(getuid());
    signal(SIGPIPE, SIG_IGN);
    alarm(DEADLINE_SECONDS);
    if (!source_is_oracle() || !request_to_call(call, sizeof call) || me == NULL || me->pw_dir == NULL)
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
    if (helper < 0 || connect(helper, (struct sockaddr *)&addr, sizeof addr) != 0 || write_full(helper, call, strlen(call)) != 0)
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

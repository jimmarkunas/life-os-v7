"""LIFE-576 option (a): run the Jobright resolver in the persistent, signed-in headless Chromium on the Oracle VM, and fall back to the runner's own browser.

The runner reaches the VM over the private Tailscale network with an SSH port-forward (the same host the store jump already uses). The forward key belongs to its own
user on the VM and may only open 127.0.0.1:9223, which is the browser's debugging port; nothing is opened to the internet. The browser keeps Jim's Jobright sign-in.
Switch: the repository variable JOBRIGHT_ON_ORACLE (anything but "on" sends the stage to the runner's own browser, same as before). Every other case falls back too:
no key, VM unreachable, browser busy (a page of someone else is open), signed out, or the connection lost part-way. The caller prints one line, JOBRIGHT_PATH=..., with the reason.
Values never reach a log: only these fixed reason words."""
import asyncio
import os
import socket
import subprocess
import tempfile

REMOTE_PORT = 9223
FORWARD_USER = "v7-browser-fwd"
HOME = "https://jobright.ai/"
PROFILE_MARK = "xpath=//span[text()='Profile']"
TUNNEL_WAIT_SECONDS = 15


def wanted(environ):
    return (environ.get("JOBRIGHT_ON_ORACLE") or "off").strip().lower() == "on"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ssh_command(key_path, hosts_path, host, local_port, user=FORWARD_USER):
    return ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={hosts_path}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            "-i", key_path, "-L", f"127.0.0.1:{local_port}:127.0.0.1:{REMOTE_PORT}", f"{user}@{host}"]


class Unreachable(Exception):
    pass


class Tunnel:
    """ssh -N -L for the life of the with-block; the key and host list are written to a private temp folder and removed."""
    def __init__(self, environ, spawn=subprocess.Popen, wait=TUNNEL_WAIT_SECONDS):
        self.environ, self.spawn, self.wait, self.proc, self.port = environ, spawn, wait, None, None

    def __enter__(self):
        key = (self.environ.get("LIFEOS_BROWSER_SSH_KEY") or "").strip()
        host = (self.environ.get("LIFEOS_ACQ_SSH_JUMP_HOST") or "").strip()
        hosts = (self.environ.get("LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS") or "").strip()
        if not key:
            raise Unreachable("no_key")
        if not host or not hosts:
            raise Unreachable("no_host")
        self.dir = tempfile.TemporaryDirectory()
        os.chmod(self.dir.name, 0o700)
        key_path, hosts_path = os.path.join(self.dir.name, "k"), os.path.join(self.dir.name, "h")
        for path, text in ((key_path, key), (hosts_path, hosts)):
            with open(path, "w") as f:
                f.write(text.replace("\\n", "\n").rstrip("\n") + "\n")
            os.chmod(path, 0o600)
        self.port = free_port()
        self.proc = self.spawn(ssh_command(key_path, hosts_path, host, self.port), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return self
            except OSError:
                time.sleep(0.25)
        self.__exit__()
        raise Unreachable("unreachable")

    def __exit__(self, *exc):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:                                   # noqa: BLE001
                self.proc.kill()
        if getattr(self, "dir", None):
            self.dir.cleanup()
        return False


async def signed_in(page):
    try:
        await page.goto(HOME, wait_until="domcontentloaded", timeout=30000)
        await page.locator(PROFILE_MARK).first.wait_for(timeout=15000)
        return True
    except Exception:                                           # noqa: BLE001
        return False


async def attempt(pw, urls, environ, follow, pause_ms, tunnel=Tunnel):
    """-> (results, reason). results is None when the runner's own browser must do the work; reason is one fixed word."""
    if not wanted(environ):
        return None, "off"
    try:
        with tunnel(environ) as t:
            try:
                browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{t.port}", timeout=15000)
            except Exception:                                   # noqa: BLE001
                return None, "unreachable"
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                if [p for p in context.pages if (p.url or "about:blank") != "about:blank"]:
                    return None, "busy"
                page = await context.new_page()
                if not await signed_in(page):
                    return None, "signed_out"
                results = []
                for url in urls:
                    try:
                        results.append(await follow(context, page, url))
                    except Exception as error:                  # noqa: BLE001
                        if "closed" in str(error).lower() or "disconnected" in str(error).lower():
                            return None, "lost"
                        results.append({"outcome": "chain_failed"})
                    await page.wait_for_timeout(pause_ms)
                await page.close()
                return results, "ok"
            except Exception:                                   # noqa: BLE001
                return None, "lost"
            finally:
                try:
                    await browser.close()
                except Exception:                               # noqa: BLE001
                    pass
    except Unreachable as error:
        return None, str(error)

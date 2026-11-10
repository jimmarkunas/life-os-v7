"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
import struct
import ipaddress


RUNNER, OTHER = "100.64.0.20", "100.64.0.21"                                  # invented tailnet addresses


SIG = b"\r\n\r\n\x00\r\nQUIT\n"


def v2(src, cmd=0x21):
    a = ipaddress.ip_address(src)
    fam, size, dst = (0x11, 12, ipaddress.IPv4Address("127.0.0.1").packed) if a.version == 4 else (0x21, 36, ipaddress.IPv6Address("::1").packed)
    return SIG + bytes([cmd, fam]) + struct.pack("!H", size) + a.packed + dst + struct.pack("!HH", 50000, 47342)


def whois(ip):
    return {"Node": {"Tags": ["tag:v7-runner"]}} if ip == RUNNER else {"Node": {"Tags": None}}

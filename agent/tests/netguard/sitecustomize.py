"""Network guard for Python child processes started by tests.

`spawn_offline_child` puts this directory first on PYTHONPATH, so Python
imports this module at startup. It blocks outbound connections, datagrams
and name lookups (the gethostby* calls reach libc without getaddrinfo), and
prints a marker so a test can prove the guard was loaded. Unix domain
sockets stay allowed, matching `--allow-unix-socket` in pytest.
"""

import socket
import sys

GUARD_MARKER = "advisor offline guard: guard loaded (python)"
GUARD_ERROR_TEXT = "advisor offline guard: network access is blocked"


class NetworkBlockedError(RuntimeError):
    """Raised for any network attempt in a guarded child."""


def _blocked(what: str):
    raise NetworkBlockedError(f"{GUARD_ERROR_TEXT} ({what})")


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_sendto = socket.socket.sendto
_original_sendmsg = socket.socket.sendmsg


def _is_unix(sock: socket.socket) -> bool:
    return getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX


def _guarded_connect(self, address):
    if _is_unix(self):
        return _original_connect(self, address)
    _blocked(f"socket.connect {address!r}")


def _guarded_connect_ex(self, address):
    if _is_unix(self):
        return _original_connect_ex(self, address)
    _blocked(f"socket.connect_ex {address!r}")


def _guarded_sendto(self, *args):
    if _is_unix(self):
        return _original_sendto(self, *args)
    _blocked("socket.sendto")


def _guarded_sendmsg(self, *args):
    if _is_unix(self):
        return _original_sendmsg(self, *args)
    _blocked("socket.sendmsg")


def _guarded_lookup(name: str):
    def guarded(*args, **kwargs):
        _blocked(f"socket.{name} {args[:1]!r}")

    return guarded


def _guarded_create_connection(address, *args, **kwargs):
    _blocked(f"socket.create_connection {address!r}")


def _guarded_getaddrinfo(host, *args, **kwargs):
    _blocked(f"socket.getaddrinfo {host!r}")


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.create_connection = _guarded_create_connection
socket.getaddrinfo = _guarded_getaddrinfo
socket.socket.sendto = _guarded_sendto
socket.socket.sendmsg = _guarded_sendmsg
for _name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
    setattr(socket, _name, _guarded_lookup(_name))

print(GUARD_MARKER, file=sys.stderr, flush=True)

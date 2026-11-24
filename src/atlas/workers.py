"""Several worker processes behind one port (M24, NFR-01).

The plan said `SO_REUSEPORT`. Windows does not have it, and this project runs where the
operator does, so the portable form is the older one: the **parent** creates the listening
socket, and each child inherits it. On POSIX the child gets the file descriptor; on Windows
`multiprocessing` duplicates it with `WSADuplicateSocket`. Either way there is exactly one
accept queue and one port, which is what the published contract says.

Why processes at all: the request path is bytecode, so threads contend on one GIL. Two
processes do not share one, which is the whole point of the phase.

What each worker owns:

- its own `Store` connection, `HybridIndex` and warm tenant views, so a query never crosses a
  process boundary;
- its own `Admission` bound and its own `METRICS` counters, because a `Semaphore` and a
  `deque` cannot be shared across processes for free.

That last point is a real limitation, not a detail: `/v1/metrics` answers from **whichever
worker accepted the connection**, so its numbers describe one worker, not the pool. `pid` in
`/v1/healthz` is how a caller can tell which one answered (L13).
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from http.server import ThreadingHTTPServer

from .config import SETTINGS, Settings
from .handler import make_handler
from .server import LISTEN_BACKLOG, ServerState

__all__ = ["listen", "serve_workers"]


def listen(host: str, port: int) -> socket.socket:
    """The one listening socket every worker accepts from."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(LISTEN_BACKLOG)
    return sock


def serve_in_child(sock: socket.socket, db_path: str, settings: Settings) -> int:
    """Serve on an inherited socket until the parent stops us. One process, one store."""
    state = ServerState(db_path, settings=settings)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state), bind_and_activate=False)
    server.socket = sock
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.store.close()
        server.server_close()
    return 0


def _spawn(sock: socket.socket, db_path: str, settings: Settings) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=serve_in_child, args=(sock, db_path, settings), daemon=False
    )
    process.start()
    return process


def serve_workers(
    db: str,
    host: str,
    port: int,
    workers: int,
    settings: Settings = SETTINGS,
) -> int:
    """Supervise `workers` children on one port; restart any that dies."""
    if workers < 2:
        raise ValueError("serve_workers needs at least 2 workers; use server.serve for one")
    sock = listen(host, port)
    children = [_spawn(sock, db, settings) for _ in range(workers)]
    print(f"[atlas] {workers} workers on http://{host}:{port} over {db}")
    try:
        while True:
            for index, child in enumerate(children):
                if not child.is_alive():
                    print(f"[atlas] worker {index} exited ({child.exitcode}); restarting")
                    children[index] = _spawn(sock, db, settings)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[atlas] shutting down")
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=5)
        sock.close()
    return 0

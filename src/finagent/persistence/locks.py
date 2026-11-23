"""One process at a time may drive a run. The operating system releases the lock if a process dies."""

import fcntl
import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_WAIT_S = 120.0
_POLL_S = 0.02


class RunBusy(RuntimeError):
    """Another command is still driving this run."""


@contextmanager
def run_lock(home: Path, run_id: str, wait_s: float = LOCK_WAIT_S) -> Iterator[None]:
    directory = Path(home) / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(run_id.encode()).hexdigest()
    with (directory / f"{name}.lock").open("a") as handle:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RunBusy(f"run {run_id} is busy") from None
                time.sleep(_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

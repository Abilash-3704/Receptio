"""Database contention evidence from Postgres itself, sampled while a load run is going.

Peak number of connections, peak number of sessions waiting on a lock, and the deltas of the
cluster counters for this database (deadlocks, rollbacks, commits) between start and finish."""

import threading
import time
from dataclasses import dataclass

from sqlalchemy import Engine, text


@dataclass
class PgStats:
    samples: int = 0
    peak_connections: int = 0
    peak_lock_waiters: int = 0
    deadlocks: int = 0
    rollbacks: int = 0
    commits: int = 0


_ACTIVITY = text(
    "select count(*), count(*) filter (where wait_event_type = 'Lock') "
    "from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()"
)
_COUNTERS = text(
    "select deadlocks, xact_rollback, xact_commit from pg_stat_database "
    "where datname = current_database()"
)


class PgSampler:
    def __init__(
        self, engine: Engine, every_seconds: float = 0.1, settle_seconds: float = 11.0
    ) -> None:
        self._engine = engine
        self._settle = settle_seconds
        self._every = every_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.stats = PgStats()
        self._start: tuple[int, int, int] = (0, 0, 0)

    def _counters(self) -> tuple[int, int, int]:
        with self._engine.connect() as connection:
            row = connection.execute(_COUNTERS).one()
            return int(row[0]), int(row[1]), int(row[2])

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            with self._engine.connect() as connection:
                total, waiting = connection.execute(_ACTIVITY).one()
            self.stats.samples += 1
            self.stats.peak_connections = max(self.stats.peak_connections, int(total))
            self.stats.peak_lock_waiters = max(self.stats.peak_lock_waiters, int(waiting))

    def __enter__(self) -> "PgSampler":
        self._start = self._counters()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        # A busy backend publishes its counters at most once a second, an idle one (a pooled
        # connection) only after 10 s, so wait that out before reading the totals.
        time.sleep(self._settle)
        deadlocks, rollbacks, commits = self._counters()
        self.stats.deadlocks = deadlocks - self._start[0]
        self.stats.rollbacks = rollbacks - self._start[1]
        self.stats.commits = commits - self._start[2]

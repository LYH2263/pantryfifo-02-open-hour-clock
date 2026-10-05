import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    # timeout = sqlite busy timeout: writers wait for each other instead of
    # failing with "database is locked" under the three-way write race.
    c = sqlite3.connect(db_path(), timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c

@contextmanager
def write_tx():
    """Serialized read-modify-write transaction.

    BEGIN IMMEDIATE takes the database write lock up front, so concurrent
    writers (open-confirm / consume / expire-sweep) serialize: each one sees
    the previous one's committed state, and a lot row ends with exactly one
    qty_remain, status and opened flag — never a mix of three writers.
    """
    c = connect()
    begun = False
    try:
        c.execute("BEGIN IMMEDIATE")
        begun = True
        yield c
        c.execute("COMMIT")
    except BaseException:
        if begun:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        c.close()

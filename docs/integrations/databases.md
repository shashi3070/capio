# Database backends: SQLite, PostgreSQL, MySQL, MongoDB

Capio does not ship database drivers — it ships the **store protocol**, and any
object implementing it is a database backend. The capabilities that persist
state (`idempotent`, `memory`, `rag`, `ingest`, `cron`, `publish` outbox) all
resolve a backend named by their `store`/`backend` option, defaulting to the
in-memory `store.memory`.

Swapping in a real database gives you durability, cross-process sharing, and
queryable state with **no changes to your decorated functions** — only the
backend name in the decorator changes.

## 1. The store protocol

| Method | Meaning |
|---|---|
| `put(ns, key, value, ttl=None)` | Upsert `value` under `ns:key`; optional TTL in seconds |
| `get(ns, key, default)` | Read a value, or `default` when missing/expired |
| `delete(ns, key)` | Remove a key; `True` if it existed |
| `items(ns)` | All live `(key, value)` pairs in a namespace |
| `scan(prefix)` | `(ns, key, value)` triples whose `ns:key` starts with `prefix` |
| `sequence(ns)` | Monotonic per-namespace counter (used for ordering / memory ids) |
| `clear(ns=None)` | Drop one namespace or everything |

Namespaces let `store.sqlite` serve `memory`, `rag`, `ingest`, `idempotent`,
`cron`, and the `publish` outbox **from one connection** — each capability just
uses a different namespace.

> **Pickling values.** Capabilities store arbitrary Python objects (dicts,
> embeddings, cached responses). Backends below serialize with `pickle`, the
> same trust caveat as `cache-redis.md` applies — for untrusted data use
> `capio.serialize` with `json` instead.

## 2. SQLite — zero-config durable store

```python
# sqlite_store.py
import pickle
import sqlite3
import time

_MISSING = object()


class SqliteStore:
    def __init__(self, path="capio.db"):
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capio_store ("
            " ns TEXT, key TEXT, value BLOB, expires REAL,"
            " PRIMARY KEY (ns, key))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capio_seq (ns TEXT PRIMARY KEY, n INTEGER NOT NULL)"
        )
        self._conn.commit()

    def put(self, ns, key, value, ttl=None):
        expires = time.time() + ttl if ttl is not None else None
        self._conn.execute(
            "INSERT INTO capio_store (ns, key, value, expires) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
            (ns, str(key), pickle.dumps(value), expires),
        )
        self._conn.execute(
            "INSERT INTO capio_seq (ns, n) VALUES (?, 1)"
            " ON CONFLICT(ns) DO UPDATE SET n = capio_seq.n + 1",
            (ns,),
        )
        self._conn.commit()

    def get(self, ns, key, default=_MISSING):
        row = self._conn.execute(
            "SELECT value, expires FROM capio_store WHERE ns=? AND key=?", (ns, str(key))
        ).fetchone()
        if row is None:
            return default
        value, expires = row
        if expires is not None and expires <= time.time():
            self.delete(ns, key)
            return default
        return pickle.loads(value)

    def delete(self, ns, key):
        cur = self._conn.execute("DELETE FROM capio_store WHERE ns=? AND key=?", (ns, str(key)))
        self._conn.commit()
        return cur.rowcount > 0

    def items(self, ns):
        now = time.time()
        out = []
        for key, value, expires in self._conn.execute(
            "SELECT key, value, expires FROM capio_store WHERE ns=?", (ns,)
        ):
            if expires is not None and expires <= now:
                self.delete(ns, key)
            else:
                out.append((str(key), pickle.loads(value)))
        return out

    def scan(self, prefix):
        now = time.time()
        out = []
        for ns, key, value, expires in self._conn.execute(
            "SELECT ns, key, value, expires FROM capio_store"
        ):
            if expires is not None and expires <= now:
                self.delete(ns, key)
            elif (ns + ":" + key).startswith(prefix):
                out.append((ns, str(key), pickle.loads(value)))
        return out

    def sequence(self, ns):
        row = self._conn.execute("SELECT n FROM capio_seq WHERE ns=?", (ns,)).fetchone()
        return row[0] if row else 0

    def clear(self, ns=None):
        if ns is None:
            self._conn.execute("DELETE FROM capio_store")
            self._conn.execute("DELETE FROM capio_seq")
        else:
            self._conn.execute("DELETE FROM capio_store WHERE ns=?", (ns,))
            self._conn.execute("DELETE FROM capio_seq WHERE ns=?", (ns,))
        self._conn.commit()
```

Wire it up:

```python
from capio import default_runtime
from sqlite_store import SqliteStore

runtime = default_runtime()
runtime.bind_backend("store.sqlite", SqliteStore("capio.db"))

@use.memory(store="store.sqlite", namespace="chat_history")   # durable memories
@use.llm(model="gpt-4o-mini")
def chat(messages): ...

@use.idempotent(backend="store.sqlite", key="Idempotency-Key")  # replays return stored result
def charge(amount, Idempotency_Key): ...

@use.rag(store="store.sqlite")
@use.llm(model="gpt-4o-mini")
def answer(question): ...

@use.cron(backend="store.sqlite", at="0 9 * * *")            # persisted next-run state
def morning_digest(): ...
```

One `capio.db` file now holds chat memory, idempotency keys, RAG indexes, and
cron state.

## 3. PostgreSQL (`psycopg`)

The protocol is identical; only the SQL dialect and parameter markers change.

```bash
pip install "psycopg[binary]"
```

```python
# postgres_store.py
import pickle
import time

_MISSING = object()


class PostgresStore:
    def __init__(self, dsn):
        import psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capio_store ("
            " ns TEXT, key TEXT, value BYTEA, expires DOUBLE PRECISION,"
            " PRIMARY KEY (ns, key))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capio_seq (ns TEXT PRIMARY KEY, n BIGINT NOT NULL)"
        )

    def put(self, ns, key, value, ttl=None):
        expires = time.time() + ttl if ttl is not None else None
        self._conn.execute(
            "INSERT INTO capio_store (ns, key, value, expires) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (ns, key) DO UPDATE SET value=EXCLUDED.value, expires=EXCLUDED.expires",
            (ns, str(key), pickle.dumps(value), expires),
        )
        self._conn.execute(
            "INSERT INTO capio_seq (ns, n) VALUES (%s, 1)"
            " ON CONFLICT (ns) DO UPDATE SET n = capio_seq.n + 1",
            (ns,),
        )

    def get(self, ns, key, default=_MISSING):
        row = self._conn.execute(
            "SELECT value, expires FROM capio_store WHERE ns=%s AND key=%s", (ns, str(key))
        ).fetchone()
        if row is None:
            return default
        value, expires = row
        if expires is not None and expires <= time.time():
            self.delete(ns, key)
            return default
        return pickle.loads(value)

    def delete(self, ns, key):
        cur = self._conn.execute("DELETE FROM capio_store WHERE ns=%s AND key=%s", (ns, str(key)))
        return cur.rowcount > 0

    def items(self, ns):
        now = time.time()
        out = []
        for key, value, expires in self._conn.execute(
            "SELECT key, value, expires FROM capio_store WHERE ns=%s", (ns,)
        ):
            if expires is not None and expires <= now:
                self.delete(ns, key)
            else:
                out.append((str(key), pickle.loads(value)))
        return out

    def scan(self, prefix):
        now = time.time()
        out = []
        for ns, key, value, expires in self._conn.execute("SELECT ns, key, value, expires FROM capio_store"):
            if expires is not None and expires <= now:
                self.delete(ns, key)
            elif (ns + ":" + key).startswith(prefix):
                out.append((ns, str(key), pickle.loads(value)))
        return out

    def sequence(self, ns):
        row = self._conn.execute("SELECT n FROM capio_seq WHERE ns=%s", (ns,)).fetchone()
        return row[0] if row else 0

    def clear(self, ns=None):
        if ns is None:
            self._conn.execute("DELETE FROM capio_store")
            self._conn.execute("DELETE FROM capio_seq")
        else:
            self._conn.execute("DELETE FROM capio_store WHERE ns=%s", (ns,))
            self._conn.execute("DELETE FROM capio_seq WHERE ns=%s", (ns,))


runtime.bind_backend("store.postgres", PostgresStore("postgresql://user:pass@localhost/capio"))

@use.rag(store="store.postgres")
@use.ingest(store="store.postgres")
class Docs: ...                    # chunked indexes live in Postgres
```

### 3.1 Audit trail in Postgres

The `audit` backend has its own small contract: `append(record)`, `query(*,
actor, action, limit)`, `verify()`, `size`, `clear`. Here the database *is* the
integrity guarantee (single-writer DB, optional row-level triggers); `verify()`
is a no-op compared to the in-memory hash-chained default.

```python
# postgres_audit.py
import json


class PostgresAuditBackend:
    def __init__(self, dsn):
        import psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS capio_audit ("
            " id TEXT PRIMARY KEY, ts DOUBLE PRECISION, actor TEXT, action TEXT,"
            " outcome TEXT, record JSONB)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON capio_audit (action)")

    def append(self, record):
        self._conn.execute(
            "INSERT INTO capio_audit (id, ts, actor, action, outcome, record)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (record.get("id"), record.get("timestamp"), record.get("actor"),
             record.get("action"), record.get("outcome"), json.dumps(record)),
        )
        return dict(record)

    def query(self, *, actor=None, action=None, limit=100):
        rows = self._conn.execute(
            "SELECT record FROM capio_audit"
            " WHERE (%(a)s IS NULL OR actor = %(a)s) AND (%(b)s IS NULL OR action = %(b)s)"
            " ORDER BY ts DESC LIMIT %(l)s",
            {"a": actor, "b": action, "l": limit},
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def verify(self):
        return True                      # durability from the DB, not a hash chain

    @property
    def size(self):
        return self._conn.execute("SELECT count(*) FROM capio_audit").fetchone()[0]

    def clear(self):
        self._conn.execute("DELETE FROM capio_audit")


runtime.bind_backend("audit.postgres", PostgresAuditBackend("postgresql://user:pass@localhost/capio"))

@use.audit(backend="audit.postgres", action="order.cancel", actor=lambda ctx: ctx.auth.get("subject"))
def cancel_order(order_id): ...
```

## 4. MySQL (`PyMySQL`)

MySQL 8+ uses `INSERT ... ON DUPLICATE KEY UPDATE` and `%s` placeholders —
otherwise identical to the Postgres example. The two `CREATE TABLE` statements
differ only in types (`BLOB`, `DOUBLE`, `VARCHAR`):

```python
class MySqlStore:                       # same methods as PostgresStore
    def __init__(self, dsn):            # dsn: "mysql+pymysql://user:pass@host/db"
        import pymysql, urllib.parse
        parts = urllib.parse.urlparse(dsn)
        self._conn = pymysql.connect(
            host=parts.hostname, port=parts.port or 3306,
            user=parts.username, password=parts.password, database=parts.path.lstrip("/"),
            autocommit=True, charset="utf8mb4",
        )
        # ...CREATE TABLE ... (same columns), then implement the 7 methods verbatim
```

Bind as `store.mysql` and pass `store="store.mysql"` to `memory`/`rag`/etc.

## 5. MongoDB (`pymongo`)

The document model maps the protocol almost one-to-one: namespaces become
collections, and `sequence` is a counter document.

```python
# mongodb_store.py
import pickle
import time

_MISSING = object()


class MongoStore:
    def __init__(self, uri="mongodb://localhost:27017", db="capio"):
        from pymongo import MongoClient
        self._db = MongoClient(uri)[db]
        self._seq = self._db["__seq__"]

    def put(self, ns, key, value, ttl=None):
        expires = time.time() + ttl if ttl is not None else None
        self._db[ns].update_one(
            {"_id": str(key)},
            {"$set": {"value": pickle.dumps(value), "expires": expires}},
            upsert=True,
        )
        self._seq.update_one({"_id": ns}, {"$inc": {"n": 1}}, upsert=True)

    def get(self, ns, key, default=_MISSING):
        doc = self._db[ns].find_one({"_id": str(key)})
        if doc is None:
            return default
        if doc.get("expires") is not None and doc["expires"] <= time.time():
            self.delete(ns, key)
            return default
        return pickle.loads(doc["value"])

    def delete(self, ns, key):
        return self._db[ns].delete_one({"_id": str(key)}).deleted_count > 0

    def items(self, ns):
        now = time.time()
        out = []
        for doc in self._db[ns].find():
            if doc.get("expires") is not None and doc["expires"] <= now:
                self.delete(ns, doc["_id"])
            else:
                out.append((str(doc["_id"]), pickle.loads(doc["value"])))
        return out

    def scan(self, prefix):
        now = time.time()
        out = []
        for ns in self._db.list_collection_names():
            for doc in self._db[ns].find():
                if doc.get("expires") is not None and doc["expires"] <= now:
                    self.delete(ns, doc["_id"])
                elif (ns + ":" + str(doc["_id"])).startswith(prefix):
                    out.append((ns, str(doc["_id"]), pickle.loads(doc["value"])))
        return out

    def sequence(self, ns):
        doc = self._seq.find_one({"_id": ns})
        return doc["n"] if doc else 0

    def clear(self, ns=None):
        if ns is None:
            for name in self._db.list_collection_names():
                if name != "__seq__":
                    self._db[name].drop()
            self._seq.delete_many({})
        else:
            self._db[ns].drop()
            self._seq.delete_one({"_id": ns})
```

```python
runtime.bind_backend("store.mongo", MongoStore("mongodb://localhost:27017", "capio"))

@use.memory(store="store.mongo")
@use.llm(model="gpt-4o-mini")
def chat(messages): ...
```

## 6. Which capability maps to which store namespace

| Capability | Default `store` | Namespace(s) it touches | Type of data |
|---|---|---|---|
| `idempotent` | `store.memory` | `idempotent` | request hash → stored result |
| `memory` | `store.memory` | `memory` (configurable) | `{input, output, embedding}` |
| `rag` | `store.memory` | `rag` | chunk vectors + texts |
| `ingest` | `store.memory` | `ingest` | chunked document index |
| `cron` | `store.memory` | `cron` | next-run timestamps |
| `publish` (outbox) | `store.memory` | outbox namespace | transactional outbox messages |

One database backend can therefore replace all six. For read-write **business
data**, use the same store protocol from a custom capability
(`docs/custom_capabilities.md`) or call your driver directly inside the wrapped
function — Capio wraps, it doesn't own your schema.

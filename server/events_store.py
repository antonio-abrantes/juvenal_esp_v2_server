"""Fila SQLite privada, um worker e transações curtas, sem executar eventos."""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from events_schema import EventError

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def utc_text(stamp):
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


class EventsStore:
    def __init__(self, data_dir, config):
        self.path = data_dir / "events" / "events.sqlite3"
        self.config = config
        self.ready = False
        self.closing = False
        self.error = ""
        self.pending_jobs = 0
        self._executor = None
        self._connection = None
        self._pending = set()

    async def _call(self, operation, *args, internal=False):
        if self._executor is None or self.closing and not internal:
            raise EventError("events_store_unavailable", 503, 5)
        # Cancelamento do HTTP não libera capacidade de trabalho SQL ainda em execução.
        if len(self._pending) >= 8 and not internal:
            raise EventError("events_store_busy", 503, 2)
        future = asyncio.get_running_loop().run_in_executor(self._executor, operation, *args)
        self._pending.add(future)

        def finished(item):
            self._pending.discard(item)
            if not item.cancelled():
                item.exception()

        future.add_done_callback(finished)
        return await asyncio.shield(future)

    @contextmanager
    def _transaction(self):
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _sweep(self, now):
        # Expiração de fila não representa efeito iniciado ou tentativa de entrega.
        connection = self._connection
        with self._transaction():
            connection.execute("""UPDATE events SET status='expired',
                error='event_expired', finished_at=expires_at, updated_at=?, payload_json=NULL
                WHERE status='queued' AND expires_at<=?""", (now, now))
            connection.execute("DELETE FROM events WHERE finished_at IS NOT NULL AND finished_at<=?",
                               (now - self.config.retention_days * 86400,))
        self.pending_jobs = connection.execute(
            "SELECT COUNT(*) FROM events WHERE finished_at IS NULL").fetchone()[0]

    def _open(self):
        # Import opcional: instalações sem sqlite3 degradam só a capacidade de eventos.
        import sqlite3
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self._connection = connection
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, SCHEMA_VERSION}:
            raise EventError("events_schema_unsupported", 503)
        # Journal convencional simplifica persistência/backup nativo; não exige WAL.
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        with self._transaction():
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                job_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                payload_json TEXT,
                kind TEXT NOT NULL,
                device_id TEXT NOT NULL,
                expression TEXT NOT NULL,
                humanize INTEGER,
                received_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL,
                action_status TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                effect_started_at REAL,
                finished_at REAL,
                UNIQUE(source, event_id)
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS events_expiry ON events(status, expires_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS events_retention ON events(finished_at)")
            connection.execute("PRAGMA user_version=1")
            # Preparação compatível com fases futuras; nenhum efeito é iniciado na EV-01.
            now = time.time()
            connection.execute("""UPDATE events SET status='unknown',
                delivery_status='unknown', error='events_interrupted_unknown', finished_at=?, updated_at=?,
                payload_json=NULL WHERE finished_at IS NULL
                AND (effect_started_at IS NOT NULL OR status='delivering')""", (now, now))
            connection.execute("""UPDATE events SET status='queued', updated_at=?
                WHERE status IN ('preparing','offered') AND effect_started_at IS NULL
                AND finished_at IS NULL""", (now,))
        self._sweep(time.time())

    def _guard(self, operation, *args):
        try:
            result = operation(*args)
            self.error = ""
            return result
        except EventError:
            raise
        except Exception as error:
            self.error = "events_storage_unavailable"
            logger.warning("Armazenamento de eventos indisponível (%s); sem expor dados/SQL",
                           type(error).__name__)
            raise EventError(self.error, 503, 5) from None

    async def start(self):
        try:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="juvenal-events")
            await self._call(self._guard, self._open, internal=True)
            self.ready = True
        except EventError as error:
            self.error = error.code
            await self.close()
        except Exception as error:
            self.error = "events_storage_unavailable"
            logger.warning("Não foi possível iniciar o worker de eventos (%s)", type(error).__name__)
            await self.close()

    def _insert(self, submission):
        now = time.time()
        self._sweep(now)
        document = submission.document
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT job_id,status,payload_hash,request_hash FROM events WHERE source=? AND event_id=?",
                ("default", document["event_id"])).fetchone()
            if previous is not None:
                if (previous["payload_hash"] != submission.payload_hash
                        and previous["request_hash"] != submission.request_hash):
                    raise EventError("event_id_conflict", 409)
                return {"job_id": previous["job_id"], "status": previous["status"], "duplicate": True}
            count = connection.execute(
                "SELECT COUNT(*) FROM events WHERE finished_at IS NULL").fetchone()[0]
            if count >= self.config.queue_max:
                raise EventError("queue_full", 503, 5)
            job_id = uuid4().hex
            connection.execute("""INSERT INTO events (
                job_id,source,event_id,payload_hash,request_hash,payload_json,kind,device_id,
                expression,humanize,received_at,expires_at,updated_at,status,action_status,delivery_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'queued','not_requested','not_started')""",
                (job_id, "default", document["event_id"], submission.payload_hash, submission.request_hash,
                 submission.payload_json, document["kind"], document["device_id"], document["expression"],
                 int(document["humanize"]) if "humanize" in document else None,
                 now, now + document["expires_in_seconds"], now))
        self.pending_jobs = count + 1
        return {"job_id": job_id, "status": "queued", "duplicate": False}

    async def submit(self, submission):
        return await self._call(self._guard, self._insert, submission)

    def _duplicate(self, event_id, request_hash):
        self._sweep(time.time())
        row = self._connection.execute("""SELECT job_id,status FROM events
            WHERE source='default' AND event_id=? AND request_hash=?""",
            (event_id, request_hash)).fetchone()
        return ({"job_id": row["job_id"], "status": row["status"], "duplicate": True}
                if row is not None else None)

    async def duplicate(self, event_id, request_hash):
        return await self._call(self._guard, self._duplicate, event_id, request_hash)

    def _read(self, job_id):
        self._sweep(time.time())
        row = self._connection.execute(
            """SELECT job_id,event_id,kind,device_id,expression,humanize,received_at,expires_at,
                updated_at,finished_at,status,action_status,delivery_status,error,payload_json
                FROM events WHERE job_id=? AND source='default'""", (job_id,)).fetchone()
        if row is None:
            raise EventError("event_not_found", 404)
        result = {key: row[key] for key in (
            "job_id", "event_id", "kind", "device_id", "expression", "status",
            "action_status", "delivery_status")}
        result["humanize"] = bool(row["humanize"]) if row["humanize"] is not None else None
        result["error"] = row["error"] or None
        for key in ("received_at", "expires_at", "updated_at", "finished_at"):
            result[key] = utc_text(row[key])
        if self.config.log_content and row["payload_json"] is not None:
            import json
            result["content"] = json.loads(row["payload_json"])
        return result

    async def get(self, job_id):
        return await self._call(self._guard, self._read, job_id)

    async def maintain(self):
        await self._call(self._guard, self._sweep, time.time())

    def _close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def close(self):
        self.closing = True
        self.ready = False
        if self._executor is None:
            return
        try:
            await asyncio.wait_for(self._call(self._close, internal=True), 10)
        except (EventError, TimeoutError):
            logger.warning("Encerramento do worker de eventos pendente; sem repetir operações")
        finally:
            # O fechamento está enfileirado depois das operações SQL já submetidas.
            self._executor.shutdown(wait=False)
            self._executor = None

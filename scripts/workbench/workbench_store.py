"""SQLite (WAL) index & paginated query store for workbench tasks, works, publish logs and institution summaries.

Design principles:
- Zero external dependencies (Python stdlib sqlite3).
- Standard SQL-92 schema & B-Tree indexes so future migration to MySQL/PostgreSQL is straightforward.
- Keeps existing JSON files, media directories, project.json and secrets.json intact; auto-migrates on first start.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


SCHEMA_VERSION = 1
ACTIVE_TASK_STATUSES = ("draft", "queued", "running", "stopping", "needs_input", "interrupted", "failed")
ACTIVE_PUBLISH_STATUSES = ("queued", "checking", "uploading", "validating", "submitting", "polling")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    institution_id TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT '',
    person_name TEXT NOT NULL DEFAULT '',
    person_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    production_status TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 1,
    completed_count INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 0,
    progress INTEGER NOT NULL DEFAULT 0,
    workspace_missing INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_inst_updated ON tasks (institution_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_person_updated ON tasks (person_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS products (
    product_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    video_index INTEGER NOT NULL,
    version TEXT NOT NULL DEFAULT 'original',
    source TEXT NOT NULL DEFAULT 'task',
    title TEXT NOT NULL DEFAULT '',
    institution_id TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT '',
    person_name TEXT NOT NULL DEFAULT '',
    accepted INTEGER NOT NULL DEFAULT 0,
    deletion_pending INTEGER NOT NULL DEFAULT 0,
    publish_overall TEXT NOT NULL DEFAULT 'unpublished',
    publish_json TEXT NOT NULL DEFAULT '{}',
    cover_url TEXT NOT NULL DEFAULT '',
    video_url TEXT NOT NULL DEFAULT '',
    package_url TEXT NOT NULL DEFAULT '',
    preview_url TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_products_updated ON products (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_pub_updated ON products (accepted, publish_overall, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_inst_updated ON products (institution_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_person_updated ON products (person_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_task ON products (task_id);

CREATE TABLE IF NOT EXISTS publish_batches (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL DEFAULT 'local',
    visibility TEXT NOT NULL DEFAULT 'private',
    ai_declaration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued',
    total_count INTEGER NOT NULL DEFAULT 0,
    published_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    active_count INTEGER NOT NULL DEFAULT 0,
    platforms_csv TEXT NOT NULL DEFAULT '',
    person_ids_json TEXT NOT NULL DEFAULT '[]',
    search_text TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pub_batches_created ON publish_batches (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pub_batches_status_created ON publish_batches (status, created_at DESC);

CREATE TABLE IF NOT EXISTS publish_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    product_key TEXT NOT NULL,
    task_id TEXT NOT NULL,
    video_index INTEGER NOT NULL,
    version TEXT NOT NULL DEFAULT 'original',
    title TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT '',
    person_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    platform_name TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',
    account_name TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private',
    channel TEXT NOT NULL DEFAULT 'local',
    status TEXT NOT NULL DEFAULT 'queued',
    step TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    task_set_id TEXT NOT NULL DEFAULT '',
    publish_url TEXT NOT NULL DEFAULT '',
    next_poll_at REAL NOT NULL DEFAULT 0,
    published_at REAL,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    item_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pub_items_batch ON publish_items (batch_id);
CREATE INDEX IF NOT EXISTS idx_pub_items_product_plat ON publish_items (product_key, platform, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pub_items_active ON publish_items (status, next_poll_at);

CREATE TABLE IF NOT EXISTS institutions_index (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_inst_updated ON institutions_index (updated_at DESC);
"""


def _dir_stamp(path: Path) -> int:
    try:
        st = path.stat()
        return int(st.st_mtime_ns)
    except OSError:
        return 0


def _clamp_pagination(page: Any, page_size: Any, *, default_size: int = 20, max_size: int = 100) -> tuple[int, int]:
    try:
        p = max(1, int(page))
    except (TypeError, ValueError):
        p = 1
    try:
        ps = max(1, min(max_size, int(page_size)))
    except (TypeError, ValueError):
        ps = default_size
    return p, ps


def _task_person_ids(task: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("videoSpecs", "videoPeople"):
        for item in task.get(key) or []:
            if isinstance(item, dict):
                pid = str(item.get("person") or "").strip()
                if pid and pid not in ids:
                    ids.append(pid)
    primary = str(task.get("person") or "").strip()
    if primary and primary not in ids:
        ids.insert(0, primary)
    return ids


def _trim_task_for_list(pub_task: dict[str, Any]) -> dict[str, Any]:
    """Keep everything taskRows() needs while dropping bulky full event logs."""
    out = dict(pub_task)
    events = out.get("events")
    if isinstance(events, list) and len(events) > 5:
        out["events"] = events[-5:]
    return out


class WorkbenchStore:
    def __init__(self, app, read_fn):
        self.app = app
        self.read = read_fn
        self.db_path = app.data / "workbench.db"
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._stamps: dict[str, int] = {}
        self._initialized = False
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.app.ensure_dir(self.db_path.parent)
            conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._conn()
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            conn.commit()

    def _current_fs_stamps(self) -> dict[str, int]:
        tasks_dir = self.app.data / "tasks"
        products_dir = self.app.data / "products"
        batches_dir = self.app.data / "publish" / "batches"
        library_dir = self.app.data / "library"
        queue_file = self.app.data / "queue.json"
        return {
            "tasks": _dir_stamp(tasks_dir),
            "products": _dir_stamp(products_dir),
            "batches": _dir_stamp(batches_dir),
            "library": _dir_stamp(library_dir),
            "queue": _dir_stamp(queue_file),
        }

    def _capture_stamps(self) -> None:
        self._stamps = self._current_fs_stamps()

    def ensure_synced(self, *, force: bool = False) -> None:
        if not force and self._initialized:
            cur = self._current_fs_stamps()
            if cur == self._stamps:
                return
        self.sync_all()

    # ---- Full sync & migration from existing JSON files ----
    def sync_all(self) -> None:
        with self._write_lock:
            conn = self._conn()
            # 1. Publish batches & items (needed first so product publish status is accurate)
            pub_batches = self.app.publish.batches()
            batch_ids = set()
            for b in pub_batches:
                batch_ids.add(b["id"])
                self._upsert_publish_batch_locked(conn, b, refresh_products=False)
            self._prune_missing_ids(conn, "publish_batches", batch_ids)
            if batch_ids:
                placeholders = ",".join("?" for _ in batch_ids)
                conn.execute(f"DELETE FROM publish_items WHERE batch_id NOT IN ({placeholders})", tuple(batch_ids))
            else:
                conn.execute("DELETE FROM publish_items")

            # Build publish status map directly from SQLite publish_items
            pub_status_map = self._aggregate_product_publish_statuses_locked(conn)

            # 2. Tasks & non-archived task products
            archives = self.app.archive.public()
            archived_ids = {a["id"] for a in archives}
            tasks_raw = self.app.tasks()
            task_ids = set()
            valid_product_keys: set[str] = set()

            for raw_t in tasks_raw:
                pub_t = self.app.public_task(raw_t)
                tid = pub_t["id"]
                task_ids.add(tid)
                self._upsert_task_locked(conn, raw_t, pub_t)
                if tid not in archived_ids:
                    for key in self._upsert_task_products_locked(conn, raw_t, pub_t, pub_status_map):
                        valid_product_keys.add(key)

            self._prune_missing_ids(conn, "tasks", task_ids)

            # 3. Archived products
            for arch in archives:
                for key in self._upsert_archive_products_locked(conn, arch, pub_status_map):
                    valid_product_keys.add(key)

            self._prune_missing_ids(conn, "products", valid_product_keys, col="product_key")

            # 4. Institutions index
            inst_records = self.app.library.records()
            inst_ids = set()
            for rec in inst_records:
                inst_ids.add(rec["id"])
                self._upsert_institution_locked(conn, rec)
            self._prune_missing_ids(conn, "institutions_index", inst_ids)

            conn.commit()
            self._initialized = True
            self._capture_stamps()

    def _prune_missing_ids(self, conn: sqlite3.Connection, table: str, keep_ids: set[str], *, col: str = "id") -> None:
        if not keep_ids:
            conn.execute(f"DELETE FROM {table}")
            return
        existing = {row[0] for row in conn.execute(f"SELECT {col} FROM {table}")}
        stale = existing - keep_ids
        if stale:
            conn.executemany(f"DELETE FROM {table} WHERE {col} = ?", [(x,) for x in stale])

    # ---- Incremental sync hooks (called on writes) ----
    def sync_task(self, task_id: str) -> None:
        with self._write_lock:
            conn = self._conn()
            raw_t = self.read(self.app.task_path(task_id), None)
            if not raw_t:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.execute("DELETE FROM products WHERE task_id = ? AND source = 'task'", (task_id,))
                conn.commit()
                self._capture_stamps()
                return
            pub_t = self.app.public_task(raw_t)
            self._upsert_task_locked(conn, raw_t, pub_t)
            arch_exists = (self.app.archive.root / task_id / "manifest.json").is_file()
            if not arch_exists:
                pub_map = self._aggregate_product_publish_statuses_locked(conn, task_id=task_id)
                keep = self._upsert_task_products_locked(conn, raw_t, pub_t, pub_map)
                rows = [r[0] for r in conn.execute("SELECT product_key FROM products WHERE task_id = ? AND source = 'task'", (task_id,))]
                for old_key in rows:
                    if old_key not in keep:
                        conn.execute("DELETE FROM products WHERE product_key = ?", (old_key,))
            conn.commit()
            self._capture_stamps()

    def sync_archive(self, task_id: str) -> None:
        with self._write_lock:
            conn = self._conn()
            arch_list = [a for a in self.app.archive.public() if a["id"] == task_id]
            pub_map = self._aggregate_product_publish_statuses_locked(conn, task_id=task_id)
            conn.execute("DELETE FROM products WHERE task_id = ?", (task_id,))
            if arch_list:
                self._upsert_archive_products_locked(conn, arch_list[0], pub_map)
            else:
                raw_t = self.read(self.app.task_path(task_id), None)
                if raw_t:
                    pub_t = self.app.public_task(raw_t)
                    self._upsert_task_products_locked(conn, raw_t, pub_t, pub_map)
            conn.commit()
            self._capture_stamps()

    def sync_publish_batch(self, batch: dict[str, Any]) -> None:
        with self._write_lock:
            conn = self._conn()
            self._upsert_publish_batch_locked(conn, batch, refresh_products=True)
            conn.commit()
            self._capture_stamps()

    def delete_publish_batch(self, batch_id: str) -> None:
        with self._write_lock:
            conn = self._conn()
            affected_keys = [r[0] for r in conn.execute("SELECT DISTINCT product_key FROM publish_items WHERE batch_id = ?", (batch_id,))]
            conn.execute("DELETE FROM publish_items WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM publish_batches WHERE id = ?", (batch_id,))
            if affected_keys:
                self._refresh_products_publish_state_locked(conn, affected_keys)
            conn.commit()
            self._capture_stamps()

    def sync_institution(self, inst_id: str) -> None:
        with self._write_lock:
            conn = self._conn()
            rec = self.read(self.app.library.path(inst_id), None)
            if not rec:
                conn.execute("DELETE FROM institutions_index WHERE id = ?", (inst_id,))
            else:
                self._upsert_institution_locked(conn, rec)
            conn.commit()
            self._capture_stamps()

    # ---- Low-level upsert helpers ----
    def _upsert_task_locked(self, conn: sqlite3.Connection, raw_t: dict[str, Any], pub_t: dict[str, Any]) -> None:
        pids = _task_person_ids(raw_t)
        trimmed = _trim_task_for_list(pub_t)
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks (
                id, title, institution_id, institution_name, person_id, person_name, person_ids_json,
                status, production_status, review_status, count, completed_count, phase, progress,
                workspace_missing, created_at, updated_at, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pub_t["id"],
                str(pub_t.get("title") or ""),
                str(pub_t.get("institutionId") or ""),
                str(pub_t.get("institutionName") or ""),
                pids[0] if pids else "",
                str(pub_t.get("personName") or ""),
                json.dumps(pids, ensure_ascii=False),
                str(pub_t.get("status") or "draft"),
                str(pub_t.get("productionStatus") or raw_t.get("status") or ""),
                str(pub_t.get("reviewStatus") or raw_t.get("reviewStatus") or ""),
                int(pub_t.get("count") or 1),
                int(pub_t.get("completedCount") or 0),
                int(pub_t.get("phase") or 0),
                int(pub_t.get("progress") or 0),
                1 if pub_t.get("workspaceMissing") else 0,
                float(pub_t.get("created") or 0),
                float(pub_t.get("updated") or 0),
                json.dumps(trimmed, ensure_ascii=False),
            ),
        )

    def _upsert_task_products_locked(
        self,
        conn: sqlite3.Connection,
        raw_t: dict[str, Any],
        pub_t: dict[str, Any],
        pub_map: dict[str, dict[str, Any]],
    ) -> list[str]:
        keys: list[str] = []
        tid = pub_t["id"]
        accepted = 1 if pub_t.get("status") == "accepted" else 0
        specs = raw_t.get("videoSpecs") or raw_t.get("videoPeople") or []
        for r in pub_t.get("results") or []:
            if not (r.get("video") or r.get("deletionPending")):
                continue
            idx = int(r.get("index", 0))
            ver = str(r.get("currentVersion") or r.get("version") or "original")
            pk = f"{tid}:{idx}:{ver}"
            keys.append(pk)
            spec = specs[idx] if 0 <= idx < len(specs) and isinstance(specs[idx], dict) else {}
            pid = str(spec.get("person") or raw_t.get("person") or "")
            pname = str(r.get("personName") or spec.get("personName") or pub_t.get("personName") or "")
            pstat = pub_map.get(pk) or self._fallback_pub_status(pub_map, tid, idx)
            overall = (pstat or {}).get("overall") or "unpublished"
            conn.execute(
                """
                INSERT OR REPLACE INTO products (
                    product_key, task_id, video_index, version, source, title,
                    institution_id, institution_name, person_id, person_name,
                    accepted, deletion_pending, publish_overall, publish_json,
                    cover_url, video_url, package_url, preview_url, label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'task', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pk,
                    tid,
                    idx,
                    ver,
                    str(r.get("title") or pub_t.get("title") or ""),
                    str(pub_t.get("institutionId") or ""),
                    str(pub_t.get("institutionName") or "未关联机构"),
                    pid,
                    pname,
                    accepted,
                    1 if r.get("deletionPending") else 0,
                    overall,
                    json.dumps(pstat or {}, ensure_ascii=False),
                    str(r.get("cover") or ""),
                    str(r.get("video") or ""),
                    str(r.get("package") or ""),
                    f"#preview/{tid}/{idx}",
                    "已验收" if accepted else "待验收",
                    float(pub_t.get("created") or 0),
                    float(pub_t.get("updated") or 0),
                ),
            )
        return keys

    def _upsert_archive_products_locked(
        self,
        conn: sqlite3.Connection,
        arch: dict[str, Any],
        pub_map: dict[str, dict[str, Any]],
    ) -> list[str]:
        keys: list[str] = []
        tid = arch["id"]
        cfg_people = self.app.pc.load().get("people") or {}
        name_to_id = {str(p.get("name") or ""): pid for pid, p in cfg_people.items()}
        for r in arch.get("results") or []:
            idx = int(r.get("index", 0))
            ver = str(r.get("version") or "original")
            pk = f"{tid}:{idx}:{ver}"
            keys.append(pk)
            pname = str(r.get("personName") or arch.get("personName") or "")
            pid = str(r.get("personId") or name_to_id.get(pname) or "")
            pstat = pub_map.get(pk) or self._fallback_pub_status(pub_map, tid, idx)
            overall = (pstat or {}).get("overall") or "unpublished"
            conn.execute(
                """
                INSERT OR REPLACE INTO products (
                    product_key, task_id, video_index, version, source, title,
                    institution_id, institution_name, person_id, person_name,
                    accepted, deletion_pending, publish_overall, publish_json,
                    cover_url, video_url, package_url, preview_url, label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'archive', ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pk,
                    tid,
                    idx,
                    ver,
                    str(r.get("title") or arch.get("title") or ""),
                    str(arch.get("institutionId") or ""),
                    str(arch.get("institutionName") or "未关联机构"),
                    pid,
                    pname,
                    overall,
                    json.dumps(pstat or {}, ensure_ascii=False),
                    str(r.get("cover") or ""),
                    str(r.get("video") or ""),
                    str(r.get("package") or ""),
                    f"#preview/archive/{tid}/{idx}/{ver}",
                    str(r.get("label") or "已归档"),
                    float(arch.get("created") or 0),
                    float(arch.get("updated") or 0),
                ),
            )
        return keys

    def _fallback_pub_status(self, pub_map: dict[str, dict[str, Any]], task_id: str, index: int) -> dict[str, Any] | None:
        prefix = f"{task_id}:{index}:"
        for k, v in pub_map.items():
            if k.startswith(prefix):
                return v
        return None

    def _upsert_publish_batch_locked(self, conn: sqlite3.Connection, batch: dict[str, Any], *, refresh_products: bool) -> None:
        pub = self.app.publish.public_batch(batch)
        summary = pub["summary"]
        items = pub.get("items") or []
        plats = sorted({str(i.get("platform") or "") for i in items if i.get("platform")})
        pids = sorted({str(i.get("personId") or "") for i in items if i.get("personId")})
        search_parts = []
        for i in items:
            search_parts.extend([
                str(i.get("title") or ""),
                str(i.get("institutionName") or ""),
                str(i.get("personName") or ""),
                str(i.get("accountName") or ""),
                str(i.get("platformName") or ""),
            ])
        conn.execute(
            """
            INSERT OR REPLACE INTO publish_batches (
                id, channel, visibility, ai_declaration, status,
                total_count, published_count, failed_count, active_count,
                platforms_csv, person_ids_json, search_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pub["id"],
                str(pub.get("channel") or "local"),
                str(pub.get("visibility") or "private"),
                1 if pub.get("aiDeclaration", True) else 0,
                str(summary.get("status") or "queued"),
                int(summary.get("total") or 0),
                int(summary.get("published") or 0),
                int(summary.get("failed") or 0),
                int(summary.get("active") or 0),
                "," + ",".join(plats) + "," if plats else "",
                json.dumps(pids, ensure_ascii=False),
                " ".join(search_parts).lower(),
                float(pub.get("created") or 0),
                float(pub.get("updated") or 0),
            ),
        )
        conn.execute("DELETE FROM publish_items WHERE batch_id = ?", (pub["id"],))
        affected_keys: set[str] = set()
        for item in items:
            pk = str(item.get("productKey") or "")
            if pk:
                affected_keys.add(pk)
            conn.execute(
                """
                INSERT OR REPLACE INTO publish_items (
                    id, batch_id, product_key, task_id, video_index, version,
                    title, institution_name, person_id, person_name,
                    platform, platform_name, account_id, account_name,
                    visibility, channel, status, step, message, task_set_id,
                    publish_url, next_poll_at, published_at, created_at, updated_at, item_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    pub["id"],
                    pk,
                    str(item.get("taskId") or ""),
                    int(item.get("index", 0)),
                    str(item.get("version") or "original"),
                    str(item.get("title") or ""),
                    str(item.get("institutionName") or ""),
                    str(item.get("personId") or ""),
                    str(item.get("personName") or ""),
                    str(item.get("platform") or ""),
                    str(item.get("platformName") or ""),
                    str(item.get("accountId") or ""),
                    str(item.get("accountName") or ""),
                    str(item.get("visibility") or "private"),
                    str(item.get("channel") or "local"),
                    str(item.get("status") or "queued"),
                    str(item.get("step") or ""),
                    str(item.get("message") or ""),
                    str(item.get("taskSetId") or ""),
                    str(item.get("publishUrl") or ""),
                    float(item.get("nextPollAt") or 0),
                    item.get("publishedAt"),
                    float(item.get("created") or 0),
                    float(item.get("updated") or 0),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
        if refresh_products and affected_keys:
            self._refresh_products_publish_state_locked(conn, list(affected_keys))

    def _aggregate_product_publish_statuses_locked(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str = "",
        product_keys: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if product_keys:
            placeholders = ",".join("?" for _ in product_keys)
            clauses.append(f"product_key IN ({placeholders})")
            params.extend(product_keys)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT product_key, platform, platform_name, status, step, visibility,
                   account_name, published_at, updated_at, batch_id, id, publish_url, message
            FROM publish_items
            {where}
            ORDER BY created_at DESC, updated_at DESC
            """,
            params,
        ).fetchall()

        by_product: dict[str, dict[str, dict[str, Any]]] = {}
        for r in rows:
            pk = r["product_key"]
            plat = r["platform"]
            bucket = by_product.setdefault(pk, {})
            prev = bucket.get(plat)
            entry = {
                "platform": plat,
                "platformName": r["platform_name"],
                "status": r["status"],
                "step": r["step"],
                "visibility": r["visibility"],
                "accountName": r["account_name"],
                "publishedAt": r["published_at"],
                "updated": r["updated_at"],
                "batchId": r["batch_id"],
                "itemId": r["id"],
                "publishUrl": r["publish_url"],
                "message": r["message"],
            }
            if prev is None:
                bucket[plat] = entry
            elif prev["status"] != "published" and entry["status"] == "published" and prev["status"] not in ACTIVE_PUBLISH_STATUSES:
                bucket[plat] = entry

        out: dict[str, dict[str, Any]] = {}
        for pk, plat_map in by_product.items():
            vals = list(plat_map.values())
            published_n = sum(1 for v in vals if v["status"] == "published")
            active_n = sum(1 for v in vals if v["status"] in ACTIVE_PUBLISH_STATUSES)
            failed_n = sum(1 for v in vals if v["status"] == "failed")
            if active_n:
                overall = "publishing"
            elif published_n and published_n == len(vals):
                overall = "published"
            elif published_n:
                overall = "partial"
            elif failed_n:
                overall = "failed"
            else:
                overall = "unpublished"
            out[pk] = {
                "overall": overall,
                "publishedCount": published_n,
                "activeCount": active_n,
                "failedCount": failed_n,
                "totalPlatforms": len(vals),
                "platforms": plat_map,
            }
        return out

    def _refresh_products_publish_state_locked(self, conn: sqlite3.Connection, product_keys: list[str]) -> None:
        agg = self._aggregate_product_publish_statuses_locked(conn, product_keys=product_keys)
        for pk in product_keys:
            pstat = agg.get(pk)
            overall = (pstat or {}).get("overall") or "unpublished"
            conn.execute(
                "UPDATE products SET publish_overall = ?, publish_json = ? WHERE product_key = ?",
                (overall, json.dumps(pstat or {}, ensure_ascii=False), pk),
            )

    def _upsert_institution_locked(self, conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
        summary = self.app.library.public(rec, False)
        conn.execute(
            """
            INSERT OR REPLACE INTO institutions_index (id, name, address, revision, updated_at, summary_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                rec["id"],
                str(rec.get("name") or ""),
                str(rec.get("address") or ""),
                int(rec.get("revision") or 0),
                float(rec.get("updated") or 0),
                json.dumps(summary, ensure_ascii=False),
            ),
        )

    # ---- Fast SQLite Paginated Queries ----
    def query_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str = "all",
        institution_id: str = "",
        person_id: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        self.ensure_synced()
        page, page_size = _clamp_pagination(page, page_size, default_size=20, max_size=100)
        clauses: list[str] = []
        params: list[Any] = []

        if status and status != "all":
            if status == "active":
                clauses.append("status IN ('running','queued','stopping')")
            elif status == "pending":
                clauses.append("status IN ('draft','interrupted','failed')")
            elif status == "completed":
                clauses.append("status IN ('completed','partial','awaiting_acceptance','accepted')")
            elif status == "cancelled":
                clauses.append("status IN ('cancelled','timed_out')")
            else:
                clauses.append("status = ?")
                params.append(status)
        if institution_id:
            clauses.append("institution_id = ?")
            params.append(institution_id)
        if person_id:
            clauses.append("(person_id = ? OR person_ids_json LIKE ?)")
            params.extend([person_id, f'%"{person_id}"%'])
        q = (q or "").strip()
        if q:
            like = f"%{q.lower()}%"
            clauses.append("(LOWER(title) LIKE ? OR LOWER(institution_name) LIKE ? OR LOWER(person_name) LIKE ?)")
            params.extend([like, like, like])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        total = int(conn.execute(f"SELECT COUNT(*) FROM tasks {where}", params).fetchone()[0])
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT summary_json FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        items = [json.loads(r["summary_json"]) for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
        }

    def query_works(
        self,
        *,
        page: int = 1,
        page_size: int = 24,
        publish_status: str = "all",
        accepted: str = "",
        institution_id: str = "",
        person_id: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        self.ensure_synced()
        page, page_size = _clamp_pagination(page, page_size, default_size=24, max_size=100)
        clauses: list[str] = []
        params: list[Any] = []

        if publish_status == "unpublished":
            clauses.append("accepted = 1 AND publish_overall IN ('unpublished','failed')")
        elif publish_status == "publishing":
            clauses.append("publish_overall = 'publishing'")
        elif publish_status == "published":
            clauses.append("publish_overall IN ('published','partial')")
        elif publish_status == "failed":
            clauses.append("publish_overall = 'failed'")

        if accepted in ("1", "true"):
            clauses.append("accepted = 1")
        elif accepted in ("0", "false"):
            clauses.append("accepted = 0")

        if institution_id:
            clauses.append("institution_id = ?")
            params.append(institution_id)
        if person_id:
            clauses.append("person_id = ?")
            params.append(person_id)
        q = (q or "").strip()
        if q:
            like = f"%{q.lower()}%"
            clauses.append("(LOWER(title) LIKE ? OR LOWER(institution_name) LIKE ? OR LOWER(person_name) LIKE ?)")
            params.extend([like, like, like])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        total = int(conn.execute(f"SELECT COUNT(*) FROM products {where}", params).fetchone()[0])
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT product_key, task_id, video_index, version, source, title,
                   institution_id, institution_name, person_id, person_name,
                   accepted, deletion_pending, publish_overall, publish_json,
                   cover_url, video_url, package_url, preview_url, label,
                   created_at, updated_at
            FROM products
            {where}
            ORDER BY updated_at DESC, task_id DESC, video_index ASC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

        items = []
        for r in rows:
            pub_obj = json.loads(r["publish_json"]) if r["publish_json"] else {}
            items.append({
                "key": r["product_key"],
                "taskId": r["task_id"],
                "index": r["video_index"],
                "version": r["version"],
                "source": r["source"],
                "title": r["title"],
                "institutionId": r["institution_id"],
                "institutionName": r["institution_name"],
                "personId": r["person_id"],
                "personName": r["person_name"],
                "accepted": bool(r["accepted"]),
                "deletionPending": bool(r["deletion_pending"]),
                "publishOverall": r["publish_overall"],
                "pubInfo": pub_obj if pub_obj else None,
                "cover": r["cover_url"],
                "video": r["video_url"],
                "package": r["package_url"],
                "previewUrl": r["preview_url"],
                "label": r["label"],
                "created": r["created_at"],
                "updated": r["updated_at"],
            })

        summary_row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS accepted_cnt,
                   SUM(CASE WHEN accepted = 1 AND publish_overall IN ('unpublished','failed') THEN 1 ELSE 0 END) AS unpub_cnt,
                   SUM(CASE WHEN publish_overall = 'publishing' THEN 1 ELSE 0 END) AS pubing_cnt,
                   SUM(CASE WHEN publish_overall IN ('published','partial') THEN 1 ELSE 0 END) AS pubed_cnt
            FROM products
            """
        ).fetchone()
        return {
            "items": items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "summary": {
                "total": int(summary_row["total"] or 0),
                "accepted": int(summary_row["accepted_cnt"] or 0),
                "unpublished": int(summary_row["unpub_cnt"] or 0),
                "publishing": int(summary_row["pubing_cnt"] or 0),
                "published": int(summary_row["pubed_cnt"] or 0),
            },
        }

    def query_publish_batches(
        self,
        *,
        page: int = 1,
        page_size: int = 15,
        status: str = "all",
        platform: str = "",
        person_id: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        self.ensure_synced()
        page, page_size = _clamp_pagination(page, page_size, default_size=15, max_size=100)
        clauses: list[str] = []
        params: list[Any] = []

        if status == "active":
            clauses.append("active_count > 0")
        elif status == "published":
            clauses.append("status IN ('published','partial')")
        elif status == "failed":
            clauses.append("failed_count > 0")
        elif status and status != "all":
            clauses.append("status = ?")
            params.append(status)

        if platform:
            clauses.append("platforms_csv LIKE ?")
            params.append(f"%,{platform},%")
        if person_id:
            clauses.append("person_ids_json LIKE ?")
            params.append(f'%"{person_id}"%')
        q = (q or "").strip()
        if q:
            clauses.append("search_text LIKE ?")
            params.append(f"%{q.lower()}%")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        total = int(conn.execute(f"SELECT COUNT(*) FROM publish_batches {where}", params).fetchone()[0])
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        batch_rows = conn.execute(
            f"""
            SELECT id, channel, visibility, ai_declaration, status,
                   total_count, published_count, failed_count, active_count,
                   created_at, updated_at
            FROM publish_batches
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

        batch_ids = [r["id"] for r in batch_rows]
        items_by_batch: dict[str, list[dict[str, Any]]] = {bid: [] for bid in batch_ids}
        if batch_ids:
            placeholders = ",".join("?" for _ in batch_ids)
            item_rows = conn.execute(
                f"SELECT batch_id, item_json FROM publish_items WHERE batch_id IN ({placeholders}) ORDER BY created_at ASC, rowid ASC",
                batch_ids,
            ).fetchall()
            for ir in item_rows:
                items_by_batch[ir["batch_id"]].append(json.loads(ir["item_json"]))

        batches_out = []
        for r in batch_rows:
            batches_out.append({
                "id": r["id"],
                "created": r["created_at"],
                "updated": r["updated_at"],
                "channel": r["channel"],
                "visibility": r["visibility"],
                "aiDeclaration": bool(r["ai_declaration"]),
                "summary": {
                    "total": int(r["total_count"]),
                    "published": int(r["published_count"]),
                    "failed": int(r["failed_count"]),
                    "active": int(r["active_count"]),
                    "status": r["status"],
                },
                "items": items_by_batch.get(r["id"], []),
            })

        agg = conn.execute(
            """
            SELECT COALESCE(SUM(active_count), 0) AS active_items,
                   COALESCE(SUM(published_count), 0) AS published_items,
                   COALESCE(SUM(failed_count), 0) AS failed_items,
                   COUNT(*) AS total_batches
            FROM publish_batches
            """
        ).fetchone()
        return {
            "items": batches_out,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "summary": {
                "activeItems": int(agg["active_items"] or 0),
                "publishedItems": int(agg["published_items"] or 0),
                "failedItems": int(agg["failed_items"] or 0),
                "totalBatches": int(agg["total_batches"] or 0),
            },
        }

    def list_institutions_fast(self) -> list[dict[str, Any]]:
        self.ensure_synced()
        rows = self._conn().execute(
            "SELECT summary_json FROM institutions_index ORDER BY updated_at DESC"
        ).fetchall()
        return [json.loads(r["summary_json"]) for r in rows]

    def nav_counts(self) -> dict[str, int]:
        self.ensure_synced()
        conn = self._conn()
        active_tasks = int(
            conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed','partial','accepted','cancelled','timed_out')"
            ).fetchone()[0]
        )
        total_tasks = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        total_works = int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
        active_publish = int(
            conn.execute("SELECT COALESCE(SUM(active_count), 0) FROM publish_batches").fetchone()[0]
        )
        return {
            "activeTasks": active_tasks,
            "totalTasks": total_tasks,
            "totalWorks": total_works,
            "activePublish": active_publish,
        }

    def active_or_recent_tasks(self, limit: int = 30) -> list[dict[str, Any]]:
        """Bounded task snapshot for /api/state (live executing tasks + most recent historical tasks)."""
        self.ensure_synced()
        rows = self._conn().execute(
            """
            SELECT summary_json FROM tasks
            WHERE status IN ('queued','running','stopping','needs_input')
            UNION
            SELECT summary_json FROM (
                SELECT summary_json, created_at FROM tasks ORDER BY created_at DESC LIMIT ?
            )
            """,
            (limit,),
        ).fetchall()
        items = [json.loads(r["summary_json"]) for r in rows]
        items.sort(key=lambda x: x.get("created", 0), reverse=True)
        return items

    def publish_overview_for_state(self, limit: int = 15) -> dict[str, Any]:
        """Lightweight publish state for /api/state backed by SQLite."""
        self.ensure_synced()
        page_data = self.query_publish_batches(page=1, page_size=limit)
        conn = self._conn()
        prod_rows = conn.execute(
            "SELECT product_key, publish_json FROM products WHERE publish_overall != 'unpublished'"
        ).fetchall()
        statuses = {}
        for r in prod_rows:
            try:
                val = json.loads(r["publish_json"])
                if val:
                    statuses[r["product_key"]] = val
            except json.JSONDecodeError:
                pass
        return {
            "settings": self.app.publish.public_settings(),
            "accounts": self.app.publish.cached_accounts(),
            "batches": page_data["items"],
            "summary": page_data["summary"],
            "productStatuses": statuses,
            "activeCount": page_data["summary"]["activeItems"],
        }

    def pending_publish_work(self, now: float) -> list[tuple[str, str, str]]:
        """Fast SQL lookup for publish_service.tick() instead of scanning batches directory."""
        self.ensure_synced()
        rows = self._conn().execute(
            """
            SELECT batch_id, id, status FROM publish_items
            WHERE status = 'queued' OR (status = 'polling' AND next_poll_at <= ?)
            ORDER BY created_at ASC
            """,
            (now,),
        ).fetchall()
        return [(r["batch_id"], r["id"], "execute" if r["status"] == "queued" else "poll") for r in rows]

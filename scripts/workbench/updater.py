"""Workbench self-update engine: GitHub official Release check, active-task guard,
scheduled idle upgrade, atomic verified swap with rollback, and systemd/local restart.

Zero third-party dependencies (Python stdlib only).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from typing import Any


DEFAULT_REPO = "laoliAI666/nursing-home-montage"
DEFAULT_API_BASE = "https://api.github.com"
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SUPPORTED_INTERVALS = (30, 60, 360, 1440)
SUPPORTED_CONFIG_SCHEMA = 1
MAX_BACKUPS = 3
MAX_HISTORY = 15
REQUIRED_RELEASE_ENTRIES = (
    "SKILL.md",
    "VERSION",
    "release.json",
    "preset.json",
    "scripts",
    "references",
    "modules",
    "assets",
)
FORBIDDEN_TOP_ENTRIES = (".git", ".montage")
DEFAULT_SETTINGS = {
    "autoCheck": True,
    "checkIntervalMinutes": 60,
    "ignoredVersion": "",
}


def parse_semver(raw: Any) -> tuple[int, int, int] | None:
    if not raw:
        return None
    m = SEMVER_RE.fullmatch(str(raw).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer_version(remote: str, local: str) -> bool:
    rv, lv = parse_semver(remote), parse_semver(local)
    if rv is None or lv is None:
        return False
    return rv > lv


def _parse_min_python(raw: Any) -> tuple[int, int] | None:
    if not raw:
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", str(raw).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_sha256sums(text: str, filename: str) -> str | None:
    matches: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not m:
            continue
        digest, name = m.group(1).lower(), m.group(2).strip()
        if name == filename or Path(name).name == filename:
            matches.append(digest)
    if len(matches) != 1:
        return None
    return matches[0]


def _format_size(num_bytes: int | None) -> str:
    if not num_bytes or num_bytes <= 0:
        return ""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = num_bytes / 1024
    return f"{kb:.0f} KB"


def _detect_restart_mode(deploy_mode: str) -> str:
    forced = str(os.environ.get("MONTAGE_UPDATE_RESTART_MODE") or "").strip().lower()
    if forced in ("systemd", "relaunch"):
        return forced
    if os.environ.get("INVOCATION_ID") or os.environ.get("NOTIFY_SOCKET"):
        return "systemd"
    return "systemd" if deploy_mode == "server" and sys.platform.startswith("linux") and os.getppid() == 1 else "relaunch"


def _apply_mirror(url: str) -> str:
    mirror = str(os.environ.get("MONTAGE_GITHUB_MIRROR") or "").strip()
    if not mirror:
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname not in ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"):
        return url
    return mirror.rstrip("/") + "/" + url


def _build_opener() -> urllib.request.OpenerDirector:
    proxy = str(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


class WorkbenchUpdater:
    """Manages release discovery, blocker checks, scheduled idle upgrades and atomic swaps."""

    def __init__(self, app, read_fn, write_fn):
        self.app = app
        self.read = read_fn
        self.write = write_fn
        self.lock = threading.RLock()
        self.settings_path = app.data / "update_settings.json"
        self.state_path = app.data / "update_state.json"
        self.updates_root = app.project / ".montage" / "updates"
        self.staging_root = self.updates_root / "staging"
        self.backups_root = self.updates_root / "backups"
        self.manifest_path = self.updates_root / "installed-manifest.json"
        self.server_ref = None  # attached by serve()

        # In-memory transient runtime state
        self._checking = False
        self._upgrading = False
        self._maintenance_lock = False
        self._phase = "idle"  # idle | downloading | backing_up | replacing | restarting | failed
        self._step = 0        # 0..4
        self._progress = 0    # 0..100
        self._message = ""
        self._error = ""
        self._error_code = ""
        self._dirty_files: list[str] = []
        self._target_version = ""
        self._scheduled = False
        self._scheduled_version = ""
        self._scheduled_by = ""
        self._scheduled_by_id = ""
        self._scheduled_at: float | None = None
        self._last_auto_tick_check = 0.0
        self._boot_time = time.time()

    def attach_server(self, http_server):
        self.server_ref = http_server

    # ---- Config & metadata helpers ----
    def repo_slug(self) -> str:
        return str(os.environ.get("MONTAGE_UPDATE_REPO") or DEFAULT_REPO).strip() or DEFAULT_REPO

    def api_base(self) -> str:
        return str(os.environ.get("MONTAGE_GITHUB_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/") or DEFAULT_API_BASE

    def current_version(self) -> str:
        try:
            ver = (self.app.skill / "VERSION").read_text(encoding="utf-8").strip()
            if ver:
                return ver
        except OSError:
            pass
        try:
            rel = json.loads((self.app.skill / "release.json").read_text(encoding="utf-8"))
            return str(rel.get("version") or "0.0.0").strip()
        except Exception:
            return "0.0.0"

    def restart_mode(self) -> str:
        return _detect_restart_mode(getattr(self.app, "deploy_mode", "local"))

    def is_maintenance_locked(self) -> bool:
        with self.lock:
            return bool(self._maintenance_lock)

    def is_draining(self) -> bool:
        """True when upgrade is in progress or scheduled-on-idle (pause dispatching new queued jobs)."""
        with self.lock:
            return bool(self._upgrading or self._maintenance_lock or self._scheduled)

    def is_upgrading(self) -> bool:
        with self.lock:
            return bool(self._upgrading or self._maintenance_lock)

    # ---- Persisted Settings & State ----
    def load_settings(self) -> dict[str, Any]:
        raw = self.read(self.settings_path, {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        interval = raw.get("checkIntervalMinutes", DEFAULT_SETTINGS["checkIntervalMinutes"])
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = 60
        if interval not in SUPPORTED_INTERVALS:
            interval = 60
        return {
            "autoCheck": bool(raw.get("autoCheck", True)),
            "checkIntervalMinutes": interval,
            "ignoredVersion": str(raw.get("ignoredVersion") or "").strip(),
        }

    def save_settings(self, patch: dict[str, Any], *, actor=None, ip: str = "") -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("更新配置格式不正确。")
        with self.lock:
            cur = self.load_settings()
            if "autoCheck" in patch:
                cur["autoCheck"] = bool(patch["autoCheck"])
            if "checkIntervalMinutes" in patch:
                try:
                    val = int(patch["checkIntervalMinutes"])
                except (TypeError, ValueError):
                    raise ValueError("自动检查频率无效。")
                if val not in SUPPORTED_INTERVALS:
                    raise ValueError("自动检查频率仅支持 30 分钟、1 小时、6 小时或每天一次。")
                cur["checkIntervalMinutes"] = val
            if "ignoredVersion" in patch:
                ign = str(patch.get("ignoredVersion") or "").strip().lstrip("v")
                if ign and parse_semver(ign) is None:
                    raise ValueError("忽略版本号格式不正确。")
                cur["ignoredVersion"] = ign
            self.write(self.settings_path, cur)
            if actor is not None and hasattr(self.app, "auth"):
                self.app.auth.log_audit(
                    actor,
                    action="system.update_settings",
                    resource_type="system",
                    summary=f"调整版本更新配置（自动检查：{'开启' if cur['autoCheck'] else '关闭'}，周期 {cur['checkIntervalMinutes']} 分钟）",
                    detail=cur,
                    ip=ip,
                )
            return cur

    def load_state(self) -> dict[str, Any]:
        raw = self.read(self.state_path, {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "lastCheckedAt": raw.get("lastCheckedAt"),
            "etag": str(raw.get("etag") or ""),
            "latest": raw.get("latest") if isinstance(raw.get("latest"), dict) else None,
            "lastError": str(raw.get("lastError") or ""),
            "lastErrorCode": str(raw.get("lastErrorCode") or ""),
            "history": raw.get("history") if isinstance(raw.get("history"), list) else [],
        }

    def _save_state(self, data: dict[str, Any]):
        self.write(self.state_path, data)

    def _record_history(self, *, from_ver: str, to_ver: str, status: str, actor_name: str, backup_name: str = "", error: str = ""):
        st = self.load_state()
        hist = list(st.get("history") or [])
        hist.insert(0, {
            "id": uuid.uuid4().hex[:10],
            "fromVersion": from_ver,
            "toVersion": to_ver,
            "status": status,  # success | failed | rolled_back
            "at": time.time(),
            "actorName": actor_name or "系统自动",
            "backupName": backup_name,
            "error": error[:300] if error else "",
        })
        st["history"] = hist[:MAX_HISTORY]
        self._save_state(st)

    # ---- Active Task Blocker Detection ----
    def list_active_blockers(self, *, exclude_current_request: bool = False) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        app = self.app
        with app.transaction():
            # 1. Video production queue & CLI tasks
            queue_items = (app.queue.data() or {}).get("items") or []
            tasks_list = app.tasks()
            tasks_by_id = {t.get("id"): t for t in tasks_list if isinstance(t, dict)}
            active_queue_by_task: dict[str, list[dict[str, Any]]] = {}
            for row in queue_items:
                if not row.get("settled") or row.get("status") in ("queued", "assigned", "running", "stopping"):
                    tid = str(row.get("taskId") or row.get("id") or "")
                    active_queue_by_task.setdefault(tid, []).append(row)

            seen_task_ids: set[str] = set()
            status_cn = {
                "queued": "排队等待中",
                "assigned": "已分配执行",
                "running": "视频制作中",
                "stopping": "正在停止",
                "completed": "等待结束确认",
            }
            for tid, rows in active_queue_by_task.items():
                seen_task_ids.add(tid)
                t = tasks_by_id.get(tid) or {}
                title = str(t.get("title") or f"制作任务 {tid[:8]}")
                first_status = str(rows[0].get("status") or "running")
                total_cnt = int(t.get("count") or len(rows))
                detail = f"{len(rows)}/{total_cnt} 条 · {status_cn.get(first_status, '运行中')}"
                blockers.append({
                    "kind": "制作任务",
                    "title": title,
                    "detail": detail,
                    "route": f"#task/{tid}" if tid in tasks_by_id else "#tasks",
                })

            for t in tasks_list:
                tid = str(t.get("id") or "")
                if tid in seen_task_ids:
                    continue
                if t.get("executor") == "cli" and t.get("status") in ("queued", "running", "stopping"):
                    blockers.append({
                        "kind": "制作任务",
                        "title": str(t.get("title") or f"制作任务 {tid[:8]}"),
                        "detail": "正在执行视频生成流程",
                        "route": f"#task/{tid}",
                    })
                    seen_task_ids.add(tid)

            if any(p.poll() is None for p in getattr(app, "processes", {}).values()) and not seen_task_ids:
                blockers.append({
                    "kind": "制作任务",
                    "title": "后台渲染子进程",
                    "detail": "仍有制作进程正在运行",
                    "route": "#tasks",
                })

            # 2. Institution AI material preparation
            if app.material_prep.active_any():
                inst_map = {}
                try:
                    inst_map = {x["id"]: x.get("name") or x["id"] for x in app.library.records()}
                except Exception:
                    pass
                prep_jobs = app.material_prep.all_jobs() or {}
                added_prep = False
                for ident, info in prep_jobs.items():
                    if app.material_prep.is_running(ident) or (isinstance(info, dict) and info.get("status") == "running"):
                        total = int((info or {}).get("total") or 0)
                        done = int((info or {}).get("processed") or 0)
                        pct = f"（{int(done * 100 / total)}%）" if total > 0 else ""
                        blockers.append({
                            "kind": "素材整理",
                            "title": f"{inst_map.get(ident, ident)} · AI 素材识别",
                            "detail": f"已整理 {done}/{total} 条实拍素材{pct}" if total > 0 else "正在后台识别镜头内容",
                            "route": f"#institution/{ident}",
                        })
                        added_prep = True
                if not added_prep:
                    blockers.append({
                        "kind": "素材整理",
                        "title": "机构素材 AI 异步整理",
                        "detail": "正在分析视频画面与文字材料",
                        "route": "#library",
                    })

            # 3. Multi-platform publishing (uploading / submitting)
            if app.publish.has_blocking_active():
                pub_added = False
                try:
                    for batch in app.publish.batches():
                        active_items = [
                            i for i in (batch.get("items") or [])
                            if i.get("status") in ("queued", "checking", "uploading", "validating", "submitting")
                        ]
                        if active_items:
                            plats = sorted({str(i.get("platformLabel") or i.get("platform") or "") for i in active_items if i.get("platformLabel") or i.get("platform")})
                            blockers.append({
                                "kind": "多平台发布",
                                "title": f"发布批次（{' / '.join(plats) if plats else '社媒分发'}）",
                                "detail": f"{len(active_items)} 条成品正在上传或提交发布",
                                "route": "#publish",
                            })
                            pub_added = True
                except Exception:
                    pass
                if not pub_added:
                    blockers.append({
                        "kind": "多平台发布",
                        "title": "多平台发布队列",
                        "detail": "正在上传视频或提交平台分发",
                        "route": "#publish",
                    })

            # 4. Voice cloning / training
            try:
                people_cfg = (app.pc.load() or {}).get("people") or {}
                for ident, p in people_cfg.items():
                    if app.voice_training.active(ident):
                        blockers.append({
                            "kind": "音色复刻",
                            "title": f"数字人「{p.get('name') or ident}」音色复刻",
                            "detail": "正在上传录音样本并训练专属音色",
                            "route": "#people",
                        })
            except Exception:
                pass

            # 5. Task acceptance cleanup & product archive
            cleanup_tasks = [t for t in tasks_list if t.get("cleanupStatus") in ("queued", "running")]
            for t in cleanup_tasks:
                blockers.append({
                    "kind": "验收清理",
                    "title": str(t.get("title") or "任务中间文件清理"),
                    "detail": "正在归档成品并清理临时工程文件",
                    "route": f"#task/{t.get('id', '')}",
                })
            pending_archives = [j for j in (app.archive.pending() or []) if j.get("status") in ("queued", "running")]
            if pending_archives and not cleanup_tasks:
                blockers.append({
                    "kind": "成品归档",
                    "title": f"{len(pending_archives)} 项成品归档清理",
                    "detail": "正在打包并落盘最终成品文件",
                    "route": "#works",
                })

            # 6. Cover / video edit re-renders
            try:
                edits_root = app.edits.root
                if edits_root.is_dir():
                    for op_path in edits_root.glob("*/*/operation.json"):
                        op = self.read(op_path, {}) or {}
                        if op.get("status") == "running":
                            blockers.append({
                                "kind": "封面渲染",
                                "title": "成品封面首帧重新合成",
                                "detail": str(op.get("message") or "正在生成新版封面并更新视频首帧"),
                                "route": "#works",
                            })
            except Exception:
                pass

            # 7. Active HTTP write/upload requests (e.g. large material uploads)
            threshold = 1 if exclude_current_request else 0
            if getattr(app, "active_requests", 0) > threshold:
                blockers.append({
                    "kind": "文件上传",
                    "title": "正在上传素材或写入配置",
                    "detail": "当前有网页上传或保存请求正在执行，请等待片刻",
                    "route": "#library",
                })

        return blockers

    # ---- Git Dirty Tree Protection ----
    def check_git_dirty(self) -> list[str]:
        if str(os.environ.get("MONTAGE_UPDATE_SKIP_GIT_CHECK") or "").strip() == "1":
            return []
        skill_dir = self.app.skill
        if not (skill_dir / ".git").exists():
            return []
        try:
            proc = subprocess.run(
                ["git", "-C", str(skill_dir), "status", "--porcelain"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=8,
            )
            if proc.returncode != 0:
                return []
            lines = [ln.rstrip() for ln in proc.stdout.splitlines() if ln.strip()]
            return lines[:20]
        except Exception:
            return []

    # ---- GitHub Official Release Check ----
    def check_now(self, *, force: bool = False) -> dict[str, Any]:
        with self.lock:
            if self._checking:
                return self.full_status(include_blockers=False)
            self._checking = True

        try:
            st = self.load_state()
            repo = self.repo_slug()
            url = f"{self.api_base()}/repos/{repo}/releases/latest"
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": f"nursing-home-montage-workbench/{self.current_version()}",
            }
            token = str(os.environ.get("MONTAGE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if not force and st.get("etag") and st.get("latest"):
                headers["If-None-Match"] = st["etag"]

            req = urllib.request.Request(url, headers=headers, method="GET")
            opener = _build_opener()
            now = time.time()

            try:
                with opener.open(req, timeout=12) as resp:
                    body_bytes = resp.read()
                    new_etag = resp.headers.get("ETag") or ""
                    payload = json.loads(body_bytes.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and st.get("latest"):
                    st["lastCheckedAt"] = now
                    st["lastError"] = ""
                    st["lastErrorCode"] = ""
                    self._save_state(st)
                    return self.full_status(include_blockers=False)
                st["lastCheckedAt"] = now
                if exc.code == 404:
                    st["lastError"] = "仓库尚未发布正式版本 Release。"
                    st["lastErrorCode"] = "no_release"
                elif exc.code in (403, 429):
                    st["lastError"] = "GitHub 接口请求频率受限，请稍后再试。"
                    st["lastErrorCode"] = "rate_limit"
                else:
                    st["lastError"] = f"查询 GitHub 正式版本失败（HTTP {exc.code}）。"
                    st["lastErrorCode"] = f"http_{exc.code}"
                self._save_state(st)
                return self.full_status(include_blockers=False)
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                st["lastCheckedAt"] = now
                msg = str(getattr(exc, "reason", exc) or exc)
                if "timed out" in msg.lower():
                    st["lastError"] = "连接 GitHub 发布服务超时，请稍后重试。"
                    st["lastErrorCode"] = "timeout"
                else:
                    st["lastError"] = "暂时无法连接 GitHub 发布服务，请检查服务器网络。"
                    st["lastErrorCode"] = "network"
                self._save_state(st)
                return self.full_status(include_blockers=False)
            except (ValueError, json.JSONDecodeError):
                st["lastCheckedAt"] = now
                st["lastError"] = "GitHub 返回的版本信息格式异常。"
                st["lastErrorCode"] = "invalid_release"
                self._save_state(st)
                return self.full_status(include_blockers=False)

            # Validate official Release strictly per update.md
            if not isinstance(payload, dict) or payload.get("draft") is True or payload.get("prerelease") is True:
                st["lastCheckedAt"] = now
                st["lastError"] = "远程最新版本为草稿或预发布版，已忽略。"
                st["lastErrorCode"] = "invalid_release"
                self._save_state(st)
                return self.full_status(include_blockers=False)

            tag_name = str(payload.get("tag_name") or "").strip()
            if not TAG_RE.fullmatch(tag_name):
                st["lastCheckedAt"] = now
                st["lastError"] = f"远程版本号「{tag_name or '未知'}」不符合 vX.Y.Z 正式版规范。"
                st["lastErrorCode"] = "invalid_release"
                self._save_state(st)
                return self.full_status(include_blockers=False)

            remote_ver = tag_name[1:]
            expected_zip = f"nursing-home-montage-{tag_name}.zip"
            assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
            zip_asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == expected_zip), None)
            sums_asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == "SHA256SUMS"), None)

            if not zip_asset or not sums_asset or not zip_asset.get("browser_download_url") or not sums_asset.get("browser_download_url"):
                st["lastCheckedAt"] = now
                st["lastError"] = f"正式版 {tag_name} 缺少完整的发行包或 SHA256SUMS 校验文件。"
                st["lastErrorCode"] = "incomplete_release"
                self._save_state(st)
                return self.full_status(include_blockers=False)

            st["lastCheckedAt"] = now
            st["etag"] = new_etag
            st["lastError"] = ""
            st["lastErrorCode"] = ""
            st["latest"] = {
                "version": remote_ver,
                "tag": tag_name,
                "name": str(payload.get("name") or tag_name).strip(),
                "body": str(payload.get("body") or "").strip()[:12000],
                "publishedAt": str(payload.get("published_at") or "").strip(),
                "htmlUrl": str(payload.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag_name}").strip(),
                "zipName": expected_zip,
                "zipUrl": str(zip_asset.get("browser_download_url")).strip(),
                "zipSize": int(zip_asset.get("size") or 0),
                "zipSizeLabel": _format_size(int(zip_asset.get("size") or 0)),
                "sumsUrl": str(sums_asset.get("browser_download_url")).strip(),
            }
            self._save_state(st)
            return self.full_status(include_blockers=False)
        finally:
            with self.lock:
                self._checking = False

    def check_async(self, *, force: bool = False):
        with self.lock:
            if self._checking or self._upgrading:
                return
        threading.Thread(target=self.check_now, kwargs={"force": force}, daemon=True, name="workbench-update-check").start()

    # ---- Schedule & Cancel Idle Upgrade ----
    def schedule_upgrade(self, actor, *, ip: str = "") -> dict[str, Any]:
        with self.lock:
            if self._upgrading:
                raise ValueError("工作台已经在升级中。")
            st = self.load_state()
            latest = st.get("latest")
            cur_ver = self.current_version()
            if not latest or not is_newer_version(latest.get("version", ""), cur_ver):
                raise ValueError("当前没有可升级的新正式版本。")
            dirty = self.check_git_dirty()
            if dirty:
                self._dirty_files = dirty
                raise ValueError(f"检测到本地源码目录有 {len(dirty)} 处未提交的代码改动，为防止覆盖已阻止预约升级。")
            self._scheduled = True
            self._scheduled_version = str(latest["version"])
            self._scheduled_by = actor.display_name or actor.username
            self._scheduled_by_id = actor.user_id
            self._scheduled_at = time.time()
            self._error = ""
            self._error_code = ""
            self._dirty_files = []
            if hasattr(self.app, "auth"):
                self.app.auth.log_audit(
                    actor,
                    action="system.upgrade_schedule",
                    resource_type="system",
                    summary=f"预约空闲后自动升级至 v{self._scheduled_version}",
                    ip=ip,
                )
        # If already idle right now, immediately kick off
        if not self.list_active_blockers(exclude_current_request=True):
            return self.start_upgrade(actor, ip=ip, from_schedule=True)
        return self.full_status(include_blockers=True, exclude_current_request=True)

    def cancel_schedule(self, actor, *, ip: str = "") -> dict[str, Any]:
        with self.lock:
            if self._maintenance_lock or self._upgrading:
                raise ValueError("升级已经开始执行，无法取消。")
            was = self._scheduled
            ver = self._scheduled_version
            self._scheduled = False
            self._scheduled_version = ""
            self._scheduled_by = ""
            self._scheduled_by_id = ""
            self._scheduled_at = None
            if was and actor is not None and hasattr(self.app, "auth"):
                self.app.auth.log_audit(
                    actor,
                    action="system.upgrade_cancel_schedule",
                    resource_type="system",
                    summary=f"取消预约自动升级（v{ver}）",
                    ip=ip,
                )
        return self.full_status(include_blockers=True, exclude_current_request=True)

    # ---- Trigger Immediate Upgrade ----
    def start_upgrade(self, actor, *, ip: str = "", from_schedule: bool = False) -> dict[str, Any]:
        with self.lock:
            if self._upgrading:
                return self.full_status(include_blockers=False)
            st = self.load_state()
            latest = st.get("latest")
            cur_ver = self.current_version()
            if not latest or not is_newer_version(latest.get("version", ""), cur_ver):
                raise ValueError("当前已是最新正式版，无需升级。")
            dirty = self.check_git_dirty()
            if dirty:
                self._dirty_files = dirty
                self._phase = "failed"
                self._error_code = "dirty_git"
                self._error = f"检测到安装目录存在 {len(dirty)} 个未提交的本地代码改动，为防止覆盖已阻止升级。"
                err = ValueError(self._error)
                err.code = "dirty_git"  # type: ignore[attr-defined]
                err.dirty_files = dirty  # type: ignore[attr-defined]
                raise err

            blockers = self.list_active_blockers(exclude_current_request=not from_schedule)
            if blockers:
                err = ValueError(f"当前有 {len(blockers)} 项任务正在运行，暂不允许立即升级。")
                err.code = "tasks_running"  # type: ignore[attr-defined]
                err.blockers = blockers  # type: ignore[attr-defined]
                raise err

            self._upgrading = True
            self._scheduled = False
            self._target_version = str(latest["version"])
            self._phase = "downloading"
            self._step = 1
            self._progress = 15
            self._message = f"正在下载正式发行包 v{self._target_version}…"
            self._error = ""
            self._error_code = ""
            self._dirty_files = []

        actor_name = (getattr(actor, "display_name", "") or getattr(actor, "username", "") or self._scheduled_by or "系统")
        if actor is not None and hasattr(self.app, "auth"):
            self.app.auth.log_audit(
                actor,
                action="system.upgrade_start",
                resource_type="system",
                summary=f"{'预约触发' if from_schedule else '手动发起'}工作台升级：v{cur_ver} → v{self._target_version}",
                ip=ip,
            )

        threading.Thread(
            target=self._run_upgrade_worker,
            args=(cur_ver, dict(latest), actor_name),
            daemon=True,
            name="workbench-upgrade-worker",
        ).start()
        return self.full_status(include_blockers=False)

    # ---- Periodic Tick called from server.py pump() ----
    def tick(self):
        now = time.time()
        # 1. Auto-trigger scheduled upgrade once idle
        trigger_actor = None
        with self.lock:
            should_try_schedule = self._scheduled and not self._upgrading and not self._maintenance_lock
            sched_name = self._scheduled_by
            sched_uid = self._scheduled_by_id
        if should_try_schedule:
            blockers = self.list_active_blockers(exclude_current_request=False)
            if not blockers:
                class _ScheduledActor:
                    display_name = sched_name or "超级管理员"
                    username = sched_name or "super_admin"
                    user_id = sched_uid or ""
                    role_code = "super_admin"
                    is_super_admin = True
                try:
                    self.start_upgrade(_ScheduledActor(), from_schedule=True)
                except Exception as exc:
                    with self.lock:
                        self._scheduled = False
                        self._phase = "failed"
                        self._error = str(exc)
                        self._error_code = getattr(exc, "code", "schedule_failed")
                return

        # 2. Periodic background release check (wait 15s after boot so startup stays snappy)
        if now - self._boot_time < 15:
            return
        if now - self._last_auto_tick_check < 30:
            return
        self._last_auto_tick_check = now

        settings = self.load_settings()
        if not settings.get("autoCheck", True):
            return
        st = self.load_state()
        last_checked = st.get("lastCheckedAt")
        interval_sec = int(settings.get("checkIntervalMinutes", 60)) * 60
        if last_checked is None or (now - float(last_checked)) >= interval_sec:
            self.check_async(force=False)

    # ---- Upgrade Execution Worker (Phase 1 Download -> Phase 2 Lock & Backup -> Phase 3 Atomic Swap -> Phase 4 Restart) ----
    def _download_file(self, url: str, dest: Path, *, max_bytes: int = 300 * 1024 * 1024):
        target_url = _apply_mirror(url)
        headers = {"User-Agent": f"nursing-home-montage-workbench/{self.current_version()}"}
        token = str(os.environ.get("MONTAGE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if token and urllib.parse.urlsplit(target_url).hostname in ("api.github.com", "github.com"):
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(target_url, headers=headers, method="GET")
        opener = _build_opener()
        written = 0
        with opener.open(req, timeout=60) as resp, dest.open("wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("下载的更新包体积异常过大，已中止。")
                out.write(chunk)

    def _validate_and_extract_zip(self, zip_path: Path, extract_dir: Path, expected_ver: str) -> Path:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"更新压缩包损坏（{bad}），已中止升级。")
            infos = zf.infolist()
            if not infos:
                raise ValueError("更新压缩包为空，已中止升级。")
            top_dirs: set[str] = set()
            for info in infos:
                name = info.filename
                if name.startswith("/") or name.startswith("\\") or re.match(r"^[A-Za-z]:", name):
                    raise ValueError("更新包包含非法绝对路径，已拒绝解压。")
                parts = Path(name).parts
                if any(p == ".." for p in parts):
                    raise ValueError("更新包包含越界路径，已拒绝解压。")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError("更新包包含软链接文件，已拒绝解压。")
                if parts:
                    top_dirs.add(parts[0])
            if top_dirs != {"nursing-home-montage"}:
                raise ValueError("更新包根目录结构不符合 nursing-home-montage 规范。")

            extract_root = extract_dir.resolve()
            extract_root.mkdir(parents=True, exist_ok=True)
            for info in infos:
                target = (extract_root / info.filename).resolve()
                if not target.is_relative_to(extract_root):
                    raise ValueError("更新包解压路径越界，已拒绝。")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, length=256 * 1024)
                    perm = (info.external_attr >> 16) & 0o777
                    if perm & 0o111:
                        try:
                            target.chmod(perm)
                        except OSError:
                            pass

        candidate = extract_root / "nursing-home-montage"
        for forbidden in FORBIDDEN_TOP_ENTRIES:
            if (candidate / forbidden).exists():
                raise ValueError(f"更新包异常包含禁带目录 {forbidden}，已拒绝。")
        for req_name in REQUIRED_RELEASE_ENTRIES:
            if not (candidate / req_name).exists():
                raise ValueError(f"更新包缺少必要运行文件 {req_name}，已拒绝。")

        pkg_ver = (candidate / "VERSION").read_text(encoding="utf-8").strip()
        rel_doc = json.loads((candidate / "release.json").read_text(encoding="utf-8"))
        rel_ver = str(rel_doc.get("version") or "").strip()
        if pkg_ver != expected_ver or rel_ver != expected_ver:
            raise ValueError(f"更新包内部版本（{pkg_ver}/{rel_ver}）与目标版本 v{expected_ver} 不一致。")

        skill_md = (candidate / "SKILL.md").read_text(encoding="utf-8")
        if "name: nursing-home-montage" not in skill_md:
            raise ValueError("更新包 SKILL.md 标识校验失败。")

        min_py = _parse_min_python(rel_doc.get("minimum_python"))
        if min_py and sys.version_info[:2] < min_py:
            raise ValueError(f"新版本要求 Python {min_py[0]}.{min_py[1]}+，当前环境为 {sys.version_info[0]}.{sys.version_info[1]}，无法升级。")

        cfg_schema = rel_doc.get("config_schema")
        if cfg_schema != SUPPORTED_CONFIG_SCHEMA:
            raise ValueError(f"新版本配置架构（schema {cfg_schema}）暂不支持自动迁移，已保留原版本。")

        return candidate

    def _collect_relative_files(self, root: Path) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in (".git", "__pycache__", ".DS_Store") for part in rel.parts) or p.suffix == ".pyc":
                continue
            out[rel.as_posix()] = p
        return out

    def _collect_existing_runtime_files(self, skill_dir: Path) -> list[str]:
        manifest = self.read(self.manifest_path, {}) or {}
        if isinstance(manifest.get("files"), list) and manifest["files"]:
            return [str(x) for x in manifest["files"] if (skill_dir / x).is_file()]
        try:
            return [p.relative_to(skill_dir).as_posix() for p in self.app.pc.runtime_files(skill_dir)]
        except Exception:
            return list(self._collect_relative_files(skill_dir).keys())

    def _prune_old_backups(self):
        if not self.backups_root.is_dir():
            return
        dirs = sorted([d for d in self.backups_root.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)
        for old in dirs[MAX_BACKUPS:]:
            shutil.rmtree(old, ignore_errors=True)

    def _copy_file_atomic(self, src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.upd-{uuid.uuid4().hex[:8]}.tmp")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _restore_from_backup(self, backup_dir: Path, skill_dir: Path, newly_added: list[str]):
        for rel in newly_added:
            p = skill_dir / rel
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        backup_files_dir = backup_dir / "files"
        if backup_files_dir.is_dir():
            for src in backup_files_dir.rglob("*"):
                if src.is_file():
                    rel = src.relative_to(backup_files_dir)
                    self._copy_file_atomic(src, skill_dir / rel)

    def _fail_upgrade(self, from_ver: str, to_ver: str, actor_name: str, message: str, code: str, backup_name: str = "", rolled_back: bool = False):
        with self.lock:
            self._upgrading = False
            self._maintenance_lock = False
            self._phase = "failed"
            self._error = message
            self._error_code = code
            self._message = message
        self._record_history(
            from_ver=from_ver,
            to_ver=to_ver,
            status="rolled_back" if rolled_back else "failed",
            actor_name=actor_name,
            backup_name=backup_name,
            error=message,
        )

    def _run_upgrade_worker(self, from_ver: str, latest: dict[str, Any], actor_name: str):
        to_ver = str(latest["version"])
        stage_dir = self.staging_root / f"v{to_ver}-{uuid.uuid4().hex[:8]}"
        backup_dir: Path | None = None
        backup_name = ""
        newly_added: list[str] = []
        skill_dir = self.app.skill

        try:
            stage_dir.mkdir(parents=True, exist_ok=True)

            # ---- Phase 1: Download & Verify (non-destructive) ----
            zip_name = str(latest["zipName"])
            zip_path = stage_dir / zip_name
            sums_path = stage_dir / "SHA256SUMS"
            self._download_file(str(latest["sumsUrl"]), sums_path, max_bytes=1024 * 1024)
            expected_sha = _parse_sha256sums(sums_path.read_text(encoding="utf-8"), zip_name)
            if not expected_sha:
                self._fail_upgrade(from_ver, to_ver, actor_name, "SHA256SUMS 校验文件缺少唯一匹配的哈希值，已中止升级。", "checksum_invalid")
                return

            with self.lock:
                self._progress = 28
                self._message = f"正在下载 {zip_name} 并校验 SHA-256…"

            self._download_file(str(latest["zipUrl"]), zip_path)
            actual_sha = _sha256_file(zip_path)
            if actual_sha != expected_sha:
                self._fail_upgrade(from_ver, to_ver, actor_name, f"更新包 {zip_name} SHA-256 校验不一致，原版本 v{from_ver} 未受影响。", "checksum_mismatch")
                return

            candidate = self._validate_and_extract_zip(zip_path, stage_dir / "extracted", to_ver)
            candidate_files = self._collect_relative_files(candidate)
            candidate_hashes = {rel: _sha256_file(p) for rel, p in candidate_files.items()}

            # ---- Phase 2: Maintenance Lock + Secondary Blocker Check + Backup ----
            with self.lock:
                self._phase = "backing_up"
                self._step = 2
                self._progress = 50
                self._message = "正在锁定新任务并备份当前版本…"

            with self.app.lifecycle:
                with self.lock:
                    self._maintenance_lock = True
                blockers = self.list_active_blockers(exclude_current_request=False)
                if blockers:
                    names = "、".join(b["title"] for b in blockers[:2])
                    self._fail_upgrade(
                        from_ver, to_ver, actor_name,
                        f"下载期间检测到新的运行中任务（{names}），已中止切换并解除锁定。",
                        "tasks_running_during_swap",
                    )
                    return

            dirty = self.check_git_dirty()
            if dirty:
                with self.lock:
                    self._dirty_files = dirty
                self._fail_upgrade(
                    from_ver, to_ver, actor_name,
                    f"检测到本地源码目录存在 {len(dirty)} 处未提交改动，已中止覆盖。",
                    "dirty_git",
                )
                return

            ts_tag = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            backup_name = f"v{from_ver}-{ts_tag}-{uuid.uuid4().hex[:6]}"
            backup_dir = self.backups_root / backup_name
            backup_files_root = backup_dir / "files"
            existing_rels = self._collect_existing_runtime_files(skill_dir)
            for rel in existing_rels:
                src = skill_dir / rel
                if src.is_file():
                    dst = backup_files_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            self.write(backup_dir / "meta.json", {
                "version": from_ver,
                "targetVersion": to_ver,
                "createdAt": time.time(),
                "fileCount": len(existing_rels),
                "skillDir": str(skill_dir),
            })
            self._prune_old_backups()

            # ---- Phase 3: Atomic Replacement + Post-Verification ----
            with self.lock:
                self._phase = "replacing"
                self._step = 3
                self._progress = 75
                self._message = "正在原子替换运行文件并逐文件校验哈希…"

            existing_set = set(existing_rels)
            candidate_set = set(candidate_files.keys())
            newly_added = sorted(candidate_set - existing_set)
            obsolete = sorted(existing_set - candidate_set)

            try:
                for rel, src in candidate_files.items():
                    self._copy_file_atomic(src, skill_dir / rel)
                for rel in obsolete:
                    target = skill_dir / rel
                    if target.is_file():
                        target.unlink()
                # Post-verify every single installed file hash & VERSION
                installed_ver = (skill_dir / "VERSION").read_text(encoding="utf-8").strip()
                if installed_ver != to_ver:
                    raise RuntimeError(f"替换后版本回读不一致（期望 {to_ver}，实际 {installed_ver}）")
                for rel, expected_h in candidate_hashes.items():
                    installed_path = skill_dir / rel
                    if not installed_path.is_file() or _sha256_file(installed_path) != expected_h:
                        raise RuntimeError(f"文件校验失败：{rel}")
            except Exception as swap_exc:
                # Automatic rollback!
                try:
                    self._restore_from_backup(backup_dir, skill_dir, newly_added)
                    restored_ver = (skill_dir / "VERSION").read_text(encoding="utf-8").strip()
                    rb_msg = f"替换文件失败（{swap_exc}），已自动回滚至原版本 v{restored_ver}。"
                except Exception as rb_exc:
                    rb_msg = f"替换失败且自动回滚异常（{rb_exc}），旧版本备份保留在：{backup_dir}"
                self._fail_upgrade(from_ver, to_ver, actor_name, rb_msg, "swap_rollback", backup_name=backup_name, rolled_back=True)
                return

            # Persist installed manifest & success history
            self.write(self.manifest_path, {
                "version": to_ver,
                "installedAt": time.time(),
                "files": sorted(candidate_set),
                "backupName": backup_name,
            })
            self._record_history(
                from_ver=from_ver,
                to_ver=to_ver,
                status="success",
                actor_name=actor_name,
                backup_name=backup_name,
            )

            # ---- Phase 4: Graceful Restart ----
            with self.lock:
                self._phase = "restarting"
                self._step = 4
                self._progress = 96
                mode = self.restart_mode()
                self._message = (
                    "文件校验通过，正在优雅重启工作台（systemd 约 3 秒自动拉起）…"
                    if mode == "systemd"
                    else "文件校验通过，正在自动重启工作台服务…"
                )

            if str(os.environ.get("MONTAGE_UPDATE_NO_RESTART") or "").strip() == "1":
                with self.lock:
                    self._upgrading = False
                    self._maintenance_lock = False
                    self._phase = "idle"
                    self._step = 0
                    self._progress = 100
                    self._message = f"已升级至 v{to_ver}"
                return

            # Brief grace period so polling clients receive step 4 ("restarting") before socket closes
            time.sleep(0.8)
            self._trigger_process_restart()
        except Exception as exc:
            self._fail_upgrade(from_ver, to_ver, actor_name, f"升级过程出现异常：{exc}", "upgrade_error", backup_name=backup_name)
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    def _trigger_process_restart(self):
        mode = self.restart_mode()
        if mode != "systemd":
            self._spawn_relaunch_helper()
        with self.app.lifecycle:
            self.app.exiting = True
        if self.server_ref is not None:
            threading.Thread(target=self.server_ref.shutdown, daemon=True, name="workbench-restart-shutdown").start()

    def _spawn_relaunch_helper(self):
        """Spawn a detached helper process that waits for old PID & service.lock, then launches new server.py."""
        old_pid = os.getpid()
        project_dir = str(self.app.project)
        server_script = str((self.app.skill / "scripts" / "workbench" / "server.py").resolve())
        # Filter out one-shot flags like --open-page when relaunching
        raw_argv = list(sys.argv[1:])
        cleaned_argv: list[str] = []
        skip_next = False
        for arg in raw_argv:
            if skip_next:
                skip_next = False
                continue
            if arg == "--open-page":
                continue
            cleaned_argv.append(arg)
        # Ensure --project is explicitly passed
        if "--project" not in cleaned_argv:
            cleaned_argv = ["--project", project_dir, *cleaned_argv]

        cmd = [sys.executable, server_script, *cleaned_argv]
        if shutil.which("rtk"):
            cmd = ["rtk", "proxy", *cmd]

        log_path = str(self.app.data / "server.log")
        lock_path = str(self.app.data / "service.lock")
        helper_code = (
            "import os, sys, time, json, subprocess\n"
            "old_pid, lock_path, log_path, cwd, cmd = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], json.loads(sys.argv[5])\n"
            "def pid_alive(pid):\n"
            "    try:\n"
            "        os.kill(pid, 0)\n"
            "        return True\n"
            "    except OSError:\n"
            "        return False\n"
            "for _ in range(120):\n"
            "    if not pid_alive(old_pid):\n"
            "        break\n"
            "    time.sleep(0.25)\n"
            "time.sleep(0.35)\n"
            "fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)\n"
            "kw = {'creationflags': subprocess.DETACHED_PROCESS} if sys.platform == 'win32' else {'start_new_session': True}\n"
            "with os.fdopen(fd, 'a') as log:\n"
            "    subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kw)\n"
        )
        background = {"creationflags": subprocess.DETACHED_PROCESS} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(
            [sys.executable, "-c", helper_code, str(old_pid), lock_path, log_path, project_dir, json.dumps(cmd)],
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **background,
        )

    # ---- Public Status Serialization (RBAC-aware) ----
    def public_health(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "version": self.current_version(),
                "upgrading": bool(self._upgrading or self._maintenance_lock),
                "phase": self._phase,
                "targetVersion": self._target_version if (self._upgrading or self._maintenance_lock) else "",
            }

    def state_summary_for_actor(self, actor) -> dict[str, Any]:
        """Lightweight summary attached to /api/state.
        Non-super-admins NEVER see update availability or settings — only active restart maintenance notice.
        """
        with self.lock:
            upgrading = bool(self._upgrading or self._maintenance_lock)
            phase = self._phase
            step = self._step
            progress = self._progress
            message = self._message
            target_ver = self._target_version

        if not getattr(actor, "is_super_admin", False):
            return {
                "canManage": False,
                "upgrading": upgrading,
                "phase": phase if upgrading else "idle",
                "targetVersion": target_ver if upgrading else "",
                "message": message if upgrading else "",
            }

        cur_ver = self.current_version()
        st = self.load_state()
        settings = self.load_settings()
        latest = st.get("latest")
        latest_ver = str((latest or {}).get("version") or "")
        has_update = bool(latest_ver and is_newer_version(latest_ver, cur_ver))

        with self.lock:
            scheduled = bool(self._scheduled and has_update)
            scheduled_ver = self._scheduled_version if scheduled else ""
            checking = self._checking
            err_msg = self._error
            err_code = self._error_code

        return {
            "canManage": True,
            "currentVersion": cur_ver,
            "latestVersion": latest_ver,
            "hasUpdate": has_update,
            "ignoredVersion": settings.get("ignoredVersion", ""),
            "isIgnored": bool(has_update and settings.get("ignoredVersion") == latest_ver),
            "checking": checking,
            "upgrading": upgrading,
            "phase": phase,
            "step": step,
            "progress": progress,
            "targetVersion": target_ver or (latest_ver if has_update else ""),
            "message": message,
            "error": err_msg,
            "errorCode": err_code,
            "scheduled": scheduled,
            "scheduledVersion": scheduled_ver,
            "lastCheckedAt": st.get("lastCheckedAt"),
            "lastError": st.get("lastError", ""),
            "lastErrorCode": st.get("lastErrorCode", ""),
            "restartMode": self.restart_mode(),
        }

    def full_status(self, *, include_blockers: bool = True, exclude_current_request: bool = False) -> dict[str, Any]:
        cur_ver = self.current_version()
        st = self.load_state()
        settings = self.load_settings()
        latest = st.get("latest")
        latest_ver = str((latest or {}).get("version") or "")
        has_update = bool(latest_ver and is_newer_version(latest_ver, cur_ver))
        blockers = self.list_active_blockers(exclude_current_request=exclude_current_request) if include_blockers else []
        dirty = self.check_git_dirty() if include_blockers else list(self._dirty_files)

        with self.lock:
            upgrading = bool(self._upgrading or self._maintenance_lock)
            return {
                "currentVersion": cur_ver,
                "hasUpdate": has_update,
                "latest": latest,
                "settings": settings,
                "checking": self._checking,
                "upgrading": upgrading,
                "maintenanceLocked": self._maintenance_lock,
                "phase": self._phase,
                "step": self._step,
                "progress": self._progress,
                "targetVersion": self._target_version or (latest_ver if has_update else ""),
                "message": self._message,
                "error": self._error,
                "errorCode": self._error_code,
                "dirtyFiles": dirty,
                "scheduled": bool(self._scheduled and has_update),
                "scheduledVersion": self._scheduled_version if (self._scheduled and has_update) else "",
                "scheduledBy": self._scheduled_by if self._scheduled else "",
                "scheduledAt": self._scheduled_at if self._scheduled else None,
                "blockers": blockers,
                "lastCheckedAt": st.get("lastCheckedAt"),
                "lastError": st.get("lastError", ""),
                "lastErrorCode": st.get("lastErrorCode", ""),
                "history": st.get("history", []),
                "restartMode": self.restart_mode(),
                "deployMode": getattr(self.app, "deploy_mode", "local"),
                "repo": self.repo_slug(),
            }

"""Institution-level asynchronous material preparation, caching, manual editing and task reuse."""
from pathlib import Path
import threading
import time
import uuid


MEDIA_KINDS = ("video", "image")
ANALYSIS_VERSION = 1
MAX_DESCRIPTION_LEN = 700
MAX_PREVIEW_LEN = 320
VISUAL_BATCH_SIZE = 4


class MaterialPreparation:
    """Prepares and caches per-institution asset metadata and vision summaries."""

    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write
        self.lock = threading.RLock()
        self.threads = {}
        self.active_runs = {}

    def _path(self, ident):
        return self.app.library.path(ident).parent / "material-analysis.json"

    def _load(self, ident):
        data = self.read(self._path(ident), {})
        if not isinstance(data, dict):
            data = {}
        items = data.get("items")
        if not isinstance(items, dict):
            items = {}
        return {
            "version": data.get("version", ANALYSIS_VERSION),
            "status": data.get("status", "idle"),
            "mode": data.get("mode", "incremental"),
            "runId": data.get("runId"),
            "startedAt": data.get("startedAt"),
            "updatedAt": data.get("updatedAt"),
            "completedAt": data.get("completedAt"),
            "total": int(data.get("total", 0) or 0),
            "processed": int(data.get("processed", 0) or 0),
            "currentName": data.get("currentName") or "",
            "currentStage": data.get("currentStage") or "",
            "error": data.get("error"),
            "needsModel": bool(data.get("needsModel")),
            "targetAssetId": data.get("targetAssetId"),
            "items": items,
        }

    def _save(self, ident, data):
        data["version"] = ANALYSIS_VERSION
        data["updatedAt"] = time.time()
        self.write(self._path(ident), data)

    def _is_thread_alive(self, ident):
        with self.lock:
            thread = self.threads.get(ident)
            return bool(thread and thread.is_alive())

    def active_any(self):
        with self.lock:
            return any(t.is_alive() for t in self.threads.values())

    def is_running(self, ident):
        return self._is_thread_alive(ident)

    def _asset_ready(self, asset, entry):
        if not isinstance(entry, dict):
            return False
        if asset["kind"] in MEDIA_KINDS:
            return bool(str(entry.get("description") or "").strip())
        if asset["kind"] == "text":
            return entry.get("analyzedAt") is not None and not entry.get("blocked")
        return True

    def _asset_has_metadata(self, asset, entry):
        if not isinstance(entry, dict):
            return False
        if asset["kind"] in MEDIA_KINDS:
            return bool(entry.get("width") and entry.get("height"))
        if asset["kind"] == "text":
            return entry.get("analyzedAt") is not None
        return True

    def asset_view(self, ident, asset, data=None):
        """Return public analysis payload for one asset."""
        data = data if data is not None else self._load(ident)
        entry = data["items"].get(asset["sha256"])
        running = self._is_thread_alive(ident)
        target_id = data.get("targetAssetId") if running else None
        is_analyzing = running and (
            target_id == asset["id"]
            or (target_id is None and asset.get("enabled") and not self._asset_ready(asset, entry))
        )
        if not isinstance(entry, dict):
            return {
                "status": "running" if is_analyzing else "pending",
                "description": "",
                "motion": False,
                "width": None,
                "height": None,
                "duration": None,
                "textLength": 0,
                "textPreview": "",
                "blocked": None,
                "source": None,
                "analyzedAt": None,
                "editedAt": None,
            }
        ready = self._asset_ready(asset, entry)
        status = "running" if is_analyzing else ("blocked" if entry.get("blocked") else ("ready" if ready else "pending"))
        return {
            "status": status,
            "description": str(entry.get("description") or ""),
            "motion": bool(entry.get("motion")) if asset["kind"] == "video" else False,
            "width": entry.get("width"),
            "height": entry.get("height"),
            "duration": entry.get("duration"),
            "textLength": int(entry.get("textLength") or 0),
            "textPreview": str(entry.get("textPreview") or ""),
            "blocked": entry.get("blocked"),
            "source": entry.get("source") or "ai",
            "analyzedAt": entry.get("analyzedAt"),
            "editedAt": entry.get("editedAt"),
        }

    def summary(self, value, data=None):
        """Compute live preparation summary for an institution record."""
        ident = value["id"]
        data = data if data is not None else self._load(ident)
        running = self._is_thread_alive(ident)
        current = [a for a in value.get("assets", []) if a.get("current")]
        enabled = [a for a in current if a.get("enabled")]
        items = data.get("items", {})
        enabled_media = [a for a in enabled if a["kind"] in MEDIA_KINDS]
        enabled_videos = [a for a in enabled if a["kind"] == "video"]
        enabled_text = [a for a in enabled if a["kind"] == "text"]

        analyzed_count = 0
        pending_count = 0
        blocked_count = 0
        motion_videos = 0
        edited_count = 0
        latest_at = data.get("completedAt")

        for asset in enabled:
            entry = items.get(asset["sha256"])
            if isinstance(entry, dict):
                if entry.get("source") == "manual":
                    edited_count += 1
                if entry.get("blocked"):
                    blocked_count += 1
                if entry.get("analyzedAt") and (latest_at is None or entry["analyzedAt"] > latest_at):
                    latest_at = entry["analyzedAt"]
            if self._asset_ready(asset, entry):
                analyzed_count += 1
                if asset["kind"] == "video" and entry.get("motion"):
                    motion_videos += 1
            else:
                pending_count += 1

        total_enabled = len(enabled)
        model_configured = bool(self.app.models.public().get("configured"))

        if running:
            status = "running"
        elif total_enabled == 0:
            status = "empty"
        elif pending_count == 0:
            status = "ready"
        elif data.get("status") == "failed" or data.get("error"):
            status = "failed"
        else:
            status = "pending"

        total = data.get("total", 0) if running else total_enabled
        processed = min(data.get("processed", 0), total) if running else analyzed_count
        percent = int(round(processed / total * 100)) if total > 0 else (100 if status == "ready" else 0)

        error = data.get("error") if status == "failed" else None
        needs_model = bool((not model_configured) and (status in ("pending", "failed") or data.get("needsModel")))
        if needs_model and not error and status == "failed":
            error = "请先在「设置」中填写制作模型的 API Key，再整理素材。"

        block_reason = ""
        if status == "running":
            block_reason = f"「{value['name']}」正在后台整理素材（{processed}/{total}），完成后即可开始制作。"
        elif status == "failed":
            block_reason = error or "素材整理尚未完成，请先重新整理素材后再开始制作。"
        elif status == "pending":
            if needs_model:
                block_reason = "请先在「设置」中配置制作大模型，再完成这家机构的素材整理。"
            else:
                block_reason = "这家机构有尚未整理的素材，请先完成素材整理再开始制作。"

        motion_warning = status == "ready" and len(enabled_videos) > 0 and motion_videos == 0

        return {
            "institutionId": ident,
            "institutionName": value.get("name", ""),
            "status": status,
            "mode": data.get("mode", "incremental"),
            "runId": data.get("runId"),
            "total": total,
            "processed": processed,
            "percent": max(0, min(100, percent)),
            "totalEnabled": total_enabled,
            "analyzedCount": analyzed_count,
            "pendingCount": pending_count,
            "blockedCount": blocked_count,
            "videoCount": len(enabled_videos),
            "imageCount": len(enabled_media) - len(enabled_videos),
            "textCount": len(enabled_text),
            "motionVideoCount": motion_videos,
            "motionWarning": motion_warning,
            "editedCount": edited_count,
            "currentName": data.get("currentName", "") if running else "",
            "currentStage": data.get("currentStage", "") if running else "",
            "startedAt": data.get("startedAt"),
            "updatedAt": data.get("updatedAt"),
            "completedAt": latest_at,
            "error": error,
            "needsModel": needs_model,
            "targetAssetId": data.get("targetAssetId") if running else None,
            "canCreate": status == "ready",
            "blockReason": block_reason,
        }

    def all_jobs(self):
        """Return preparation summaries for all institutions for global UI polling."""
        jobs = []
        for value in self.app.library.records():
            summary = self.summary(value)
            if summary["status"] != "empty":
                jobs.append(summary)
        return jobs

    def assert_ready_for_task(self, ident):
        """Block task creation/start while preparation is running or incomplete."""
        value = self.app.library.get(ident)
        info = self.summary(value)
        if info["status"] == "running":
            raise ValueError(info["blockReason"] or "机构素材正在后台整理中，请等待整理完成后再制作。")
        if self.app.workbench_host == "local" and info["status"] in ("pending", "failed"):
            raise ValueError(info["blockReason"] or "请先完成这家机构的素材整理，再开始制作。")
        return info

    def needs_incremental(self, value):
        data = self._load(value["id"])
        items = data.get("items", {})
        enabled = [a for a in value.get("assets", []) if a.get("current") and a.get("enabled")]
        return any(not self._asset_ready(a, items.get(a["sha256"])) for a in enabled)

    def start(self, ident, *, mode="incremental", asset_id=None, auto=False):
        """Start asynchronous preparation for one institution."""
        if mode not in ("incremental", "full", "single"):
            raise ValueError("整理模式无效")
        if asset_id:
            mode = "single"
        with self.app.transaction():
            value = self.app.library.get(ident)
            enabled = [a for a in value.get("assets", []) if a.get("current") and a.get("enabled")]
            if mode == "single":
                target = next((a for a in value.get("assets", []) if a.get("current") and a["id"] == asset_id), None)
                if not target:
                    raise ValueError("找不到要重新识别的素材")
                targets = [target]
            else:
                targets = enabled

        if not targets:
            return self.summary(value)

        with self.lock:
            existing = self.threads.get(ident)
            if existing and existing.is_alive():
                if auto:
                    return self.summary(value)
                raise ValueError("这家机构正在后台整理素材，请等待当前整理完成。")

            data = self._load(ident)
            items = data.setdefault("items", {})
            if mode == "incremental":
                pending = [a for a in targets if not self._asset_ready(a, items.get(a["sha256"]))]
                if not pending:
                    data.update(status="ready", error=None, needsModel=False, currentName="", currentStage="done",
                                targetAssetId=None, total=len(targets), processed=len(targets),
                                completedAt=data.get("completedAt") or time.time())
                    self._save(ident, data)
                    return self.summary(value, data)
            else:
                pending = list(targets)

            needs_vision = any(a["kind"] in MEDIA_KINDS for a in pending)
            model_ok = bool(self.app.models.public().get("configured"))
            if needs_vision and not model_ok:
                # Extract local metadata & text immediately even without an LLM key.
                self._extract_local_only(ident, pending, data)
                data.update(
                    status="failed",
                    mode=mode,
                    error="请先在「设置」中填写制作模型的 API Key，再整理实拍画面。",
                    needsModel=True,
                    currentName="",
                    currentStage="needs_model",
                    targetAssetId=asset_id,
                    total=len(pending),
                    processed=0,
                )
                self._save(ident, data)
                return self.summary(value, data)

            run_id = uuid.uuid4().hex[:12]
            data.update(
                status="running",
                mode=mode,
                runId=run_id,
                startedAt=time.time(),
                total=len(pending),
                processed=0,
                currentName=pending[0]["name"],
                currentStage="metadata",
                error=None,
                needsModel=False,
                targetAssetId=asset_id,
            )
            self._save(ident, data)
            thread = threading.Thread(
                target=self._worker,
                args=(ident, run_id, mode, asset_id),
                name=f"material-prep-{ident}",
                daemon=True,
            )
            self.threads[ident] = thread
            self.active_runs[ident] = run_id
            thread.start()
        return self.summary(value, data)

    def _extract_local_asset(self, ident, asset):
        _, stored = self.app.library.asset(ident, asset["id"])
        if not stored.is_file():
            raise ValueError(f"素材文件缺失：{asset['name']}")
        from shared_cache import SharedCache
        import workflow

        cache = SharedCache(self.app.project)

        def analyze(path):
            ext = path.suffix.lower()
            if ext in (".txt", ".md", ".docx", ".pdf"):
                return {"kind": "text", "text": workflow.extract_text(path)}
            meta = workflow.probe(path)
            stream = next((x for x in meta.get("streams", []) if x.get("codec_type") == "video"), None)
            if not stream:
                raise RuntimeError("未检测到有效画面流：" + asset["name"])
            return {
                "kind": "image" if ext in (".jpg", ".jpeg", ".png", ".webp", ".heic") else "video",
                "width": int(stream.get("width") or 0),
                "height": int(stream.get("height") or 0),
                "duration": round(float(meta.get("format", {}).get("duration", 0) or 0), 2),
            }

        analyzer_version = "inventory-v2-" + workflow.sha(Path(workflow.__file__))
        details, _, _ = cache.material(stored, analyze, analyzer_version)
        return stored, details

    def _extract_local_only(self, ident, pending, data):
        items = data.setdefault("items", {})
        for asset in pending:
            sha = asset["sha256"]
            try:
                _, details = self._extract_local_asset(ident, asset)
            except Exception as exc:
                if asset["kind"] == "text":
                    items[sha] = {
                        "sha256": sha,
                        "kind": "text",
                        "blocked": self.app.redact(str(exc))[:300],
                        "textLength": 0,
                        "textPreview": "",
                        "source": "ai",
                        "analyzedAt": time.time(),
                        "editedAt": None,
                    }
                continue
            if asset["kind"] == "text":
                text = str(details.get("text") or "").strip()
                items[sha] = {
                    "sha256": sha,
                    "kind": "text",
                    "textLength": len(text),
                    "textPreview": text[:MAX_PREVIEW_LEN],
                    "blocked": None,
                    "source": "ai",
                    "analyzedAt": time.time(),
                    "editedAt": None,
                }
            else:
                prev = items.get(sha) or {}
                items[sha] = {
                    **prev,
                    "sha256": sha,
                    "kind": asset["kind"],
                    "width": details.get("width"),
                    "height": details.get("height"),
                    "duration": details.get("duration"),
                }

    def _worker(self, ident, run_id, mode, asset_id):
        try:
            value = self.app.library.get(ident)
            data = self._load(ident)
            items = data.setdefault("items", {})

            if mode == "single":
                targets = [a for a in value.get("assets", []) if a.get("current") and a["id"] == asset_id]
            else:
                targets = [a for a in value.get("assets", []) if a.get("current") and a.get("enabled")]

            if mode == "incremental":
                pending = [a for a in targets if not self._asset_ready(a, items.get(a["sha256"]))]
            else:
                pending = list(targets)

            total = len(pending)
            processed = 0
            data.update(total=total, processed=0, currentStage="metadata", error=None, needsModel=False)
            self._save(ident, data)

            text_assets = [a for a in pending if a["kind"] == "text"]
            media_assets = [a for a in pending if a["kind"] in MEDIA_KINDS]

            # Step 1: Extract text documents.
            for asset in text_assets:
                data.update(currentName=asset["name"], currentStage="text", processed=processed)
                self._save(ident, data)
                sha = asset["sha256"]
                try:
                    _, details = self._extract_local_asset(ident, asset)
                    text = str(details.get("text") or "").strip()
                    items[sha] = {
                        "sha256": sha,
                        "kind": "text",
                        "textLength": len(text),
                        "textPreview": text[:MAX_PREVIEW_LEN],
                        "blocked": None,
                        "source": "ai",
                        "analyzedAt": time.time(),
                        "editedAt": None,
                    }
                except Exception as exc:
                    items[sha] = {
                        "sha256": sha,
                        "kind": "text",
                        "textLength": 0,
                        "textPreview": "",
                        "blocked": self.app.redact(str(exc))[:300],
                        "source": "ai",
                        "analyzedAt": time.time(),
                        "editedAt": None,
                    }
                processed += 1
                data.update(processed=processed)
                self._save(ident, data)

            # Step 2: Probe media metadata + extract frames + call vision model.
            if media_assets:
                from auto_editor import frame as read_frame
                from model_api import ModelResponseError, image_part

                client = self.app.models.client()
                probed = {}
                for asset in media_assets:
                    data.update(currentName=asset["name"], currentStage="probe", processed=processed)
                    self._save(ident, data)
                    stored, details = self._extract_local_asset(ident, asset)
                    probed[asset["id"]] = (stored, details)

                def observe_group(group):
                    nonlocal processed
                    candidates = {f"M{i}": a for i, a in enumerate(group, 1)}
                    visuals = []
                    for code, asset in candidates.items():
                        stored, details = probed[asset["id"]]
                        duration = float(details.get("duration") or 0)
                        times = (round(0.2 * duration, 3), round(0.65 * duration, 3)) if asset["kind"] == "video" else (None,)
                        for idx, sec in enumerate(times, 1):
                            raw = read_frame(stored, sec, timeout=45)
                            visuals.extend([{"type": "text", "text": f"{code} 图 {idx}"}, image_part(raw)])
                    instruction = (
                        "按素材短编号观察实拍画面。每个编号返回一条客观、简洁的可见内容描述（40–100 字，说明场景、设施或人物活动），"
                        "视频对照两个时点判断镜头或主体是否有明显移动（motion: true/false）。"
                        "不凭图片推断护理水平、价格或人员身份。"
                        'JSON {"media":[{"id":"M1","description":"可见细节","motion":true}]}。'
                    )
                    payload = {"media": {code: candidates[code]["kind"] for code in candidates}}
                    response = client.ask(instruction, payload, visuals, max_tokens=2400)
                    rows = response.get("media") if isinstance(response, dict) else None
                    if not isinstance(rows, list):
                        return set()
                    seen = set()
                    now = time.time()
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        code, desc = row.get("id"), row.get("description")
                        if (not isinstance(code, str) or code not in candidates or code in seen
                                or not isinstance(desc, str) or not desc.strip()):
                            continue
                        asset = candidates[code]
                        _, details = probed[asset["id"]]
                        sha = asset["sha256"]
                        items[sha] = {
                            "sha256": sha,
                            "kind": asset["kind"],
                            "width": details.get("width"),
                            "height": details.get("height"),
                            "duration": details.get("duration"),
                            "description": desc.strip()[:MAX_DESCRIPTION_LEN],
                            "motion": asset["kind"] == "video" and row.get("motion") is True,
                            "blocked": None,
                            "source": "ai",
                            "analyzedAt": now,
                            "editedAt": None,
                        }
                        seen.add(code)
                        processed += 1
                    data.update(processed=min(processed, total))
                    self._save(ident, data)
                    return seen

                for start_idx in range(0, len(media_assets), VISUAL_BATCH_SIZE):
                    batch = media_assets[start_idx:start_idx + VISUAL_BATCH_SIZE]
                    data.update(currentName=batch[0]["name"], currentStage="vision", processed=processed)
                    self._save(ident, data)
                    done_codes = set()
                    try:
                        done_codes = observe_group(batch)
                    except ModelResponseError:
                        done_codes = set()
                    missing_assets = [a for i, a in enumerate(batch, 1) if f"M{i}" not in done_codes]
                    for single in missing_assets:
                        data.update(currentName=single["name"], currentStage="vision", processed=processed)
                        self._save(ident, data)
                        retry_codes = observe_group([single])
                        if not retry_codes:
                            raise ValueError(f"视觉模型未返回素材「{single['name']}」的画面描述，请点击重试。")

            # Finalize status.
            now = time.time()
            data.update(
                status="ready",
                processed=total,
                currentName="",
                currentStage="done",
                completedAt=now,
                error=None,
                needsModel=False,
                targetAssetId=None,
            )
            self._save(ident, data)
        except Exception as exc:
            msg = self.app.redact(str(exc))[:500] or "素材整理出现异常，请重试。"
            data = self._load(ident)
            data.update(
                status="failed",
                error=msg,
                needsModel="API Key" in msg or "模型" in msg and "配置" in msg,
                currentStage="failed",
                targetAssetId=None,
            )
            self._save(ident, data)
        finally:
            with self.lock:
                if self.active_runs.get(ident) == run_id:
                    self.active_runs.pop(ident, None)
                    self.threads.pop(ident, None)

    def update_asset_analysis(self, ident, asset_id, *, description=None, motion=None):
        """Manually update one asset's visual summary and motion flag."""
        with self.app.transaction():
            value = self.app.library.get(ident)
            asset = next((a for a in value.get("assets", []) if a.get("current") and a["id"] == asset_id), None)
            if not asset:
                raise ValueError("素材不存在或已被新版本替代")
            if asset["kind"] not in MEDIA_KINDS:
                raise ValueError("仅支持修改视频或照片的画面摘要")
            text = str(description if description is not None else "").strip()
            if not text:
                raise ValueError("请填写画面摘要内容")
            if len(text) > MAX_DESCRIPTION_LEN:
                raise ValueError(f"画面摘要最多 {MAX_DESCRIPTION_LEN} 字")

            data = self._load(ident)
            sha = asset["sha256"]
            prev = data["items"].get(sha) or {}
            if not (prev.get("width") and prev.get("height")):
                try:
                    _, details = self._extract_local_asset(ident, asset)
                    prev = {
                        **prev,
                        "width": details.get("width"),
                        "height": details.get("height"),
                        "duration": details.get("duration"),
                    }
                except Exception:
                    pass
            now = time.time()
            data["items"][sha] = {
                **prev,
                "sha256": sha,
                "kind": asset["kind"],
                "description": text,
                "motion": bool(motion) if asset["kind"] == "video" else False,
                "blocked": None,
                "source": "manual",
                "analyzedAt": prev.get("analyzedAt") or now,
                "editedAt": now,
            }
            # Refresh top-level status if all enabled assets are now ready.
            enabled = [a for a in value.get("assets", []) if a.get("current") and a.get("enabled")]
            if enabled and all(self._asset_ready(a, data["items"].get(a["sha256"])) for a in enabled):
                data.update(status="ready", error=None, needsModel=False, completedAt=now)
            self._save(ident, data)
            return self.app.library.public(value, True)

    def cached_media_map(self, ident):
        """Return sha256 -> {description, motion} map for task execution reuse."""
        if not ident:
            return {}
        try:
            data = self._load(ident)
        except Exception:
            return {}
        result = {}
        for sha, entry in (data.get("items") or {}).items():
            if not isinstance(entry, dict):
                continue
            desc = str(entry.get("description") or "").strip()
            if desc and entry.get("kind") in MEDIA_KINDS:
                result[sha] = {
                    "description": desc[:MAX_DESCRIPTION_LEN],
                    "motion": entry.get("kind") == "video" and entry.get("motion") is True,
                }
        return result

    def seed_media_cache(self, task, folder):
        """Pre-populate task independent-media-review.json from institution cache."""
        ident = task.get("institutionId")
        if not ident:
            return 0
        cached = self.cached_media_map(ident)
        if not cached:
            return 0
        folder = Path(folder)
        cache_path = folder / "independent-media-review.json"
        stored = self.read(cache_path, {})
        if not isinstance(stored, dict):
            stored = {}
        inventory = self.read(folder / "inventory.json", {})
        files = inventory.get("files") if isinstance(inventory, dict) else None
        allowed_shas = {row.get("sha256") for row in files if isinstance(row, dict) and row.get("sha256")} if files else None
        seeded = 0
        for sha, value in cached.items():
            if allowed_shas is not None and sha not in allowed_shas:
                continue
            if sha not in stored:
                stored[sha] = value
                seeded += 1
        if seeded:
            self.write(cache_path, stored)
        return seeded

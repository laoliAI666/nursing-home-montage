"""Multi-platform publishing configuration, account sync, batch queue and product status."""
from __future__ import annotations

import copy
from pathlib import Path
import random
import re
import threading
import time
import uuid
from typing import Any

from publish_providers import (
    PLATFORMS,
    PLATFORM_KEY_TO_CN,
    PublishError,
    YixiaoerProvider,
    build_video_payload,
    find_yxer_binary,
)


SECRET_SLOT = "yixiaoer.api_key"
DEFAULT_PUBLISH_SETTINGS = {
    "provider": "yixiaoer",
    "channel": "local",
    "client_id": "",
    "auto_fallback_local": False,
    "visibility": "private",
    "ai_declaration": True,
    "concurrency": 2,
}
ACTIVE_ITEM_STATUSES = ("queued", "checking", "uploading", "validating", "submitting", "polling")
POLL_INTERVAL_SECONDS = 8.0
POLL_TIMEOUT_SECONDS = 900.0


def product_key(task_id: str, index: int, version: str = "original") -> str:
    return f"{task_id}:{int(index)}:{version or 'original'}"


def parse_publication_text(raw: str, fallback_title: str = "") -> dict[str, Any]:
    text = (raw or "").replace("\r\n", "\n").strip()
    if not text:
        return {"title": fallback_title, "description": "", "topics": [], "fullDescription": ""}
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    title = blocks[0] if blocks else fallback_title
    title = re.sub(r"^(?:标题|发布标题)[:：]\s*", "", title).strip() or fallback_title
    body_parts: list[str] = []
    topics: list[str] = []
    for block in blocks[1:]:
        cleaned = re.sub(r"^(?:正文|文案|描述|发布描述|话题|标签)[:：]\s*", "", block).strip()
        tokens = cleaned.split()
        if tokens and all(tok.startswith("#") and len(tok) > 1 for tok in tokens):
            topics.extend(tokens)
        else:
            body_parts.append(cleaned)
    body = "\n".join(body_parts).strip()
    topic_str = " ".join(topics).strip()
    full_desc = "\n\n".join(x for x in (body, topic_str) if x).strip()
    return {
        "title": title,
        "description": body,
        "topics": topics,
        "fullDescription": full_desc or title,
    }


def normalize_bindings(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, _ in PLATFORMS:
        item = raw.get(key)
        if not isinstance(item, dict):
            continue
        aid = str(item.get("accountId") or "").strip()
        if not aid:
            continue
        out[key] = {
            "provider": str(item.get("provider") or "yixiaoer"),
            "accountId": aid,
            "authorId": str(item.get("authorId") or "").strip(),
            "accountName": str(item.get("accountName") or "").strip(),
        }
    return out


class PublishService:
    def __init__(self, app, read_fn, write_fn, *, provider_factory=None, recover: bool = True, jitter: bool = True):
        self.app = app
        self.read = read_fn
        self.write = write_fn
        self.provider_factory = provider_factory
        self.jitter = jitter
        self.root = app.data / "publish"
        self.batches_dir = self.root / "batches"
        self.accounts_path = self.root / "accounts.json"
        self.app.ensure_dir(self.batches_dir)
        self._dispatch_lock = threading.Lock()
        self._account_locks: dict[str, threading.Lock] = {}
        self._account_locks_guard = threading.Lock()
        self._upload_locks: dict[str, threading.Lock] = {}
        self._running_items: set[str] = set()
        if recover:
            self._recover_batches()

    def _account_lock(self, account_id: str) -> threading.Lock:
        with self._account_locks_guard:
            lock = self._account_locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._account_locks[account_id] = lock
            return lock

    # ---- Settings & credentials ----
    def _secrets_path(self) -> Path:
        return self.app.pc.directory() / "secrets.json"

    def api_key(self) -> str:
        secrets_data = self.read(self._secrets_path(), {}) or {}
        return str(secrets_data.get(SECRET_SLOT) or "").strip()

    def secret_values(self) -> list[str]:
        val = self.api_key()
        return [val] if val else []

    def raw_settings(self) -> dict[str, Any]:
        cfg = self.app.pc.load()
        stored = ((cfg.get("settings") or {}).get("publish") or {})
        merged = dict(DEFAULT_PUBLISH_SETTINGS)
        if isinstance(stored, dict):
            for k in DEFAULT_PUBLISH_SETTINGS:
                if k in stored and stored[k] is not None:
                    merged[k] = stored[k]
        merged["channel"] = merged["channel"] if merged["channel"] in ("local", "cloud") else "local"
        merged["visibility"] = merged["visibility"] if merged["visibility"] in ("private", "public") else "private"
        merged["client_id"] = str(merged.get("client_id") or "").strip()
        merged["auto_fallback_local"] = bool(merged.get("auto_fallback_local"))
        merged["ai_declaration"] = bool(merged.get("ai_declaration", True))
        try:
            merged["concurrency"] = max(1, min(4, int(merged.get("concurrency", 2))))
        except (TypeError, ValueError):
            merged["concurrency"] = 2
        return merged

    def public_settings(self) -> dict[str, Any]:
        s = self.raw_settings()
        cli_bin = find_yxer_binary()
        return {
            "provider": s["provider"],
            "channel": s["channel"],
            "clientId": s["client_id"],
            "autoFallbackLocal": s["auto_fallback_local"],
            "visibility": s["visibility"],
            "aiDeclaration": s["ai_declaration"],
            "concurrency": s["concurrency"],
            "apiKeyConfigured": bool(self.api_key()),
            "cliInstalled": bool(cli_bin),
            "cliPath": cli_bin,
        }

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("发布配置格式不正确。")
        with self.app.transaction():
            current = self.raw_settings()
            api_key = values.get("apiKey")
            if api_key is None:
                api_key = values.get("yixiaoer_api_key")
            if api_key is not None:
                if not isinstance(api_key, str) or len(api_key) > 512 or any(c in api_key for c in "\r\n"):
                    raise ValueError("蚁小二 API Key 格式不正确。")
                if api_key.strip():
                    secrets_path = self._secrets_path()
                    secrets_data = self.read(secrets_path, {}) or {}
                    secrets_data[SECRET_SLOT] = api_key.strip()
                    self.app.pc.write(secrets_path, secrets_data, private=True)

            if "channel" in values or "publish_channel" in values:
                ch = str(values.get("channel") or values.get("publish_channel") or "local").strip()
                if ch not in ("local", "cloud"):
                    raise ValueError("发布通道只支持 local（本机客户端）或 cloud（云发布）。")
                current["channel"] = ch
            if "clientId" in values or "yixiaoer_client_id" in values:
                cid = str(values.get("clientId") if "clientId" in values else values.get("yixiaoer_client_id") or "").strip()
                if len(cid) > 120 or any(c in cid for c in "\r\n"):
                    raise ValueError("ClientID 格式不正确。")
                current["client_id"] = cid
            if "autoFallbackLocal" in values:
                current["auto_fallback_local"] = bool(values["autoFallbackLocal"])
            if "visibility" in values:
                vis = str(values["visibility"] or "private").strip()
                if vis not in ("private", "public"):
                    raise ValueError("发布可见性只支持 private 或 public。")
                current["visibility"] = vis
            if "aiDeclaration" in values:
                current["ai_declaration"] = bool(values["aiDeclaration"])
            if "concurrency" in values:
                try:
                    conc = int(values["concurrency"])
                except (TypeError, ValueError):
                    raise ValueError("并发路数必须为 1–4 之间的整数。") from None
                if not 1 <= conc <= 4:
                    raise ValueError("并发路数必须在 1–4 之间。")
                current["concurrency"] = conc

            cfg = self.app.pc.load()
            cfg.setdefault("settings", {})["publish"] = current
            self.app.pc.write(self.app.pc.directory() / "project.json", cfg)
        return self.public_settings()

    def make_provider(self, override: dict[str, Any] | None = None):
        s = self.raw_settings()
        ov = override or {}
        api_key = str(ov.get("apiKey") or "").strip() or self.api_key()
        channel = str(ov.get("channel") or s["channel"]).strip()
        client_id = str(ov.get("clientId") if ov.get("clientId") is not None else s["client_id"]).strip()
        auto_fb = bool(ov["autoFallbackLocal"]) if "autoFallbackLocal" in ov else s["auto_fallback_local"]
        if self.provider_factory is not None:
            return self.provider_factory(
                api_key=api_key,
                channel=channel,
                client_id=client_id,
                auto_fallback_local=auto_fb,
            )
        return YixiaoerProvider(
            api_key=api_key,
            channel=channel,
            client_id=client_id,
            auto_fallback_local=auto_fb,
        )

    # ---- Accounts cache & matching ----
    def cached_accounts(self) -> dict[str, Any]:
        raw = self.read(self.accounts_path, {}) or {}
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        return {
            "syncedAt": raw.get("syncedAt"),
            "version": raw.get("version") or "",
            "items": items,
            "summary": self._summarize_accounts(items),
        }

    def _summarize_accounts(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        by_platform: dict[str, dict[str, int]] = {}
        for key, _ in PLATFORMS:
            rows = [a for a in items if a.get("platform") == key]
            online = sum(1 for a in rows if a.get("online"))
            by_platform[key] = {"total": len(rows), "online": online, "expired": len(rows) - online}
        total = len(items)
        online_total = sum(v["online"] for v in by_platform.values())
        return {"total": total, "online": online_total, "expired": total - online_total, "byPlatform": by_platform}

    def test_and_sync(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        provider = self.make_provider(override)
        doc = provider.doctor()
        if not doc.get("authenticated"):
            return {"ok": False, "doctor": doc, "accounts": self.cached_accounts(), "suggestions": []}
        accounts = provider.list_accounts()
        payload = {
            "syncedAt": time.time(),
            "version": doc.get("version") or "",
            "items": accounts,
        }
        self.write(self.accounts_path, payload)
        suggestions = self.match_suggestions(accounts)
        return {
            "ok": True,
            "doctor": doc,
            "accounts": self.cached_accounts(),
            "suggestions": suggestions,
        }

    def match_suggestions(self, accounts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if accounts is None:
            accounts = self.cached_accounts()["items"]
        cfg = self.app.pc.load()
        out = []
        for pid, person in (cfg.get("people") or {}).items():
            manual = person.get("platform_accounts") or {}
            bindings = normalize_bindings(person.get("platform_bindings"))
            for pkey, pcn in PLATFORMS:
                if pkey in bindings:
                    continue
                target_id = str(manual.get(pkey) or "").strip()
                if not target_id:
                    continue
                hit = next(
                    (
                        a
                        for a in accounts
                        if a.get("platform") == pkey and target_id in (a.get("id"), a.get("authorId"), a.get("name"))
                    ),
                    None,
                )
                if hit:
                    out.append({
                        "personId": pid,
                        "personName": person.get("name") or pid,
                        "platform": pkey,
                        "platformName": pcn,
                        "manualId": target_id,
                        "account": hit,
                    })
        return out

    def _find_account(self, account_id: str, accounts: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        pool = accounts if accounts is not None else self.cached_accounts()["items"]
        return next((a for a in pool if str(a.get("id")) == str(account_id)), None)

    # ---- Accepted products resolution ----
    def _resolve_person_for_product(self, task: dict[str, Any] | None, archive_item: dict[str, Any] | None, archive_prod: dict[str, Any] | None, index: int) -> tuple[str, str]:
        cfg = self.app.pc.load()
        people = cfg.get("people") or {}
        person_id = ""
        person_name = ""
        if task:
            for list_key in ("videoSpecs", "videoPeople"):
                vp = task.get(list_key) or []
                if 0 <= index < len(vp) and isinstance(vp[index], dict):
                    person_id = str(vp[index].get("person") or "")
                    person_name = str(vp[index].get("personName") or "")
                    if person_id:
                        break
            if not person_id:
                person_id = str(task.get("person") or "")
                person_name = str(task.get("personName") or "")
        if not person_id and archive_prod:
            person_id = str(archive_prod.get("personId") or "")
            person_name = str(archive_prod.get("personName") or "")
        if not person_name and archive_item:
            person_name = str(archive_item.get("personName") or "")
        if not person_id and person_name:
            matches = [pid for pid, p in people.items() if p.get("name") == person_name]
            if len(matches) == 1:
                person_id = matches[0]
        if person_id and person_id in people and not person_name:
            person_name = str(people[person_id].get("name") or "")
        return person_id, person_name

    def resolve_accepted_product(self, task_id: str, index: int, version: str | None = None) -> dict[str, Any]:
        if task_id == "reference":
            raise ValueError("已认可样片受保护，不支持直接发布。")
        self.app.task_path(task_id)
        index = int(index)
        task = self.read(self.app.task_path(task_id), None)
        archive_manifest = self.read(self.app.archive.root / task_id / "manifest.json", None)

        # Enforce strict accepted-only rule: either archived (only happens post-acceptance) OR task.status == 'accepted'.
        if archive_manifest:
            ver = version or archive_manifest.get("currentVersions", {}).get(str(index), "original")
            prod = next(
                (p for p in archive_manifest.get("products", []) if p.get("index") == index and p.get("version") == ver),
                None,
            )
            if prod is None:
                raise ValueError("所选归档成品不存在或已被删除。")
            ws = self.app.archive.workspace_media(task_id, index, ver) if archive_manifest.get("workspace") else None
            if ws:
                folder = ws[0]
                video_path, cover_path, copy_path = folder / "final.mp4", folder / "cover.png", folder / "发布文案.txt"
            else:
                folder = self.app.archive.root / task_id / str(index) / ver
                video_path, cover_path, copy_path = folder / "final.mp4", folder / "cover.png", folder / "发布文案.txt"
            if not video_path.is_file() or not cover_path.is_file():
                raise ValueError("归档成品的视频或封面文件缺失，无法发布。")
            raw_copy = copy_path.read_text(encoding="utf-8") if copy_path.is_file() else ""
            title = str(prod.get("title") or archive_manifest.get("title") or "养老院探访")
            person_id, person_name = self._resolve_person_for_product(task, archive_manifest, prod, index)
            return {
                "productKey": product_key(task_id, index, ver),
                "taskId": task_id,
                "index": index,
                "version": ver,
                "title": title,
                "institutionId": archive_manifest.get("institutionId") or "",
                "institutionName": archive_manifest.get("institutionName") or "",
                "personId": person_id,
                "personName": person_name,
                "videoPath": str(video_path),
                "coverPath": str(cover_path),
                "copy": parse_publication_text(raw_copy, fallback_title=title),
                "coverUrl": f"/media/archive/{task_id}/{index}/{ver}/cover",
            }

        if not task:
            raise ValueError("所选成品不存在。")
        if task.get("reviewStatus") != "accepted" and task.get("status") != "accepted":
            raise ValueError("只有已验收的成品才能发起发布，请先在制作任务中完成验收。")
        from product_archive import product_deletion
        if product_deletion(task, index):
            raise ValueError("该条成品已删除或正在删除中，无法发布。")

        ver = version or self.read(self.app.edits.root / task_id / str(index) / "current.json", {}).get("version", "original")
        from workspace import delivery_folder
        d_folder = delivery_folder(task, index, ver)
        raw_copy = ""
        title = str(task.get("title") or "养老院探访")
        if d_folder is not None and (d_folder / "final.mp4").is_file() and (d_folder / "cover.png").is_file():
            video_path, cover_path, copy_path = d_folder / "final.mp4", d_folder / "cover.png", d_folder / "发布文案.txt"
        else:
            ws = self.app.edits.workspace_media(task_id, index, ver)
            if ws is not None:
                folder, title = ws
                video_path, cover_path, copy_path = folder / "final.mp4", folder / "cover.png", folder / "发布文案.txt"
            else:
                video_path = Path(self.app.edits.media(task_id, index, "video", ver))
                cover_path = Path(self.app.edits.media(task_id, index, "cover", ver))
                copy_path = video_path.parent / "发布文案.txt"
                pub = self.read(video_path.parent / "plan.json", {}).get("publication") or {}
                title = str(pub.get("title") or task.get("title") or "养老院探访")
                if not copy_path.is_file() and pub:
                    from publish_assets import publication_text
                    raw_copy = publication_text(pub)
        if not video_path.is_file() or not cover_path.is_file():
            raise ValueError("成品视频或封面文件缺失，无法发布。")
        if copy_path.is_file():
            raw_copy = copy_path.read_text(encoding="utf-8")
        parsed_copy = parse_publication_text(raw_copy, fallback_title=title)
        title = parsed_copy["title"] or title
        person_id, person_name = self._resolve_person_for_product(task, None, None, index)
        return {
            "productKey": product_key(task_id, index, ver),
            "taskId": task_id,
            "index": index,
            "version": ver,
            "title": title,
            "institutionId": task.get("institutionId") or "",
            "institutionName": task.get("institutionName") or "",
            "personId": person_id,
            "personName": person_name,
            "videoPath": str(video_path),
            "coverPath": str(cover_path),
            "copy": parsed_copy,
            "coverUrl": f"/media/video/{task_id}/{index}/cover?version={ver}",
        }

    def prepare_publish(self, selections: list[dict[str, Any]], *, refresh_accounts: bool = False) -> dict[str, Any]:
        if not isinstance(selections, list) or not selections:
            raise ValueError("请至少勾选一条已验收成品。")
        if len(selections) > 50:
            raise ValueError("单次最多勾选 50 条成品。")
        if refresh_accounts and self.api_key():
            try:
                self.test_and_sync()
            except Exception:
                pass
        accounts_cache = self.cached_accounts()
        accounts = accounts_cache["items"]
        cfg = self.app.pc.load()
        people = cfg.get("people") or {}
        statuses = self.product_statuses()
        settings = self.public_settings()

        prepared = []
        for sel in selections:
            task_id = str(sel.get("taskId") or "").strip()
            index = int(sel.get("index", 0))
            ver = sel.get("version") or None
            try:
                prod = self.resolve_accepted_product(task_id, index, ver)
            except ValueError as exc:
                prepared.append({
                    "taskId": task_id,
                    "index": index,
                    "version": ver or "original",
                    "eligible": False,
                    "blockedReason": str(exc),
                    "platforms": [],
                })
                continue

            person = people.get(prod["personId"]) if prod["personId"] else None
            bindings = normalize_bindings((person or {}).get("platform_bindings"))
            manual = (person or {}).get("platform_accounts") or {}
            existing_map = (statuses.get(prod["productKey"]) or {}).get("platforms") or {}

            platform_rows = []
            for pkey, pcn in PLATFORMS:
                binding = bindings.get(pkey)
                manual_id = str(manual.get(pkey) or "").strip()
                live = self._find_account(binding["accountId"], accounts) if binding else None
                if not binding and manual_id:
                    # Fallback lookup by authorId or id in cached accounts.
                    live = next((a for a in accounts if a.get("platform") == pkey and manual_id in (a.get("id"), a.get("authorId"))), None)
                account_id = (binding or {}).get("accountId") or (live or {}).get("id") or ""
                account_name = (live or {}).get("name") or (binding or {}).get("accountName") or manual_id
                online = bool(live.get("online")) if live else None
                existing = existing_map.get(pkey)
                already_published = bool(existing and existing.get("status") == "published")
                in_progress = bool(existing and existing.get("status") in ACTIVE_ITEM_STATUSES)

                reason = ""
                if not person:
                    reason = "无法识别出镜数字人，请检查人物配置"
                elif not account_id:
                    reason = "该数字人尚未绑定蚁小二账号"
                elif live is None and accounts:
                    reason = "绑定的账号不在当前蚁小二列表中，请重新同步并绑定"
                elif online is False:
                    reason = "账号登录已过期，请先到蚁小二扫码续期"
                elif in_progress:
                    reason = "该平台已有发布任务进行中"

                platform_rows.append({
                    "platform": pkey,
                    "platformName": pcn,
                    "accountId": account_id,
                    "accountName": account_name,
                    "online": online,
                    "existingStatus": existing.get("status") if existing else None,
                    "alreadyPublished": already_published,
                    "canSelect": not reason,
                    "defaultChecked": (not reason) and (not already_published),
                    "blockedReason": reason,
                })

            prepared.append({
                "productKey": prod["productKey"],
                "taskId": prod["taskId"],
                "index": prod["index"],
                "version": prod["version"],
                "title": prod["copy"]["title"] or prod["title"],
                "description": prod["copy"]["fullDescription"],
                "institutionName": prod["institutionName"],
                "personId": prod["personId"],
                "personName": prod["personName"],
                "coverUrl": prod["coverUrl"],
                "eligible": True,
                "blockedReason": "",
                "platforms": platform_rows,
            })

        return {
            "settings": settings,
            "accountsSyncedAt": accounts_cache["syncedAt"],
            "items": prepared,
        }

    # ---- Batch persistence & queue ----
    def _batch_path(self, batch_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", batch_id):
            raise ValueError("发布批次不存在。")
        return self.batches_dir / f"{batch_id}.json"

    def batches(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.batches_dir.glob("*.json"):
            data = self.read(path, None)
            if isinstance(data, dict) and data.get("id"):
                rows.append(data)
        return sorted(rows, key=lambda b: b.get("created", 0), reverse=True)

    def _save_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        batch["updated"] = time.time()
        batch["summary"] = self._batch_summary(batch)
        self.write(self._batch_path(batch["id"]), batch)
        if hasattr(self.app, "store"):
            self.app.store.sync_publish_batch(batch)
        return batch

    def _batch_summary(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = batch.get("items") or []
        total = len(items)
        published = sum(1 for i in items if i.get("status") == "published")
        failed = sum(1 for i in items if i.get("status") == "failed")
        active = sum(1 for i in items if i.get("status") in ACTIVE_ITEM_STATUSES)
        if active:
            status = "running" if any(i.get("status") != "queued" for i in items) else "queued"
        elif total and published == total:
            status = "published"
        elif published and failed:
            status = "partial"
        elif failed:
            status = "failed"
        else:
            status = "published"
        return {"total": total, "published": published, "failed": failed, "active": active, "status": status}

    def _recover_batches(self) -> None:
        for batch in self.batches():
            changed = False
            for item in batch.get("items") or []:
                st = item.get("status")
                if st in ("checking", "uploading", "validating", "submitting"):
                    item["status"] = "failed"
                    item["message"] = "工作台已重启，该条发布中断，请点击重试。"
                    item["updated"] = time.time()
                    changed = True
                elif st == "polling" and not item.get("taskSetId"):
                    item["status"] = "failed"
                    item["message"] = "工作台已重启且未记录远端任务编号，请核实后重试。"
                    item["updated"] = time.time()
                    changed = True
            if changed:
                self._save_batch(batch)

    def active_count(self) -> int:
        return sum(1 for b in self.batches() for i in (b.get("items") or []) if i.get("status") in ACTIVE_ITEM_STATUSES)

    def has_blocking_active(self) -> bool:
        return any(
            i.get("status") in ("queued", "checking", "uploading", "validating", "submitting")
            for b in self.batches()
            for i in (b.get("items") or [])
        )

    def create_batch(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("发布参数格式不正确。")
        settings = self.raw_settings()
        if not self.api_key():
            raise ValueError("请先在「设置 → 多平台分发（蚁小二）」保存 API Key。")
        channel = str(values.get("channel") or settings["channel"]).strip()
        if channel not in ("local", "cloud"):
            raise ValueError("发布通道无效。")
        if channel == "local" and not settings["client_id"]:
            raise ValueError("当前为本机客户端发布模式，请先在设置中填写蚁小二 ClientID。")
        visibility = str(values.get("visibility") or settings["visibility"]).strip()
        if visibility not in ("private", "public"):
            raise ValueError("发布可见性只支持私密或公开。")
        ai_declaration = bool(values.get("aiDeclaration", settings["ai_declaration"]))

        entries = values.get("items")
        if not isinstance(entries, list) or not entries:
            raise ValueError("请至少选择一条要发布的成品和目标平台。")

        cfg = self.app.pc.load()
        people = cfg.get("people") or {}
        accounts = self.cached_accounts()["items"]
        existing_statuses = self.product_statuses()

        batch_id = uuid.uuid4().hex[:12]
        now = time.time()
        atomic_items: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for entry in entries:
            task_id = str(entry.get("taskId") or "").strip()
            index = int(entry.get("index", 0))
            ver = entry.get("version") or None
            prod = self.resolve_accepted_product(task_id, index, ver)
            platforms = entry.get("platforms")
            if not isinstance(platforms, list) or not platforms:
                continue
            title = str(entry.get("title") or prod["copy"]["title"] or prod["title"]).strip()
            description = str(entry.get("description") if entry.get("description") is not None else prod["copy"]["fullDescription"]).strip()
            person = people.get(prod["personId"]) if prod["personId"] else None
            bindings = normalize_bindings((person or {}).get("platform_bindings"))
            manual = (person or {}).get("platform_accounts") or {}

            for pkey in platforms:
                pkey = str(pkey).strip()
                if pkey not in PLATFORM_KEY_TO_CN:
                    raise ValueError(f"不支持的平台：{pkey}")
                pair = (prod["productKey"], pkey)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                existing = ((existing_statuses.get(prod["productKey"]) or {}).get("platforms") or {}).get(pkey)
                if existing and existing.get("status") in ACTIVE_ITEM_STATUSES:
                    raise ValueError(f"「{prod['title']}」在{PLATFORM_KEY_TO_CN[pkey]}已有发布任务进行中，请勿重复提交。")

                override_account_id = str((entry.get("accountIds") or {}).get(pkey) or "").strip()
                binding = bindings.get(pkey)
                manual_id = str(manual.get(pkey) or "").strip()
                live = None
                if override_account_id:
                    live = self._find_account(override_account_id, accounts)
                elif binding:
                    live = self._find_account(binding["accountId"], accounts)
                elif manual_id:
                    live = next((a for a in accounts if a.get("platform") == pkey and manual_id in (a.get("id"), a.get("authorId"))), None)

                account_id = override_account_id or (binding or {}).get("accountId") or (live or {}).get("id") or ""
                if not account_id:
                    raise ValueError(f"数字人「{prod['personName'] or '未命名'}」尚未绑定{PLATFORM_KEY_TO_CN[pkey]}的蚁小二账号。")
                if live is not None and not live.get("online"):
                    raise ValueError(f"{PLATFORM_KEY_TO_CN[pkey]}账号「{live.get('name') or account_id}」登录已过期，请先到蚁小二扫码。")

                account_name = (live or {}).get("name") or (binding or {}).get("accountName") or account_id
                atomic_items.append({
                    "id": uuid.uuid4().hex[:12],
                    "productKey": prod["productKey"],
                    "taskId": prod["taskId"],
                    "index": prod["index"],
                    "version": prod["version"],
                    "institutionName": prod["institutionName"],
                    "personId": prod["personId"],
                    "personName": prod["personName"],
                    "videoPath": prod["videoPath"],
                    "coverPath": prod["coverPath"],
                    "coverUrl": prod["coverUrl"],
                    "title": title,
                    "description": description,
                    "platform": pkey,
                    "platformName": PLATFORM_KEY_TO_CN[pkey],
                    "accountId": account_id,
                    "accountName": account_name,
                    "visibility": visibility,
                    "channel": channel,
                    "aiDeclaration": ai_declaration,
                    "status": "queued",
                    "step": "排队中",
                    "message": "已加入发布队列，等待执行",
                    "taskSetId": "",
                    "publishUrl": "",
                    "remoteStatus": None,
                    "attempts": 0,
                    "created": now,
                    "updated": now,
                    "publishedAt": None,
                    "nextPollAt": 0.0,
                    "pollStartedAt": 0.0,
                    "events": [{"time": now, "kind": "info", "message": f"已提交{PLATFORM_KEY_TO_CN[pkey]}发布任务（{'私密' if visibility == 'private' else '公开'} · {'本机客户端' if channel == 'local' else '云发布'}）"}],
                })

        if not atomic_items:
            raise ValueError("没有可提交的发布子项，请检查目标平台勾选情况。")

        batch = {
            "id": batch_id,
            "created": now,
            "updated": now,
            "channel": channel,
            "visibility": visibility,
            "aiDeclaration": ai_declaration,
            "createdBy": str(values.get("_actorId") or ""),
            "createdByName": str(values.get("_actorName") or ""),
            "uploadedVideos": {},
            "items": atomic_items,
        }
        with self.app.transaction():
            self._save_batch(batch)
        self.tick()
        return self.public_batch(batch)

    def retry_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        with self.app.transaction():
            batch = self.read(self._batch_path(batch_id), None)
            if not batch:
                raise ValueError("发布批次不存在。")
            item = next((i for i in batch.get("items") or [] if i["id"] == item_id), None)
            if not item:
                raise ValueError("发布子项不存在。")
            if item.get("status") in ACTIVE_ITEM_STATUSES:
                raise ValueError("该条正在发布中，无需重复重试。")
            if item.get("status") == "published":
                raise ValueError("该条已发布成功，无需重试。")
            now = time.time()
            item.update(
                status="queued",
                step="排队中",
                message="已重新加入发布队列",
                taskSetId="",
                remoteStatus=None,
                nextPollAt=0.0,
                pollStartedAt=0.0,
                updated=now,
            )
            item.setdefault("events", []).append({"time": now, "kind": "info", "message": "手动发起重试"})
            item["events"] = item["events"][-40:]
            self._save_batch(batch)
        self.tick()
        return self.public_batch(batch)

    def refresh_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        batch = self.read(self._batch_path(batch_id), None)
        if not batch:
            raise ValueError("发布批次不存在。")
        item = next((i for i in batch.get("items") or [] if i["id"] == item_id), None)
        if not item:
            raise ValueError("发布子项不存在。")
        if not item.get("taskSetId"):
            raise ValueError("该条尚未提交到蚁小二平台，暂无远端任务编号可查询。")
        provider = self.make_provider({"channel": item.get("channel")})
        res = provider.query_details(item["taskSetId"], account_id=item.get("accountId", ""))
        self._apply_poll_result(batch_id, item_id, res)
        return self.public_batch(self.read(self._batch_path(batch_id), batch))

    def delete_batch(self, batch_id: str) -> dict[str, Any]:
        with self.app.transaction():
            path = self._batch_path(batch_id)
            batch = self.read(path, None)
            if not batch:
                raise ValueError("发布批次不存在。")
            if any(i.get("status") in ACTIVE_ITEM_STATUSES for i in batch.get("items") or []):
                raise ValueError("该批次还有发布任务进行中，请完成后再删除记录。")
            path.unlink(missing_ok=True)
            if hasattr(self.app, "store"):
                self.app.store.delete_publish_batch(batch_id)
        return {"deleted": True, "id": batch_id}

    # ---- Worker dispatch & execution ----
    def tick(self) -> None:
        settings = self.raw_settings()
        max_workers = settings["concurrency"]
        now = time.time()
        to_launch: list[tuple[str, str, str]] = []
        with self._dispatch_lock:
            running_count = len(self._running_items)
            if hasattr(self.app, "store"):
                candidates = self.app.store.pending_publish_work(now)
            else:
                candidates = []
                for batch in self.batches():
                    for item in batch.get("items") or []:
                        st = item.get("status")
                        if st == "queued":
                            candidates.append((batch["id"], item["id"], "execute"))
                        elif st == "polling" and now >= float(item.get("nextPollAt") or 0):
                            candidates.append((batch["id"], item["id"], "poll"))
            for batch_id, item_id, mode in candidates:
                key = f"{batch_id}:{item_id}"
                if key in self._running_items:
                    continue
                if mode == "execute":
                    if running_count >= max_workers:
                        continue
                    self._running_items.add(key)
                    running_count += 1
                    to_launch.append((batch_id, item_id, "execute"))
                else:
                    self._running_items.add(key)
                    to_launch.append((batch_id, item_id, "poll"))
        for batch_id, item_id, mode in to_launch:
            target = self._run_item if mode == "execute" else self._poll_item
            threading.Thread(target=target, args=(batch_id, item_id), daemon=True).start()

    def _update_item(self, batch_id: str, item_id: str, *, event: str = "", event_kind: str = "info", **patch) -> dict[str, Any] | None:
        with self.app.transaction():
            path = self._batch_path(batch_id)
            batch = self.read(path, None)
            if not batch:
                return None
            item = next((i for i in batch.get("items") or [] if i["id"] == item_id), None)
            if not item:
                return None
            now = time.time()
            item.update(patch)
            item["updated"] = now
            if event:
                safe_msg = self.app.redact(str(event))[:600]
                item.setdefault("events", []).append({"time": now, "kind": event_kind, "message": safe_msg})
                item["events"] = item["events"][-40:]
            self._save_batch(batch)
            return copy.deepcopy(item)

    def _upload_lock(self, key: str) -> threading.Lock:
        with self._account_locks_guard:
            lock = self._upload_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._upload_locks[key] = lock
            return lock

    def _get_or_upload_video(self, batch_id: str, item: dict[str, Any], provider) -> dict[str, Any]:
        pkey = item["productKey"]
        with self._upload_lock(f"{batch_id}:{pkey}"):
            batch = self.read(self._batch_path(batch_id), {})
            cached = (batch.get("uploadedVideos") or {}).get(pkey)
            if isinstance(cached, dict) and cached.get("key"):
                return cached
            uploaded = provider.upload_file(Path(item["videoPath"]), platform_key=item["platform"], usage="video")
            with self.app.transaction():
                b = self.read(self._batch_path(batch_id), None)
                if b:
                    b.setdefault("uploadedVideos", {})[pkey] = uploaded
                    self._save_batch(b)
            return uploaded

    def _run_item(self, batch_id: str, item_id: str) -> None:
        run_key = f"{batch_id}:{item_id}"
        try:
            batch = self.read(self._batch_path(batch_id), None)
            if not batch:
                return
            item = next((i for i in batch.get("items") or [] if i["id"] == item_id), None)
            if not item or item.get("status") != "queued":
                return
            account_lock = self._account_lock(str(item.get("accountId") or item_id))
            with account_lock:
                provider = self.make_provider({"channel": item.get("channel")})
                self._update_item(
                    batch_id,
                    item_id,
                    status="checking",
                    step="校验账号",
                    message="正在核验蚁小二账号登录状态…",
                    attempts=int(item.get("attempts") or 0) + 1,
                    event="开始校验账号在线状态",
                )
                accounts = provider.list_accounts()
                self.write(self.accounts_path, {"syncedAt": time.time(), "version": self.cached_accounts().get("version", ""), "items": accounts})
                live = self._find_account(item["accountId"], accounts)
                if not live:
                    raise PublishError("在蚁小二中未找到已绑定的目标账号，请先重新同步并绑定。")
                if not live.get("online"):
                    raise PublishError(f"账号「{live.get('name') or item['accountName']}」登录已过期，请先到蚁小二扫码后重试。")

                self._update_item(
                    batch_id,
                    item_id,
                    status="uploading",
                    step="上传素材",
                    message="正在上传视频与封面到蚁小二…",
                    event="账号在线，开始上传视频与封面",
                )
                video_meta = self._get_or_upload_video(batch_id, item, provider)
                cover_meta = provider.upload_file(Path(item["coverPath"]), platform_key=item["platform"], usage="cover")

                self._update_item(
                    batch_id,
                    item_id,
                    status="validating",
                    step="参数校验",
                    message="正在校验平台字段并执行预演…",
                    event="素材上传完成，开始校验参数与预演",
                )
                payload = build_video_payload(
                    platform_key=item["platform"],
                    account_id=item["accountId"],
                    video_meta=video_meta,
                    cover_meta=cover_meta,
                    title=item["title"],
                    description=item["description"],
                    channel=item.get("channel", "local"),
                    visibility=item.get("visibility", "private"),
                    ai_declaration=bool(item.get("aiDeclaration", True)),
                )
                provider.validate_payload(item["platform"], payload)
                provider.dry_run_publish(item["platform"], payload)

                if self.jitter:
                    time.sleep(random.uniform(0.4, 1.2))

                self._update_item(
                    batch_id,
                    item_id,
                    status="submitting",
                    step="提交发布",
                    message="预演通过，正在向平台提交发布任务…",
                    event="参数校验与预演通过，正式提交发布",
                )
                submitted = provider.submit_publish(item["platform"], payload)
                task_set_id = str(submitted.get("taskSetId") or "").strip()
                if not task_set_id:
                    raise PublishError("提交成功但未返回 taskSetId，请在蚁小二发布记录中核实，避免重复发布。")

                now = time.time()
                self._update_item(
                    batch_id,
                    item_id,
                    status="polling",
                    step="等待平台确认",
                    message="已提交至蚁小二，正在等待平台返回最终发布结果…",
                    taskSetId=task_set_id,
                    pollStartedAt=now,
                    nextPollAt=now + 4.0,
                    event=f"任务已受理（taskSetId: {task_set_id}），等待平台确认",
                )
        except Exception as exc:
            msg = self.app.redact(str(exc))[:600]
            self._update_item(
                batch_id,
                item_id,
                status="failed",
                step="发布失败",
                message=msg,
                event=f"发布失败：{msg}",
                event_kind="error",
            )
        finally:
            with self._dispatch_lock:
                self._running_items.discard(run_key)

    def _apply_poll_result(self, batch_id: str, item_id: str, result: dict[str, Any]) -> None:
        state = result.get("state")
        msg = self.app.redact(str(result.get("message") or ""))[:600]
        now = time.time()
        if state == "published":
            self._update_item(
                batch_id,
                item_id,
                status="published",
                step="已发布",
                message=msg or "平台已确认发布成功",
                remoteStatus=result.get("rawStatus"),
                publishUrl=str(result.get("publishUrl") or ""),
                publishedAt=now,
                event=f"发布成功：{msg or '平台已确认'}",
            )
        elif state == "failed":
            self._update_item(
                batch_id,
                item_id,
                status="failed",
                step="发布失败",
                message=msg or "平台返回发布失败",
                remoteStatus=result.get("rawStatus"),
                event=f"平台发布失败：{msg or '请检查蚁小二发布记录'}",
                event_kind="error",
            )
        else:
            batch = self.read(self._batch_path(batch_id), {})
            item = next((i for i in batch.get("items") or [] if i["id"] == item_id), {})
            started = float(item.get("pollStartedAt") or item.get("updated") or now)
            if now - started > POLL_TIMEOUT_SECONDS:
                self._update_item(
                    batch_id,
                    item_id,
                    status="failed",
                    step="查询超时",
                    message="等待平台回传超时（超过 15 分钟），请点击「刷新状态」或在蚁小二客户端查看实际发布结果。",
                    remoteStatus=result.get("rawStatus"),
                    event="平台状态查询超时，已暂停自动轮询",
                    event_kind="error",
                )
            else:
                self._update_item(
                    batch_id,
                    item_id,
                    status="polling",
                    step="等待平台确认",
                    message=msg or "等待平台回传最终发布结果…",
                    remoteStatus=result.get("rawStatus"),
                    nextPollAt=now + POLL_INTERVAL_SECONDS,
                )

    def _poll_item(self, batch_id: str, item_id: str) -> None:
        run_key = f"{batch_id}:{item_id}"
        try:
            batch = self.read(self._batch_path(batch_id), None)
            if not batch:
                return
            item = next((i for i in batch.get("items") or [] if i["id"] == item_id), None)
            if not item or item.get("status") != "polling":
                return
            provider = self.make_provider({"channel": item.get("channel")})
            res = provider.query_details(item["taskSetId"], account_id=item.get("accountId", ""))
            self._apply_poll_result(batch_id, item_id, res)
        except Exception as exc:
            msg = self.app.redact(str(exc))[:300]
            self._update_item(
                batch_id,
                item_id,
                nextPollAt=time.time() + POLL_INTERVAL_SECONDS * 2,
                message=f"查询状态暂时失败（{msg}），稍后自动重试…",
            )
        finally:
            with self._dispatch_lock:
                self._running_items.discard(run_key)

    # ---- Public state & product status aggregation ----
    def public_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = []
        for i in batch.get("items") or []:
            row = {k: v for k, v in i.items() if k not in ("videoPath", "coverPath")}
            items.append(row)
        return {
            "id": batch["id"],
            "created": batch.get("created"),
            "updated": batch.get("updated"),
            "channel": batch.get("channel", "local"),
            "visibility": batch.get("visibility", "private"),
            "aiDeclaration": bool(batch.get("aiDeclaration", True)),
            "createdBy": batch.get("createdBy") or "",
            "createdByName": batch.get("createdByName") or "",
            "summary": self._batch_summary(batch),
            "items": items,
        }

    def product_statuses(self) -> dict[str, dict[str, Any]]:
        """Aggregate publish state per productKey across all batches (newest item per platform wins)."""
        by_product: dict[str, dict[str, dict[str, Any]]] = {}
        # Batches are newest first; keep the newest non-trivial record per (productKey, platform),
        # while preferring an already-published record over a later failed attempt unless currently active.
        for batch in self.batches():
            for item in batch.get("items") or []:
                pk = item.get("productKey")
                plat = item.get("platform")
                if not pk or not plat:
                    continue
                bucket = by_product.setdefault(pk, {})
                prev = bucket.get(plat)
                entry = {
                    "platform": plat,
                    "platformName": item.get("platformName") or PLATFORM_KEY_TO_CN.get(plat, plat),
                    "status": item.get("status"),
                    "step": item.get("step") or "",
                    "visibility": item.get("visibility") or "private",
                    "accountName": item.get("accountName") or "",
                    "publishedAt": item.get("publishedAt"),
                    "updated": item.get("updated"),
                    "batchId": batch["id"],
                    "itemId": item["id"],
                    "publishUrl": item.get("publishUrl") or "",
                    "message": item.get("message") or "",
                }
                if prev is None:
                    bucket[plat] = entry
                elif prev["status"] != "published" and entry["status"] == "published" and prev["status"] not in ACTIVE_ITEM_STATUSES:
                    bucket[plat] = entry

        out: dict[str, dict[str, Any]] = {}
        for pk, plat_map in by_product.items():
            vals = list(plat_map.values())
            published_n = sum(1 for v in vals if v["status"] == "published")
            active_n = sum(1 for v in vals if v["status"] in ACTIVE_ITEM_STATUSES)
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

    def public_state(self) -> dict[str, Any]:
        batches = [self.public_batch(b) for b in self.batches()]
        statuses = self.product_statuses()
        return {
            "settings": self.public_settings(),
            "accounts": self.cached_accounts(),
            "batches": batches[:80],
            "productStatuses": statuses,
            "activeCount": sum(b["summary"]["active"] for b in batches),
        }

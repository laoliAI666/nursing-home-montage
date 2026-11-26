"""Institution-owned, versioned local materials. Task inputs are immutable snapshots."""
from pathlib import Path
import hashlib
import re
import shutil
import time
import uuid


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Library:
    def __init__(self, app, read, write):
        self.app, self.read, self.write = app, read, write
        self.root = app.data / "library"
        app.ensure_dir(self.root)

    def path(self, ident):
        if not re.fullmatch(r"[a-f0-9]{12}", ident):
            raise ValueError("机构不存在")
        return self.root / ident / "institution.json"

    def get(self, ident):
        value = self.read(self.path(ident))
        if not value:
            raise ValueError("机构不存在")
        return value

    def records(self):
        return sorted((self.read(p) for p in self.root.glob("*/institution.json")), key=lambda x: x["updated"], reverse=True)

    def asset(self, ident, aid):
        value = self.get(ident)
        asset = next((a for a in value["assets"] if a["id"] == aid), None)
        if not asset:
            raise ValueError("素材不存在")
        return asset, self.path(ident).parent / "assets" / asset["stored"]

    def public(self, value, detail=False):
        current = [a for a in value["assets"] if a["current"]]
        enabled = [a for a in current if a["enabled"]]
        cover = next((a for a in enabled if a["id"] == value.get("coverAsset")), None) or next((a for a in enabled if a["kind"] == "image"), None) or next((a for a in enabled if a["kind"] == "video"), None)
        ident = value["id"]
        tasks = [t for t in self.app.tasks() if t.get("institutionId") == ident]
        from product_archive import product_deletion
        completed_count = 0
        for task in tasks:
            rows = self.app.queue.for_task(task['id'])
            indices = [row['index'] for row in rows if row['status'] == 'completed'] if rows else (
                range(task['count']) if task['status'] == 'completed' else ())
            completed_count += sum(not product_deletion(task, index) for index in indices)
        prep = getattr(self.app, "material_prep", None)
        prep_data = prep._load(ident) if prep else None
        result = {k: value.get(k) for k in ("id", "name", "address", "description", "created", "updated", "revision", "reference", "coverAsset")}
        result.update(counts={k: sum(a["kind"] == k for a in enabled) for k in ("video", "image", "text")},
                      fileCount=len(current), enabledCount=len(enabled), taskCount=len(tasks),
                      completedCount=completed_count,
                      thumbnail=f"/media/library/{ident}/{cover['id']}/thumb" if cover else None)
        if prep:
            result["materialPrep"] = prep.summary(value, prep_data)
        result['completedCount']+=sum(len(a['results']) for a in self.app.archive.public() if a.get('institutionId')==ident and not any(t['id']==a['id'] for t in tasks))
        if detail:
            result["assets"] = [{k: v for k, v in a.items() if k != "stored"} | {
                "url": f"/media/library/{ident}/{a['id']}/file",
                "thumbnail": f"/media/library/{ident}/{a['id']}/thumb" if a["kind"] != "text" else None,
                "textUrl": f"/api/library/{ident}/text/{a['id']}" if a["kind"] == "text" else None,
                **({"analysis": prep.asset_view(ident, a, prep_data)} if prep else {}),
            } for a in current]
            result["history"] = value.get("history", [])[-30:]
            result["sources"] = value.get("sources", [])
            result["tasks"] = [self.app.public_task(t) for t in tasks]
        return result

    def save(self, values, ident=None):
        with self.app.transaction():
            creating = ident is None
            value = self.get(ident) if ident else {"id": uuid.uuid4().hex[:12], "created": time.time(), "revision": 0, "assets": [], "history": [], "sources": []}
            name = str(values.get("name", value.get("name", ""))).strip()[:120]
            if not name:
                raise ValueError("请填写养老院名称")
            if creating and any(x["name"] == name and x.get("address", "") == str(values.get("address", "")).strip() for x in self.records()):
                raise ValueError("这家机构已经建档，请到资料库打开它并补充资料")
            if values.get("folder") and not self.app.scan(values["folder"])["files"]:
                raise ValueError("文件夹中没有可用的视频、照片或介绍文档")
            change = creating or any(str(values.get(k, value.get(k, ""))).strip() != value.get(k, "") for k in ("name", "address", "description"))
            for key, limit in (("name", 120), ("address", 500), ("description", 40000)):
                value[key] = str(values.get(key, value.get(key, ""))).strip()[:limit]
            if values.get("coverAsset"):
                asset = next((a for a in value["assets"] if a["id"] == values["coverAsset"] and a["current"] and a["kind"] in ("image", "video")), None)
                if not asset:
                    raise ValueError("请选择当前版本的照片或视频作为封面")
                change = change or value.get("coverAsset") != asset["id"]
                value["coverAsset"] = asset["id"]
            if change:
                self.commit(value, "建立机构资料" if creating else "更新机构名称、位置或介绍")
            if values.get("folder"):
                return self.add_source(value["id"], values["folder"])
            return self.public(value, True)

    def commit(self, value, message):
        value["revision"] += 1
        value["updated"] = time.time()
        value["history"].append({"time": value["updated"], "revision": value["revision"], "message": message})
        self.write(self.path(value["id"]), value)

    def add_source(self, ident, folder, record_source=True):
        source = self.app.scan(folder)
        if not source["files"]:
            raise ValueError("文件夹中没有可用的视频、照片或介绍文档")
        with self.app.transaction():
            value = self.get(ident)
            source_versions = value.setdefault("sourceVersions", {}).setdefault(source["folder"], {})
            assets_dir = self.path(ident).parent / "assets"
            assets_dir.mkdir(exist_ok=True)
            added = changed = skipped = 0
            for row in source["files"]:
                path = Path(source["folder"]) / row["name"]
                sha = fingerprint(path)
                old = next((a for a in value["assets"] if a["current"] and a["name"] == row["name"]), None)
                if source_versions.get(row["name"]) == sha:
                    skipped += 1
                    continue
                source_versions[row["name"]] = sha
                if old and old["sha256"] == sha:
                    skipped += 1
                    continue
                aid = uuid.uuid4().hex[:12]
                stored = aid + path.suffix.lower()
                shutil.copy2(path, assets_dir / stored)
                if old:
                    old["current"] = False
                    changed += 1
                else:
                    added += 1
                value["assets"].append({"id": aid, "name": row["name"], "kind": row["kind"], "bytes": row["bytes"], "sha256": sha, "stored": stored, "current": True, "enabled": True, "version": old["version"] + 1 if old else 1, "added": time.time()})
            staged = Path(source["folder"]).is_relative_to(self.app.data / "uploads")
            if record_source and not staged and source["folder"] not in value["sources"]:
                value["sources"].append(source["folder"])
            if added or changed:
                self.commit(value, f"新增 {added} 个素材，更新 {changed} 个文件版本")
            else:
                self.write(self.path(ident), value)
        prep = getattr(self.app, "material_prep", None)
        if prep and (added or changed or prep.needs_incremental(self.get(ident))):
            prep.start(ident, mode="incremental", auto=True)
        result = self.public(self.get(ident), True)
        result["importResult"] = {"added": added, "updated": changed, "unchanged": skipped}
        return result

    def sync(self, ident):
        value = self.get(ident)
        missing, changed = [], 0
        for folder in list(value.get("sources", [])):
            if not Path(folder).is_dir():
                missing.append(folder)
                continue
            result = self.add_source(ident, folder)
            changed += result["importResult"]["added"] + result["importResult"]["updated"]
        result = self.public(self.get(ident), True)
        result["syncResult"] = {"changed": changed, "missing": missing}
        return result

    def enable(self, ident, aid, enabled):
        changed_to_enabled = False
        with self.app.transaction():
            value = self.get(ident)
            asset = next((a for a in value["assets"] if a["id"] == aid and a["current"]), None)
            if not asset:
                raise ValueError("素材不存在或已被新版本替代")
            if asset["enabled"] != enabled:
                asset["enabled"] = enabled
                changed_to_enabled = bool(enabled)
                self.commit(value, ("纳入新制作：" if enabled else "暂不用于新制作：") + asset["name"])
        prep = getattr(self.app, "material_prep", None)
        if changed_to_enabled and prep and prep.needs_incremental(self.get(ident)):
            prep.start(ident, mode="incremental", auto=True)
        return self.public(self.get(ident), True)

    def snapshot(self, ident, task_id):
        with self.app.transaction():
            value = self.get(ident)
            selected = [a for a in value["assets"] if a["current"] and a["enabled"]]
            counts = {k: sum(a["kind"] == k for a in selected) for k in ("video", "image", "text")}
            if not counts["video"]:
                raise ValueError("这家机构还没有可用的实拍视频，请先补充素材")
            if not counts["text"] and not value["description"]:
                raise ValueError("请先补充这家机构的介绍文字或上传介绍文档")
            destination = self.app.task_path(task_id).parent / "source"
            destination.mkdir(parents=True, exist_ok=True)
            manifest = []
            for a in selected:
                src = self.path(ident).parent / "assets" / a["stored"]
                if not src.is_file() or fingerprint(src) != a["sha256"]:
                    raise ValueError("素材文件缺失或被修改，请重新导入：" + a["name"])
                dest = destination / a["name"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                from publish_assets import link_or_copy
                link_or_copy(src, dest)
                manifest.append({k: a[k] for k in ("id", "name", "sha256", "version")})
            introduction = "机构名称：" + value["name"] + ("\n机构位置：" + value["address"] if value["address"] else "") + ("\n用户确认的机构介绍：\n" + value["description"] if value["description"] else "")
            (destination / ("000_机构资料_" + ident + ".txt")).write_text(introduction, encoding="utf-8")
            return {"institutionId": ident, "institutionName": value["name"], "institutionRevision": value["revision"], "source": str(destination), "materialSnapshot": manifest, "counts": {**counts, "text": counts["text"] + 1}}

    def bootstrap(self):
        marker = self.app.data / "library-migration.json"
        if marker.exists():
            return
        selected = self.read(self.app.data / "selected-source.json", {}).get("folder")
        ident = None
        if selected and Path(selected).is_dir() and not self.records():
            result = self.save({"name": Path(selected).name, "folder": selected})
            ident = result["id"]
            baseline = self.read(self.app.project / "项目状态.json", {})
            value = self.get(ident)
            if value["name"] in baseline.get("project", "") and self.app.reference()["available"]:
                value["reference"] = True
                self.write(self.path(ident), value)
            for task in self.app.tasks():
                if Path(task["source"]).resolve() == Path(selected).resolve() and not task.get("institutionId"):
                    self.app.update(task["id"], institutionId=ident, institutionName=value["name"], institutionRevision=None)
        self.write(marker, {"version": 1, "institution": ident, "at": time.time()})

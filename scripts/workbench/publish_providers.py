"""Pluggable social publishing providers (default: Yixiaoer `yxer` CLI)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


PLATFORMS = (
    ("xiaohongshu", "小红书"),
    ("douyin", "抖音"),
    ("channels", "视频号"),
)
PLATFORM_KEY_TO_CN = dict(PLATFORMS)
PLATFORM_CN_TO_KEY = {cn: key for key, cn in PLATFORMS}
TITLE_LIMITS = {"xiaohongshu": 20, "douyin": 30, "channels": 80}


class PublishError(ValueError):
    def __init__(self, message: str, *, code: str = "", hint: str = "", details: Any = None):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = details


def parse_json_output(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {"data": parsed}
        except json.JSONDecodeError:
            pass
    return {"raw": text}


def find_yxer_binary(explicit: str = "") -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("yxer")
    if found:
        candidates.append(found)
    home = Path.home()
    candidates.extend([
        "/opt/homebrew/bin/yxer",
        "/usr/local/bin/yxer",
        str(home / ".npm-global/bin/yxer"),
        str(home / ".nvm/current/bin/yxer"),
        str(home / "AppData/Roaming/npm/yxer.cmd"),
    ])
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item))
    return ""


def probe_media_meta(path: Path) -> dict[str, Any]:
    path = Path(path)
    meta: dict[str, Any] = {"size": path.stat().st_size if path.is_file() else 0}
    ffprobe = shutil.which("ffprobe") or ("/opt/homebrew/bin/ffprobe" if Path("/opt/homebrew/bin/ffprobe").is_file() else "")
    if not ffprobe or not path.is_file():
        return meta
    try:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            info = json.loads(res.stdout or "{}")
            for stream in info.get("streams") or []:
                if stream.get("width") and stream.get("height"):
                    meta["width"] = int(stream["width"])
                    meta["height"] = int(stream["height"])
                    break
            dur = (info.get("format") or {}).get("duration")
            if dur not in (None, ""):
                meta["duration"] = round(float(dur), 2)
    except Exception:
        pass
    return meta


def normalize_resource_object(payload: dict[str, Any], local_path: Path, is_video: bool) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    candidate = None
    if isinstance(data, dict):
        for key in ("resource", "file", "video", "cover", "result", "item"):
            if isinstance(data.get(key), dict) and data[key].get("key"):
                candidate = dict(data[key])
                break
        if candidate is None and data.get("key"):
            candidate = dict(data)
        if candidate is None and isinstance(data.get("files"), list) and data["files"]:
            first = data["files"][0]
            if isinstance(first, dict) and first.get("key"):
                candidate = dict(first)
    if not candidate or not candidate.get("key"):
        raise PublishError("上传接口未返回有效的资源 key，请检查蚁小二 CLI 输出或网络状态。")
    probed = probe_media_meta(local_path)
    if not candidate.get("size") and probed.get("size"):
        candidate["size"] = probed["size"]
    if not candidate.get("width") and probed.get("width"):
        candidate["width"] = probed["width"]
    if not candidate.get("height") and probed.get("height"):
        candidate["height"] = probed["height"]
    if is_video and not candidate.get("duration") and probed.get("duration"):
        candidate["duration"] = probed["duration"]
    return candidate


def extract_task_set_id(payload: dict[str, Any]) -> str:
    def walk(obj: Any) -> str:
        if isinstance(obj, dict):
            for k in ("taskSetId", "task_set_id", "taskId", "id"):
                val = obj.get(k)
                if isinstance(val, (str, int)) and str(val).strip():
                    return str(val).strip()
            for k in ("data", "result", "response", "task"):
                if k in obj:
                    found = walk(obj[k])
                    if found:
                        return found
        return ""

    return walk(payload)


def parse_details_status(payload: dict[str, Any], account_id: str = "") -> dict[str, Any]:
    """Interpret `yxer query details <taskSetId> --json` into polling/published/failed."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    records: list[dict[str, Any]] = []

    def collect(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    records.append(item)
                    collect(item)
        elif isinstance(obj, dict):
            for key in ("tasks", "accountTasks", "publishTasks", "records", "list", "items", "details", "subTasks"):
                if key in obj:
                    collect(obj[key])

    collect(data)
    target = None
    if account_id:
        for row in records:
            aid = str(row.get("platformAccountId") or row.get("accountId") or row.get("account_id") or "")
            if aid == str(account_id):
                target = row
                break
    if target is None:
        target = records[0] if records else (data if isinstance(data, dict) else {})

    status_texts = []
    for k in ("statusText", "statusName", "publishStatusText", "state", "taskStatusText", "result", "publishResult", "statusDesc"):
        v = target.get(k)
        if isinstance(v, str) and v.strip():
            status_texts.append(v.strip())
    err_texts = []
    for k in ("failReason", "errorMsg", "errorMessage", "error", "reason", "msg", "message"):
        v = target.get(k)
        if isinstance(v, str) and v.strip():
            err_texts.append(v.strip())
    stage_status = str(target.get("stageStatus") or "").strip().lower()
    stage_name = str(target.get("stages") or "").strip().lower()
    publish_id = str(target.get("publishId") or target.get("documentId") or "").strip()
    upload_progress = target.get("uploadProgress")
    raw_status = next(
        (target.get(k) for k in ("stageStatus", "publishStatus", "taskStatus", "queryStatus", "status", "state") if target.get(k) is not None),
        None,
    )
    publish_url = next((str(target.get(k)).strip() for k in ("openUrl", "publishUrl", "url", "workUrl", "shareUrl", "link") if target.get(k)), "")

    combined = " ".join(status_texts).lower()
    err_combined = " ".join(err_texts)
    success_words = ("发布成功", "已发布", "成功", "published", "succeeded", "success", "草稿保存成功", "已存草稿", "draft_saved")
    fail_words = ("发布失败", "失败", "异常", "错误", "取消", "驳回", "过期", "失效", "failed", "fail", "error", "cancelled", "rejected")
    pending_words = ("发布中", "待发布", "排队", "处理中", "上传", "等待", "审核中", "pending", "running", "processing", "queued", "publishing")

    # 1. Explicit failure signals (including Yixiaoer stageStatus / errorMessage)
    if any(w in combined for w in fail_words) or stage_status in ("fail", "failed", "error", "cancel", "cancelled", "rejected") or stage_name in ("fail", "failed", "error"):
        return {
            "state": "failed",
            "message": err_combined or (status_texts[0] if status_texts else f"平台发布失败（{stage_name or stage_status}）"),
            "rawStatus": raw_status,
            "publishUrl": publish_url,
            "publishId": publish_id,
        }
    if target.get("failReason") or target.get("errorMsg") or target.get("errorMessage"):
        return {
            "state": "failed",
            "message": err_combined,
            "rawStatus": raw_status,
            "publishUrl": publish_url,
            "publishId": publish_id,
        }

    # 2. Yixiaoer v3.2+ stageStatus + stages / publishId structure
    terminal_stages = ("", "success", "push", "publish", "published", "done", "finish", "finished", "complete", "completed", "draft")
    if stage_status in ("success", "succeeded", "published", "done", "completed"):
        if stage_name in terminal_stages or publish_id or publish_url:
            id_note = f"（作品 ID: {publish_id}）" if publish_id else ""
            return {
                "state": "published",
                "message": f"平台已确认发布成功{id_note}",
                "rawStatus": f"{stage_name}:{stage_status}" if stage_name else stage_status,
                "publishUrl": publish_url,
                "publishId": publish_id,
            }
        return {
            "state": "polling",
            "message": f"已完成阶段「{stage_name}」，等待平台推送完成…",
            "rawStatus": f"{stage_name}:{stage_status}",
            "publishUrl": publish_url,
            "publishId": publish_id,
        }

    # 3. Text / generic status fallbacks
    if any(w in combined for w in success_words):
        return {
            "state": "published",
            "message": status_texts[0] if status_texts else "平台已确认发布成功",
            "rawStatus": raw_status,
            "publishUrl": publish_url,
            "publishId": publish_id,
        }
    if any(w in combined for w in pending_words) or stage_status in ("pending", "running", "processing", "waiting", "init", "uploading", "pushing"):
        prog = f"（上传进度 {upload_progress}%）" if upload_progress not in (None, "") else ""
        return {
            "state": "polling",
            "message": (status_texts[0] if status_texts else f"平台正在处理（{stage_name or stage_status or '处理中'}）") + prog,
            "rawStatus": raw_status,
            "publishUrl": publish_url,
            "publishId": publish_id,
        }

    s_raw = str(raw_status).strip().lower() if raw_status is not None else ""
    if s_raw in ("published", "success", "succeeded", "done", "completed"):
        return {"state": "published", "message": "平台已确认发布成功", "rawStatus": raw_status, "publishUrl": publish_url, "publishId": publish_id}
    if s_raw in ("failed", "fail", "error", "cancelled", "rejected"):
        return {"state": "failed", "message": err_combined or "平台返回发布失败", "rawStatus": raw_status, "publishUrl": publish_url, "publishId": publish_id}
    if publish_url or publish_id:
        id_note = f"（作品 ID: {publish_id}）" if publish_id else ""
        return {"state": "published", "message": f"平台已返回作品标识{id_note}", "rawStatus": raw_status, "publishUrl": publish_url, "publishId": publish_id}
    prog = f" · 上传 {upload_progress}%" if upload_progress not in (None, "") else ""
    return {
        "state": "polling",
        "message": (status_texts[0] if status_texts else f"等待平台回传最终结果（状态：{raw_status if raw_status is not None else '处理中'}）") + prog,
        "rawStatus": raw_status,
        "publishUrl": publish_url,
        "publishId": publish_id,
    }


def build_video_payload(
    *,
    platform_key: str,
    account_id: str,
    video_meta: dict[str, Any],
    cover_meta: dict[str, Any],
    title: str,
    description: str,
    channel: str = "local",
    visibility: str = "private",
    ai_declaration: bool = True,
) -> dict[str, Any]:
    if platform_key not in PLATFORM_KEY_TO_CN:
        raise PublishError(f"暂不支持的发布平台：{platform_key}")
    platform_cn = PLATFORM_KEY_TO_CN[platform_key]
    limit = TITLE_LIMITS[platform_key]
    clean_title = (title or "").strip()[:limit]
    clean_desc = (description or "").strip()[:1000]
    is_private = visibility != "public"

    form: dict[str, Any] = {
        "formType": "task",
        "title": clean_title,
        "description": clean_desc,
    }
    if platform_key == "xiaohongshu":
        form["visibleType"] = 1 if is_private else 0
        form["createType"] = 0
        if ai_declaration:
            form["declaration"] = 2
    elif platform_key == "douyin":
        form["visibleType"] = 1 if is_private else 0
        form["allow_save"] = 0
        if ai_declaration:
            form["declaration"] = 3
    elif platform_key == "channels":
        form["createType"] = 2
        form["pubType"] = 0 if is_private else 1
        if ai_declaration:
            form["declaration"] = 1

    cover_key = str(cover_meta["key"])
    return {
        "action": "publish",
        "publishType": "video",
        "platforms": [platform_cn],
        "publishChannel": channel if channel in ("cloud", "local") else "local",
        "coverKey": cover_key,
        "publishArgs": {
            "video": video_meta,
            "cover": cover_meta,
            "coverKey": cover_key,
            "accountForms": [
                {
                    "platformAccountId": str(account_id),
                    "platformName": platform_cn,
                    "contentPublishForm": form,
                }
            ],
        },
    }


class PublishProvider:
    name = "base"

    def doctor(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_accounts(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def upload_file(self, file_path: Path, *, platform_key: str = "", usage: str = "") -> dict[str, Any]:
        raise NotImplementedError

    def validate_payload(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def dry_run_publish(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def submit_publish(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def query_details(self, task_set_id: str, account_id: str = "") -> dict[str, Any]:
        raise NotImplementedError


class YixiaoerProvider(PublishProvider):
    name = "yixiaoer"

    def __init__(
        self,
        *,
        api_key: str = "",
        channel: str = "local",
        client_id: str = "",
        auto_fallback_local: bool = False,
        binary: str = "",
    ):
        self.api_key = (api_key or "").strip()
        self.channel = channel if channel in ("local", "cloud") else "local"
        self.client_id = (client_id or "").strip()
        self.auto_fallback_local = bool(auto_fallback_local)
        self.binary = find_yxer_binary(binary)

    def _require_binary(self) -> str:
        if not self.binary:
            raise PublishError(
                "本机尚未安装蚁小二 CLI（yxer）。请先在终端执行 npm install -g @yixiaoermail/cli 后重试。",
                code="YIXIAOER_ENV_ERR",
            )
        return self.binary

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        extra_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
        current_path = env.get("PATH", "")
        parts = [p for p in extra_paths if p not in current_path.split(os.pathsep)]
        if parts:
            env["PATH"] = os.pathsep.join(parts + ([current_path] if current_path else []))
        if self.api_key:
            env["YIXIAOER_API_KEY"] = self.api_key
        return env

    def _channel_flags(self) -> list[str]:
        flags = ["--publish-channel", self.channel]
        if self.channel == "local":
            if not self.client_id:
                raise PublishError("当前使用本机客户端发布模式，请先在设置中填写蚁小二 ClientID。", code="YIXIAOER_USAGE_ERR")
            flags.extend(["--client-id", self.client_id])
        elif self.auto_fallback_local and self.client_id:
            flags.extend(["--auto-fallback-local", "--client-id", self.client_id])
        return flags

    def _run(self, args: list[str], *, timeout: int = 120) -> dict[str, Any]:
        binary = self._require_binary()
        with tempfile.TemporaryDirectory(prefix="yxer-home-") as tmp_home:
            env = self._env()
            if self.api_key or self.client_id:
                cfg_dir = Path(tmp_home) / ".yxer"
                cfg_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                cfg_file = cfg_dir / "config.json"
                fd = os.open(cfg_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "apiKey": self.api_key,
                            "localPublishClientId": self.client_id,
                        },
                        fh,
                        ensure_ascii=False,
                        indent=2,
                    )
                env["HOME"] = tmp_home
                env["USERPROFILE"] = tmp_home
            try:
                result = subprocess.run(
                    [binary, *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise PublishError(f"调用蚁小二 CLI 超时（>{timeout} 秒），请检查网络或本机蚁小二客户端状态。", code="YIXIAOER_TIMEOUT") from exc
            except OSError as exc:
                raise PublishError(f"无法启动蚁小二 CLI：{exc}", code="YIXIAOER_ENV_ERR") from exc

        parsed = parse_json_output(result.stdout)
        if not parsed or ("ok" not in parsed and "error" not in parsed and result.stderr):
            err_parsed = parse_json_output(result.stderr)
            if err_parsed and ("ok" in err_parsed or "error" in err_parsed):
                parsed = err_parsed
        if result.returncode != 0 or parsed.get("ok") is False:
            err = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            message = err.get("message") or parsed.get("message") or (result.stderr or "").strip() or (result.stdout or "").strip() or "蚁小二 CLI 调用失败"
            hint = str(err.get("hint") or "").strip()
            code = str(err.get("code") or "")
            full_msg = f"{message}（{hint}）" if hint and hint not in message else message
            raise PublishError(full_msg, code=code, hint=hint, details=err or parsed)
        return parsed

    def doctor(self) -> dict[str, Any]:
        binary = self._require_binary()
        ver = self._run(["--version"], timeout=15)
        version = str(ver.get("version") or (ver.get("data") or {}).get("version") or "")
        if not self.api_key:
            return {"installed": True, "binary": binary, "version": version, "authenticated": False, "message": "已安装 yxer CLI，但尚未配置 API Key。"}
        doc = self._run(["doctor", "--json"], timeout=30)
        return {
            "installed": True,
            "binary": binary,
            "version": version or str(doc.get("version") or ""),
            "authenticated": True,
            "data": doc.get("data") or {},
            "message": "蚁小二 CLI 环境与密钥校验通过。",
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise PublishError("请先在设置中保存蚁小二 API Key。", code="YIXIAOER_AUTH_ERR")
        accounts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for platform_key, platform_cn in PLATFORMS:
            res = self._run(["accounts", "list", platform_cn, "--all", "--json"], timeout=60)
            data = res.get("data")
            rows = []
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                for key in ("list", "accounts", "items", "records", "data"):
                    if isinstance(data.get(key), list):
                        rows = data[key]
                        break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                aid = str(row.get("id") or row.get("platformAccountId") or "").strip()
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                status = row.get("status")
                try:
                    status_int = int(status)
                except (TypeError, ValueError):
                    status_int = 0
                accounts.append({
                    "id": aid,
                    "platform": platform_key,
                    "platformName": platform_cn,
                    "name": str(row.get("platformAccountName") or row.get("name") or aid).strip(),
                    "authorId": str(row.get("platformAuthorId") or "").strip(),
                    "avatar": str(row.get("platformAvatar") or "").strip(),
                    "status": status_int,
                    "online": status_int == 1,
                    "statusLabel": "在线" if status_int == 1 else "未登录" if status_int == 0 else "登录已过期",
                    "parentId": str(row.get("parentId") or "").strip(),
                    "proxyId": str(row.get("proxyId") or "").strip(),
                    "kuaidailiArea": str(row.get("kuaidailiArea") or "").strip(),
                    "isFreeze": bool(row.get("isFreeze")),
                })
        return accounts

    def upload_file(self, file_path: Path, *, platform_key: str = "", usage: str = "") -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise PublishError(f"待上传文件不存在：{path.name}")
        args = ["upload", "--file", str(path), "--json"]
        if usage == "video":
            args.append("--auto-meta")
        if platform_key in PLATFORM_KEY_TO_CN:
            args.extend(["--platform", PLATFORM_KEY_TO_CN[platform_key]])
        if usage:
            args.extend(["--usage", usage])
        res = self._run(args, timeout=300)
        return normalize_resource_object(res, path, is_video=(usage == "video" or path.suffix.lower() in (".mp4", ".mov", ".m4v")))

    def _with_payload_file(self, payload: dict[str, Any], fn):
        with tempfile.TemporaryDirectory(prefix="yxer-payload-") as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return fn(path)

    def validate_payload(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        platform_cn = PLATFORM_KEY_TO_CN[platform_key]
        flags = self._channel_flags()
        return self._with_payload_file(
            payload,
            lambda p: self._run(["validate", platform_cn, "video", str(p), *flags, "--json"], timeout=60),
        )

    def dry_run_publish(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        platform_cn = PLATFORM_KEY_TO_CN[platform_key]
        flags = self._channel_flags()
        return self._with_payload_file(
            payload,
            lambda p: self._run(["publish", "video", platform_cn, str(p), *flags, "--dry-run", "--json"], timeout=60),
        )

    def submit_publish(self, platform_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        platform_cn = PLATFORM_KEY_TO_CN[platform_key]
        flags = self._channel_flags()
        res = self._with_payload_file(
            payload,
            lambda p: self._run(["publish", "video", platform_cn, str(p), *flags, "--json"], timeout=120),
        )
        task_set_id = extract_task_set_id(res)
        return {"taskSetId": task_set_id, "raw": res}

    def query_details(self, task_set_id: str, account_id: str = "") -> dict[str, Any]:
        if not task_set_id:
            return {"state": "failed", "message": "缺少远端任务编号（taskSetId），无法查询发布结果。", "rawStatus": None, "publishUrl": ""}
        res = self._run(["query", "details", str(task_set_id), "--json"], timeout=45)
        parsed = parse_details_status(res, account_id=account_id)
        parsed["raw"] = res
        return parsed

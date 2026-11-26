"""Authentication, RBAC permissions, session management, rate limiting & audit logging.

Design principles:
- Zero third-party dependencies (Python stdlib hashlib/hmac/secrets/sqlite3).
- Standard SQL-92 tables stored in workbench.db (SQLite WAL), easily portable to MySQL/PostgreSQL.
- Roles & permissions are data-driven (roles + role_permissions) so custom roles can be enabled later
  without altering auth middleware or database schema.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from typing import Any


SESSION_COOKIE_NAME = "montage_auth_session"
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")

# ---------------------------------------------------------------------------
# Atomic Permission Catalog (grouped by workbench page / module)
# ---------------------------------------------------------------------------
PERMISSION_MODULES: list[dict[str, Any]] = [
    {
        "id": "library",
        "label": "机构资料库",
        "description": "养老机构档案、实拍素材导入、AI 素材整理与摘要管理",
        "permissions": [
            {"code": "library.read", "kind": "read", "label": "查看资料库与素材", "description": "查看机构列表、详情、素材预览与整理摘要"},
            {"code": "library.create", "kind": "create", "label": "新建机构与上传素材", "description": "创建机构档案、上传导入文件夹或实拍素材"},
            {"code": "library.update", "kind": "update", "label": "编辑资料与整理摘要", "description": "修改机构介绍、启停素材、触发 AI 整理与编辑摘要"},
            {"code": "library.delete", "kind": "delete", "label": "删除机构与素材", "description": "永久删除机构档案或单条素材文件"},
        ],
    },
    {
        "id": "tasks",
        "label": "制作任务",
        "description": "新建视频批次、排队控制、单条重做与成品验收",
        "permissions": [
            {"code": "tasks.read", "kind": "read", "label": "查看任务与进度", "description": "查看任务列表、实时进度、队列状态与事件日志"},
            {"code": "tasks.create", "kind": "create", "label": "新建制作与重做", "description": "提交视频制作批次、保存草稿、从成品发起单条重做"},
            {"code": "tasks.update", "kind": "update", "label": "启停重试与补答", "description": "启动、取消、重试队列任务及回复补充问题"},
            {"code": "tasks.accept", "kind": "action", "label": "验收成品", "description": "验收已完成任务、清理中间工程并解锁多平台发布"},
            {"code": "tasks.delete", "kind": "delete", "label": "删除任务记录", "description": "删除任务与工程目录（已归档成品仍保留）"},
        ],
    },
    {
        "id": "works",
        "label": "成品库",
        "description": "已生成视频预览、发布包下载、封面修改与成品清理",
        "permissions": [
            {"code": "works.read", "kind": "read", "label": "查看预览与下载", "description": "浏览成品库、播放成片、下载视频/封面/发布包"},
            {"code": "works.update", "kind": "update", "label": "修改封面与版本", "description": "编辑封面文字与样式、重新渲染首帧、恢复历史版本"},
            {"code": "works.delete", "kind": "delete", "label": "永久删除成品", "description": "永久删除单条成品的视频、封面与发布包"},
        ],
    },
    {
        "id": "publish",
        "label": "发布任务",
        "description": "多平台分发（小红书 / 抖音 / 视频号）队列管理与账号同步",
        "permissions": [
            {"code": "publish.read", "kind": "read", "label": "查看发布任务与状态", "description": "查看发布批次、各平台终态与成品发布状态"},
            {"code": "publish.create_private", "kind": "create", "label": "发起发布（私密/草稿）", "description": "将已验收成品以私密可见方式提交到多平台"},
            {"code": "publish.create_public", "kind": "action", "label": "发起发布（公开对外）", "description": "将已验收成品直接公开发布到社媒平台"},
            {"code": "publish.update", "kind": "update", "label": "重试刷新与同步账号", "description": "失败项重试、手动刷新终态、同步平台授权账号"},
            {"code": "publish.delete", "kind": "delete", "label": "删除历史发布记录", "description": "清理已结束的发布批次记录"},
        ],
    },
    {
        "id": "people",
        "label": "数字人",
        "description": "出镜人物形象、配音音色、片尾钩子与社媒账号绑定",
        "permissions": [
            {"code": "people.read", "kind": "read", "label": "查看数字人与试听", "description": "浏览数字人列表、形象资料与试听音色预览"},
            {"code": "people.create", "kind": "create", "label": "新建数字人", "description": "上传人物照片与录音创建新数字人"},
            {"code": "people.update", "kind": "update", "label": "编辑资料与绑定账号", "description": "修改名称、配乐、风格、片尾钩子与分发账号绑定"},
            {"code": "people.voice_clone", "kind": "action", "label": "发起音色复刻（付费）", "description": "上传录音并调用语音平台接口复刻或重训音色"},
            {"code": "people.delete", "kind": "delete", "label": "删除数字人", "description": "删除数字人配置与本地人物素材"},
        ],
    },
    {
        "id": "styles",
        "label": "风格库",
        "description": "30+ 套封面样式与视频样式浏览、数字人绑定与全局默认",
        "permissions": [
            {"code": "styles.read", "kind": "read", "label": "浏览风格库", "description": "查看和预览全部封面与视频样式模板"},
            {"code": "styles.bind", "kind": "update", "label": "绑定风格到数字人", "description": "批量将封面与视频风格绑定到指定数字人"},
            {"code": "styles.set_default", "kind": "action", "label": "设为工作台默认风格", "description": "修改工作台全局默认封面与视频样式"},
        ],
    },
    {
        "id": "music",
        "label": "配乐库",
        "description": "背景音乐试听、上传与管理",
        "permissions": [
            {"code": "music.read", "kind": "read", "label": "查看与试听配乐", "description": "浏览配乐列表并在线试听"},
            {"code": "music.create", "kind": "create", "label": "上传配乐", "description": "上传新的背景音乐音频文件"},
            {"code": "music.update", "kind": "update", "label": "编辑配乐与设默认", "description": "重命名、收起/恢复或设为默认配乐"},
            {"code": "music.delete", "kind": "delete", "label": "删除配乐", "description": "永久删除配乐文件与记录"},
        ],
    },
    {
        "id": "settings",
        "label": "工作台全局设置",
        "description": "大模型/语音/数字人/蚁小二服务密钥、超时与登录过期配置",
        "permissions": [
            {"code": "settings.read", "kind": "read", "label": "查看配置状态（脱敏）", "description": "查看各项服务是否已配置与连通状态（不含密钥明文）"},
            {"code": "settings.update", "kind": "update", "label": "修改全局设置与服务", "description": "调整模型、语音、数字人、分发配置、工作区与登录会话过期时间"},
            {"code": "settings.reveal_secret", "kind": "action", "label": "查看 API 密钥明文", "description": "经二次密码验证后明文查看已保存的密钥（仅超管锁定）", "superOnly": True},
        ],
    },
    {
        "id": "admin",
        "label": "账号与权限中心",
        "description": "团队成员账号、角色权限矩阵与操作审计日志",
        "permissions": [
            {"code": "users.manage_subordinate", "kind": "update", "label": "管理下级账号", "description": "查看、创建、停用、重置密码及删除下级角色（如运营）账号"},
            {"code": "users.manage_admin", "kind": "action", "label": "管理管理员账号", "description": "创建、修改、停用或删除管理员与超管账号（仅超管锁定）", "superOnly": True},
            {"code": "roles.manage", "kind": "action", "label": "配置角色权限矩阵", "description": "调整各角色的页面与操作权限配置（仅超管锁定）", "superOnly": True},
            {"code": "audit.read", "kind": "read", "label": "查看操作审计日志", "description": "查阅团队成员的关键操作与登录追溯记录"},
        ],
    },
]

ALL_PERMISSION_META: dict[str, dict[str, Any]] = {}
for _mod in PERMISSION_MODULES:
    for _perm in _mod["permissions"]:
        ALL_PERMISSION_META[_perm["code"]] = {
            **_perm,
            "module": _mod["id"],
            "moduleLabel": _mod["label"],
            "superOnly": bool(_perm.get("superOnly", False)),
        }

ALL_PERMISSION_CODES: set[str] = set(ALL_PERMISSION_META.keys())
SUPER_ONLY_PERMISSIONS: set[str] = {c for c, m in ALL_PERMISSION_META.items() if m["superOnly"]}

ADMIN_DEFAULT_PERMISSIONS: set[str] = {
    "library.read", "library.create", "library.update", "library.delete",
    "tasks.read", "tasks.create", "tasks.update", "tasks.accept", "tasks.delete",
    "works.read", "works.update", "works.delete",
    "publish.read", "publish.create_private", "publish.create_public", "publish.update", "publish.delete",
    "people.read", "people.create", "people.update", "people.voice_clone", "people.delete",
    "styles.read", "styles.bind",
    "music.read", "music.create", "music.update", "music.delete",
    "settings.read",
    "users.manage_subordinate",
    "audit.read",
}

OPERATOR_DEFAULT_PERMISSIONS: set[str] = {
    "library.read", "library.create", "library.update",
    "tasks.read", "tasks.create", "tasks.update", "tasks.accept",
    "works.read", "works.update",
    "publish.read", "publish.create_private", "publish.create_public", "publish.update",
    "people.read",
    "styles.read",
    "music.read",
}

DEFAULT_ROLES: list[dict[str, Any]] = [
    {
        "id": "super_admin",
        "code": "super_admin",
        "name": "超级管理员",
        "description": "拥有工作台全部权限，可配置全局服务与各角色操作权限。",
        "rank": 100,
        "is_system": 1,
        "default_permissions": sorted(ALL_PERMISSION_CODES),
    },
    {
        "id": "admin",
        "code": "admin",
        "name": "管理员",
        "description": "仅次于超级管理员，负责日常业务全流程管理与运营账号维护，不可修改全局设置与管理员权限。",
        "rank": 50,
        "is_system": 1,
        "default_permissions": sorted(ADMIN_DEFAULT_PERMISSIONS),
    },
    {
        "id": "operator",
        "code": "operator",
        "name": "运营",
        "description": "负责机构建档与素材补充、视频制作、成品验收封面微调与多平台发布，默认禁止删除操作。",
        "rank": 10,
        "is_system": 1,
        "default_permissions": sorted(OPERATOR_DEFAULT_PERMISSIONS),
    },
]

DEFAULT_ROLE_MAP = {r["code"]: r for r in DEFAULT_ROLES}

DEFAULT_AUTH_SETTINGS: dict[str, Any] = {
    "sessionTtlDays": 7,
    "shortSessionHours": 12,
    "allowAutoLogin": True,
    "reauthWindowSeconds": 300,
    "maxFailedAttempts": 5,
    "lockoutMinutes": 10,
}


class AuthError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "auth_error"):
        super().__init__(message)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Password hashing & validation (stdlib only)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pwd_bytes = password.encode("utf-8")
    if hasattr(hashlib, "scrypt"):
        n, r, p = 16384, 8, 1
        dk = hashlib.scrypt(pwd_bytes, salt=salt, n=n, r=r, p=p, dklen=32)
        return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"
    iterations = 310_000
    dk = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt, iterations, dklen=32)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not password or not encoded:
        return False
    try:
        parts = encoded.split("$")
        pwd_bytes = password.encode("utf-8")
        if parts[0] == "scrypt" and len(parts) == 6 and hasattr(hashlib, "scrypt"):
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt = bytes.fromhex(parts[4])
            expected = bytes.fromhex(parts[5])
            actual = hashlib.scrypt(pwd_bytes, salt=salt, n=n, r=r, p=p, dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = bytes.fromhex(parts[3])
            actual = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt, iterations, dklen=len(expected))
            return hmac.compare_digest(actual, expected)
    except Exception:
        return False
    return False


def validate_username(raw: Any) -> str:
    username = str(raw or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AuthError("用户名须为 3–32 位，以字母开头，仅含小写字母、数字、下划线或横线。", status=400, code="invalid_username")
    return username


def validate_password(raw: Any) -> str:
    pwd = str(raw or "")
    if len(pwd) < 8 or len(pwd) > 128:
        raise AuthError("密码长度须为 8–128 个字符。", status=400, code="invalid_password")
    if not (re.search(r"[A-Za-z]", pwd) and re.search(r"\d", pwd)):
        raise AuthError("密码必须同时包含字母和数字。", status=400, code="weak_password")
    return pwd


def validate_display_name(raw: Any, fallback: str = "") -> str:
    name = re.sub(r"[\r\n]+", " ", str(raw or "").strip())[:40]
    return name or fallback


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Sliding-window login rate limiter
# ---------------------------------------------------------------------------
class LoginRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._user_failures: dict[str, list[float]] = {}
        self._ip_failures: dict[str, list[float]] = {}

    def check(self, username: str, ip: str, *, max_user: int = 5, max_ip: int = 20, window: float = 600.0) -> None:
        now = time.time()
        with self._lock:
            u_times = [t for t in self._user_failures.get(username, []) if now - t < window]
            self._user_failures[username] = u_times
            if len(u_times) >= max_user:
                wait_min = max(1, int((window - (now - u_times[0])) / 60) + 1)
                raise AuthError(f"该账号连续登录失败过多，请约 {wait_min} 分钟后再试。", status=429, code="rate_limited")
            if ip:
                ip_times = [t for t in self._ip_failures.get(ip, []) if now - t < window]
                self._ip_failures[ip] = ip_times
                if len(ip_times) >= max_ip:
                    raise AuthError("当前网络登录尝试过于频繁，请稍后再试。", status=429, code="rate_limited")

    def record_failure(self, username: str, ip: str) -> None:
        now = time.time()
        with self._lock:
            self._user_failures.setdefault(username, []).append(now)
            if ip:
                self._ip_failures.setdefault(ip, []).append(now)

    def clear_user(self, username: str) -> None:
        with self._lock:
            self._user_failures.pop(username, None)


# ---------------------------------------------------------------------------
# Authenticated request context
# ---------------------------------------------------------------------------
class AuthContext:
    def __init__(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        role_code: str,
        role_name: str,
        role_rank: int,
        must_change_password: bool,
        permissions: set[str],
        session_hash: str = "",
        reauth_at: float | None = None,
        expires_at: float = 0.0,
        refreshed_max_age: int | None = None,
        is_internal: bool = False,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.display_name = display_name
        self.role_code = role_code
        self.role_name = role_name
        self.role_rank = role_rank
        self.must_change_password = must_change_password
        self.permissions = permissions
        self.session_hash = session_hash
        self.reauth_at = reauth_at
        self.expires_at = expires_at
        self.refreshed_max_age = refreshed_max_age
        self.is_internal = is_internal

    @property
    def is_super_admin(self) -> bool:
        return self.role_code == "super_admin"

    def can(self, perm_code: str) -> bool:
        if self.is_super_admin:
            return True
        return perm_code in self.permissions

    def can_any(self, *perm_codes: str) -> bool:
        if self.is_super_admin:
            return True
        return any(code in self.permissions for code in perm_codes)

    def public_user(self, reauth_window: int = 300) -> dict[str, Any]:
        reauth_remaining = 0
        if self.reauth_at:
            reauth_remaining = max(0, int(reauth_window - (time.time() - self.reauth_at)))
        return {
            "id": self.user_id,
            "username": self.username,
            "displayName": self.display_name,
            "role": self.role_code,
            "roleName": self.role_name,
            "rank": self.role_rank,
            "isSuperAdmin": self.is_super_admin,
            "mustChangePassword": self.must_change_password,
            "permissions": sorted(ALL_PERMISSION_CODES if self.is_super_admin else self.permissions),
            "reauthRemainingSeconds": reauth_remaining,
            "sessionExpiresAt": self.expires_at,
        }


# ---------------------------------------------------------------------------
# Main Auth & RBAC Service (backed by WorkbenchStore's SQLite WAL DB)
# ---------------------------------------------------------------------------
class AuthService:
    def __init__(self, app, store) -> None:
        self.app = app
        self.store = store
        self.limiter = LoginRateLimiter()
        self.init_defaults()

    def _conn(self):
        return self.store._conn()

    @property
    def _lock(self):
        return self.store._write_lock

    def init_defaults(self) -> None:
        now = time.time()
        with self._lock:
            conn = self._conn()
            for r in DEFAULT_ROLES:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO roles (id, code, name, description, rank, is_system, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (r["id"], r["code"], r["name"], r["description"], r["rank"], r["is_system"], now),
                )
            # Ensure super_admin always has full permission catalog synced
            conn.execute("DELETE FROM role_permissions WHERE role_id = 'super_admin'")
            conn.executemany(
                "INSERT OR IGNORE INTO role_permissions (role_id, perm_code) VALUES ('super_admin', ?)",
                [(code,) for code in sorted(ALL_PERMISSION_CODES)],
            )
            # Seed default permissions for admin/operator only once per role
            for r in DEFAULT_ROLES:
                if r["code"] == "super_admin":
                    continue
                seed_key = f"role_seeded_{r['code']}"
                row = conn.execute("SELECT value FROM meta WHERE key = ?", (seed_key,)).fetchone()
                if not row:
                    conn.executemany(
                        "INSERT OR IGNORE INTO role_permissions (role_id, perm_code) VALUES (?, ?)",
                        [(r["id"], code) for code in r["default_permissions"]],
                    )
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (seed_key, "1"))
            # Ensure auth_settings exists
            row = conn.execute("SELECT value FROM meta WHERE key = 'auth_settings'").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('auth_settings', ?)",
                    (json.dumps(DEFAULT_AUTH_SETTINGS, ensure_ascii=False),),
                )
            conn.commit()

    # ---- Auth Settings (session TTL / auto-login / security) ----
    def get_settings(self) -> dict[str, Any]:
        row = self._conn().execute("SELECT value FROM meta WHERE key = 'auth_settings'").fetchone()
        raw = {}
        if row:
            try:
                raw = json.loads(row[0])
            except Exception:
                raw = {}
        out = dict(DEFAULT_AUTH_SETTINGS)
        out.update({k: raw[k] for k in DEFAULT_AUTH_SETTINGS if k in raw})
        return out

    def save_settings(self, values: dict[str, Any], actor: AuthContext | None = None, ip: str = "") -> dict[str, Any]:
        if not isinstance(values, dict):
            raise AuthError("安全设置格式不正确。", status=400)
        cur = self.get_settings()
        if "sessionTtlDays" in values:
            try:
                days = int(values["sessionTtlDays"])
            except (TypeError, ValueError):
                raise AuthError("自动登录过期天数须为整数。", status=400)
            if not 1 <= days <= 365:
                raise AuthError("自动登录过期时间请设置在 1 至 365 天之间。", status=400)
            cur["sessionTtlDays"] = days
        if "shortSessionHours" in values:
            try:
                hours = int(values["shortSessionHours"])
            except (TypeError, ValueError):
                raise AuthError("短期会话时长须为整数。", status=400)
            if not 1 <= hours <= 72:
                raise AuthError("短期会话时长请设置在 1 至 72 小时之间。", status=400)
            cur["shortSessionHours"] = hours
        if "allowAutoLogin" in values:
            cur["allowAutoLogin"] = bool(values["allowAutoLogin"])
        if "reauthWindowSeconds" in values:
            try:
                sec = int(values["reauthWindowSeconds"])
            except (TypeError, ValueError):
                raise AuthError("二次验证有效时间须为整数。", status=400)
            if not 60 <= sec <= 1800:
                raise AuthError("密钥二次验证有效时间请设置在 60 至 1800 秒之间。", status=400)
            cur["reauthWindowSeconds"] = sec
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('auth_settings', ?)",
                (json.dumps(cur, ensure_ascii=False),),
            )
            conn.commit()
        if actor:
            self.log_audit(
                actor,
                action="auth.settings_update",
                resource_type="settings",
                summary=f"更新登录与会话设置（自动登录：{'开启' if cur['allowAutoLogin'] else '关闭'}，有效期 {cur['sessionTtlDays']} 天）",
                detail=cur,
                ip=ip,
            )
        return cur

    # ---- Initialization & Login ----
    def has_users(self) -> bool:
        row = self._conn().execute("SELECT COUNT(*) FROM users").fetchone()
        return bool(row and row[0] > 0)

    def setup_superadmin(
        self,
        username: str,
        password: str,
        display_name: str = "",
        *,
        ip: str = "",
        user_agent: str = "",
        remember: bool = True,
    ) -> tuple[str, int, AuthContext]:
        uname = validate_username(username)
        pwd = validate_password(password)
        dname = validate_display_name(display_name, fallback="超级管理员")
        now = time.time()
        uid = "u_" + uuid.uuid4().hex[:12]
        pwd_hash = hash_password(pwd)
        with self._lock:
            conn = self._conn()
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count > 0:
                raise AuthError("工作台已完成初始化，请直接登录。", status=409, code="already_initialized")
            conn.execute(
                """
                INSERT INTO users (id, username, display_name, password_hash, role_id, status,
                    must_change_password, last_login_at, last_login_ip, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'super_admin', 'active', 0, ?, ?, ?, ?, ?)
                """,
                (uid, uname, dname, pwd_hash, now, ip, uid, now, now),
            )
            conn.commit()
        token, max_age, ctx = self._create_session(uid, ip=ip, user_agent=user_agent, remember=remember)
        self.log_audit(ctx, action="auth.setup", resource_type="user", resource_id=uid, summary=f"初始化首个超级管理员账号「{uname}」", ip=ip)
        return token, max_age, ctx

    def login(
        self,
        username: str,
        password: str,
        *,
        ip: str = "",
        user_agent: str = "",
        remember: bool = True,
    ) -> tuple[str, int, AuthContext]:
        uname = str(username or "").strip().lower()
        pwd = str(password or "")
        if not uname or not pwd:
            raise AuthError("请输入用户名和密码。", status=400, code="missing_credentials")
        settings = self.get_settings()
        self.limiter.check(
            uname,
            ip,
            max_user=int(settings.get("maxFailedAttempts", 5)),
            window=float(settings.get("lockoutMinutes", 10)) * 60.0,
        )
        row = self._conn().execute(
            """
            SELECT u.id, u.username, u.display_name, u.password_hash, u.status,
                   u.must_change_password, r.code AS role_code, r.name AS role_name, r.rank AS role_rank
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (uname,),
        ).fetchone()
        if not row or not verify_password(pwd, row["password_hash"]):
            self.limiter.record_failure(uname, ip)
            self._log_raw_audit(
                username=uname,
                action="auth.login_failed",
                resource_type="auth",
                summary=f"账号「{uname}」登录失败（用户名或密码错误）",
                ip=ip,
            )
            raise AuthError("用户名或密码错误。", status=401, code="invalid_credentials")
        if row["status"] != "active":
            self._log_raw_audit(
                user_id=row["id"],
                username=row["username"],
                display_name=row["display_name"],
                role_code=row["role_code"],
                action="auth.login_blocked",
                resource_type="auth",
                summary=f"已停用账号「{row['username']}」尝试登录被拦截",
                ip=ip,
            )
            raise AuthError("该账号已停用，请联系管理员。", status=403, code="account_disabled")
        self.limiter.clear_user(uname)
        use_remember = bool(remember) and bool(settings.get("allowAutoLogin", True))
        token, max_age, ctx = self._create_session(row["id"], ip=ip, user_agent=user_agent, remember=use_remember)
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE users SET last_login_at = ?, last_login_ip = ? WHERE id = ?",
                (time.time(), ip, row["id"]),
            )
            conn.commit()
        self.log_audit(
            ctx,
            action="auth.login",
            resource_type="auth",
            summary=f"登录工作台（{'自动登录 ' + str(settings['sessionTtlDays']) + ' 天' if use_remember else '本次会话'}）",
            ip=ip,
        )
        return token, max_age, ctx

    def _create_session(self, user_id: str, *, ip: str, user_agent: str, remember: bool) -> tuple[str, int, AuthContext]:
        settings = self.get_settings()
        now = time.time()
        if remember and settings.get("allowAutoLogin", True):
            ttl = int(settings.get("sessionTtlDays", 7)) * 86400
        else:
            ttl = int(settings.get("shortSessionHours", 12)) * 3600
        raw_token = secrets.token_urlsafe(32)
        thash = hash_session_token(raw_token)
        expires_at = now + ttl
        ua = str(user_agent or "")[:240]
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            # Keep at most 10 active sessions per user
            stale = conn.execute(
                "SELECT token_hash FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC LIMIT -1 OFFSET 9",
                (user_id,),
            ).fetchall()
            if stale:
                conn.executemany("DELETE FROM sessions WHERE token_hash = ?", [(r[0],) for r in stale])
            conn.execute(
                """
                INSERT INTO sessions (token_hash, user_id, ip, user_agent, reauth_at, created_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (thash, user_id, ip, ua, now, now, expires_at),
            )
            conn.commit()
        ctx = self.authenticate(raw_token)
        if not ctx:
            raise AuthError("会话创建失败，请重试。", status=500)
        return raw_token, ttl, ctx

    def authenticate(self, raw_token: str) -> AuthContext | None:
        if not raw_token or len(raw_token) < 16:
            return None
        thash = hash_session_token(raw_token)
        now = time.time()
        conn = self._conn()
        row = conn.execute(
            """
            SELECT s.token_hash, s.user_id, s.reauth_at, s.created_at, s.last_seen_at, s.expires_at,
                   u.username, u.display_name, u.status, u.must_change_password,
                   r.id AS role_id, r.code AS role_code, r.name AS role_name, r.rank AS role_rank
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN roles r ON r.id = u.role_id
            WHERE s.token_hash = ?
            """,
            (thash,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= now or row["status"] != "active":
            with self._lock:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (thash,))
                conn.commit()
            return None
        perms = (
            set(ALL_PERMISSION_CODES)
            if row["role_code"] == "super_admin"
            else {
                r[0]
                for r in conn.execute(
                    "SELECT perm_code FROM role_permissions WHERE role_id = ?", (row["role_id"],)
                ).fetchall()
            }
        )
        # Sliding renewal when > 25% of configured TTL has elapsed (at most every 10 min write)
        settings = self.get_settings()
        ttl = int(settings.get("sessionTtlDays", 7)) * 86400 if settings.get("allowAutoLogin", True) else int(settings.get("shortSessionHours", 12)) * 3600
        refreshed_max_age: int | None = None
        new_expires = row["expires_at"]
        if now - row["last_seen_at"] >= 600:
            max_abs = row["created_at"] + max(ttl * 2, 90 * 86400)
            if settings.get("allowAutoLogin", True) and (row["expires_at"] - now) < ttl * 0.75:
                new_expires = min(max_abs, now + ttl)
                refreshed_max_age = max(60, int(new_expires - now))
            with self._lock:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                    (now, new_expires, thash),
                )
                conn.commit()
        return AuthContext(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            role_code=row["role_code"],
            role_name=row["role_name"],
            role_rank=int(row["role_rank"]),
            must_change_password=bool(row["must_change_password"]),
            permissions=perms,
            session_hash=thash,
            reauth_at=row["reauth_at"],
            expires_at=new_expires,
            refreshed_max_age=refreshed_max_age,
        )

    def logout(self, actor: AuthContext | None, ip: str = "") -> None:
        if not actor or not actor.session_hash:
            return
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (actor.session_hash,))
            conn.commit()
        self.log_audit(actor, action="auth.logout", resource_type="auth", summary="退出登录", ip=ip)

    def change_password(self, actor: AuthContext, old_password: str, new_password: str, *, ip: str = "") -> None:
        new_pwd = validate_password(new_password)
        row = self._conn().execute("SELECT password_hash FROM users WHERE id = ?", (actor.user_id,)).fetchone()
        if not row or not verify_password(str(old_password or ""), row["password_hash"]):
            raise AuthError("当前密码不正确。", status=400, code="wrong_password")
        if verify_password(new_pwd, row["password_hash"]):
            raise AuthError("新密码不能与当前密码相同。", status=400, code="same_password")
        new_hash = hash_password(new_pwd)
        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                (new_hash, now, actor.user_id),
            )
            # Invalidate all OTHER sessions for safety while keeping current session logged in
            if actor.session_hash:
                conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (actor.user_id, actor.session_hash))
            conn.commit()
        self.log_audit(actor, action="auth.change_password", resource_type="user", resource_id=actor.user_id, summary="修改个人登录密码", ip=ip)

    def reauth(self, actor: AuthContext, password: str, *, ip: str = "") -> dict[str, Any]:
        if not actor.is_super_admin:
            raise AuthError("仅超级管理员可进行密钥二次验证。", status=403, code="forbidden")
        row = self._conn().execute("SELECT password_hash FROM users WHERE id = ?", (actor.user_id,)).fetchone()
        if not row or not verify_password(str(password or ""), row["password_hash"]):
            self.log_audit(actor, action="auth.reauth_failed", resource_type="auth", summary="查看密钥前的二次密码验证失败", ip=ip)
            raise AuthError("超级管理员密码不正确。", status=403, code="wrong_password")
        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE sessions SET reauth_at = ? WHERE token_hash = ?", (now, actor.session_hash))
            conn.commit()
        window = int(self.get_settings().get("reauthWindowSeconds", 300))
        self.log_audit(actor, action="auth.reauth", resource_type="auth", summary=f"通过密钥查看二次密码验证（有效期 {window} 秒）", ip=ip)
        return {"verified": True, "expiresInSeconds": window}

    def require_reauth(self, actor: AuthContext) -> None:
        if not actor.is_super_admin:
            raise AuthError("仅超级管理员可查看 API 密钥明文。", status=403, code="forbidden")
        window = int(self.get_settings().get("reauthWindowSeconds", 300))
        if not actor.reauth_at or (time.time() - actor.reauth_at) > window:
            raise AuthError("查看密钥明文前请再次输入超级管理员密码进行确认。", status=403, code="reauth_required")

    # ---- User Management (with strict rank / anti-escalation guards) ----
    def _get_role(self, role_code: str) -> dict[str, Any]:
        row = self._conn().execute(
            "SELECT id, code, name, description, rank, is_system FROM roles WHERE code = ?",
            (str(role_code or "").strip(),),
        ).fetchone()
        if not row:
            raise AuthError("指定的角色不存在。", status=400, code="invalid_role")
        return dict(row)

    def _active_super_admin_count(self, exclude_user_id: str = "") -> int:
        row = self._conn().execute(
            """
            SELECT COUNT(*) FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.code = 'super_admin' AND u.status = 'active' AND u.id != ?
            """,
            (exclude_user_id,),
        ).fetchone()
        return int(row[0] if row else 0)

    def _assert_can_manage(
        self,
        actor: AuthContext,
        target_role_rank: int,
        new_role_rank: int | None = None,
        *,
        target_user_id: str = "",
        new_status: str | None = None,
    ) -> None:
        if actor.is_super_admin:
            # Protect the last active super_admin from disabling/demoting/deleting
            if target_user_id:
                t_row = self._conn().execute(
                    "SELECT r.code AS role_code, u.status FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?",
                    (target_user_id,),
                ).fetchone()
                if t_row and t_row["role_code"] == "super_admin":
                    demoting = new_role_rank is not None and new_role_rank < 100
                    disabling = new_status is not None and new_status != "active"
                    if (demoting or disabling) and self._active_super_admin_count(exclude_user_id=target_user_id) == 0:
                        raise AuthError("系统必须至少保留一位启用状态的超级管理员。", status=400, code="last_super_admin")
            return
        if not actor.can("users.manage_subordinate"):
            raise AuthError("你没有管理团队账号的权限。", status=403, code="forbidden")
        if target_user_id and target_user_id == actor.user_id:
            raise AuthError("不能在此修改或停用自己的账号状态。", status=403, code="cannot_manage_self")
        if target_role_rank >= actor.role_rank:
            raise AuthError("权限不足：不能管理同级或更高级别的账号。", status=403, code="forbidden")
        if new_role_rank is not None and new_role_rank >= actor.role_rank:
            raise AuthError("权限不足：不能分配同级或更高级别的角色。", status=403, code="forbidden")

    def list_users(self, actor: AuthContext, *, q: str = "", role: str = "", status: str = "") -> dict[str, Any]:
        if not actor.is_super_admin and not actor.can("users.manage_subordinate"):
            raise AuthError("你没有查看团队账号的权限。", status=403, code="forbidden")
        clauses = ["1=1"]
        params: list[Any] = []
        if not actor.is_super_admin:
            clauses.append("(r.rank < ? OR u.id = ?)")
            params.extend([actor.role_rank, actor.user_id])
        if role:
            clauses.append("r.code = ?")
            params.append(role)
        if status in ("active", "disabled"):
            clauses.append("u.status = ?")
            params.append(status)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            clauses.append("(LOWER(u.username) LIKE ? OR LOWER(u.display_name) LIKE ?)")
            params.extend([like, like])
        rows = self._conn().execute(
            f"""
            SELECT u.id, u.username, u.display_name, u.status, u.must_change_password,
                   u.last_login_at, u.last_login_ip, u.created_at, u.updated_at,
                   r.code AS role_code, r.name AS role_name, r.rank AS role_rank
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.rank DESC, u.created_at ASC
            """,
            params,
        ).fetchall()
        items = []
        for r in rows:
            can_edit = actor.is_super_admin or (r["role_rank"] < actor.role_rank and r["id"] != actor.user_id)
            items.append({
                "id": r["id"],
                "username": r["username"],
                "displayName": r["display_name"],
                "role": r["role_code"],
                "roleName": r["role_name"],
                "rank": int(r["role_rank"]),
                "status": r["status"],
                "mustChangePassword": bool(r["must_change_password"]),
                "lastLoginAt": r["last_login_at"],
                "lastLoginIp": r["last_login_ip"],
                "createdAt": r["created_at"],
                "isSelf": r["id"] == actor.user_id,
                "canManage": can_edit,
            })
        assignable_roles = [
            {"code": r["code"], "name": r["name"], "rank": r["rank"], "description": r["description"]}
            for r in self._conn().execute("SELECT code, name, rank, description FROM roles ORDER BY rank DESC").fetchall()
            if actor.is_super_admin or r["rank"] < actor.role_rank
        ]
        return {"items": items, "total": len(items), "assignableRoles": assignable_roles}

    def create_user(self, actor: AuthContext, values: dict[str, Any], *, ip: str = "") -> dict[str, Any]:
        uname = validate_username(values.get("username"))
        pwd = validate_password(values.get("password"))
        dname = validate_display_name(values.get("displayName"), fallback=uname)
        role_code = str(values.get("role") or "operator").strip()
        role = self._get_role(role_code)
        self._assert_can_manage(actor, target_role_rank=role["rank"], new_role_rank=role["rank"])
        must_change = 1 if values.get("mustChangePassword", True) else 0
        now = time.time()
        uid = "u_" + uuid.uuid4().hex[:12]
        pwd_hash = hash_password(pwd)
        with self._lock:
            conn = self._conn()
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (uname,)).fetchone():
                raise AuthError(f"用户名「{uname}」已存在，请换一个用户名。", status=400, code="username_exists")
            conn.execute(
                """
                INSERT INTO users (id, username, display_name, password_hash, role_id, status,
                    must_change_password, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (uid, uname, dname, pwd_hash, role["id"], must_change, actor.user_id, now, now),
            )
            conn.commit()
        self.log_audit(
            actor,
            action="user.create",
            resource_type="user",
            resource_id=uid,
            summary=f"新建{role['name']}账号「{dname}（{uname}）」",
            detail={"username": uname, "displayName": dname, "role": role["code"]},
            ip=ip,
        )
        return {"id": uid, "username": uname, "displayName": dname, "role": role["code"], "roleName": role["name"], "status": "active"}

    def update_user(self, actor: AuthContext, user_id: str, values: dict[str, Any], *, ip: str = "") -> dict[str, Any]:
        conn = self._conn()
        target = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.status, u.role_id,
                   r.code AS role_code, r.name AS role_name, r.rank AS role_rank
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        if not target:
            raise AuthError("目标账号不存在。", status=404, code="not_found")
        new_role = self._get_role(values["role"]) if "role" in values and values["role"] else None
        new_status = str(values.get("status") or target["status"]).strip()
        if new_status not in ("active", "disabled"):
            raise AuthError("无效的账号状态。", status=400)
        if user_id == actor.user_id and (new_status != "active" or (new_role and new_role["code"] != target["role_code"])):
            raise AuthError("不能停用或变更当前登录账号的角色。", status=400, code="cannot_self_disable")
        self._assert_can_manage(
            actor,
            target_role_rank=int(target["role_rank"]),
            new_role_rank=int(new_role["rank"]) if new_role else None,
            target_user_id=user_id,
            new_status=new_status,
        )
        new_dname = validate_display_name(values.get("displayName", target["display_name"]), fallback=target["username"])
        role_id = new_role["id"] if new_role else target["role_id"]
        role_code = new_role["code"] if new_role else target["role_code"]
        role_name = new_role["name"] if new_role else target["role_name"]
        now = time.time()
        with self._lock:
            conn.execute(
                "UPDATE users SET display_name = ?, role_id = ?, status = ?, updated_at = ? WHERE id = ?",
                (new_dname, role_id, new_status, now, user_id),
            )
            if new_status == "disabled":
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        self.log_audit(
            actor,
            action="user.update",
            resource_type="user",
            resource_id=user_id,
            summary=f"更新账号「{target['username']}」（姓名：{new_dname}，角色：{role_name}，状态：{'启用' if new_status == 'active' else '停用'}）",
            detail={"displayName": new_dname, "role": role_code, "status": new_status},
            ip=ip,
        )
        return {"id": user_id, "username": target["username"], "displayName": new_dname, "role": role_code, "roleName": role_name, "status": new_status}

    def reset_user_password(self, actor: AuthContext, user_id: str, new_password: str, *, must_change: bool = True, ip: str = "") -> dict[str, Any]:
        pwd = validate_password(new_password)
        target = self._conn().execute(
            "SELECT u.id, u.username, u.display_name, r.rank AS role_rank FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?",
            (user_id,),
        ).fetchone()
        if not target:
            raise AuthError("目标账号不存在。", status=404, code="not_found")
        self._assert_can_manage(actor, target_role_rank=int(target["role_rank"]), target_user_id=user_id)
        pwd_hash = hash_password(pwd)
        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                (pwd_hash, 1 if must_change else 0, now, user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        self.limiter.clear_user(target["username"])
        self.log_audit(
            actor,
            action="user.reset_password",
            resource_type="user",
            resource_id=user_id,
            summary=f"重置账号「{target['display_name']}（{target['username']}）」的登录密码并强制下线",
            ip=ip,
        )
        return {"reset": True, "id": user_id}

    def delete_user(self, actor: AuthContext, user_id: str, *, ip: str = "") -> dict[str, Any]:
        if user_id == actor.user_id:
            raise AuthError("不能删除当前正在登录的账号。", status=400, code="cannot_delete_self")
        target = self._conn().execute(
            "SELECT u.id, u.username, u.display_name, r.code AS role_code, r.rank AS role_rank FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?",
            (user_id,),
        ).fetchone()
        if not target:
            raise AuthError("目标账号不存在。", status=404, code="not_found")
        self._assert_can_manage(
            actor,
            target_role_rank=int(target["role_rank"]),
            target_user_id=user_id,
            new_status="disabled",
        )
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_drafts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        self.log_audit(
            actor,
            action="user.delete",
            resource_type="user",
            resource_id=user_id,
            summary=f"删除账号「{target['display_name']}（{target['username']}）」",
            ip=ip,
        )
        return {"deleted": True, "id": user_id}

    # ---- Role & Permission Matrix (Super Admin only) ----
    def list_roles(self, actor: AuthContext) -> dict[str, Any]:
        if not actor.is_super_admin:
            raise AuthError("仅超级管理员可查看和配置角色权限矩阵。", status=403, code="forbidden")
        conn = self._conn()
        role_rows = conn.execute("SELECT id, code, name, description, rank, is_system, updated_at FROM roles ORDER BY rank DESC").fetchall()
        counts = {
            r[0]: int(r[1])
            for r in conn.execute("SELECT role_id, COUNT(*) FROM users GROUP BY role_id").fetchall()
        }
        roles = []
        for r in role_rows:
            if r["code"] == "super_admin":
                perms = sorted(ALL_PERMISSION_CODES)
            else:
                perms = sorted(
                    p[0]
                    for p in conn.execute("SELECT perm_code FROM role_permissions WHERE role_id = ?", (r["id"],)).fetchall()
                )
            roles.append({
                "id": r["id"],
                "code": r["code"],
                "name": r["name"],
                "description": r["description"],
                "rank": int(r["rank"]),
                "isSystem": bool(r["is_system"]),
                "editable": r["code"] != "super_admin",
                "userCount": counts.get(r["id"], 0),
                "permissions": perms,
                "defaultPermissions": DEFAULT_ROLE_MAP.get(r["code"], {}).get("default_permissions", []),
                "updatedAt": r["updated_at"],
            })
        return {"roles": roles, "modules": PERMISSION_MODULES, "superOnlyPermissions": sorted(SUPER_ONLY_PERMISSIONS)}

    def update_role_permissions(self, actor: AuthContext, role_code: str, permissions: list[str], *, ip: str = "") -> dict[str, Any]:
        if not actor.is_super_admin:
            raise AuthError("仅超级管理员可修改角色权限配置。", status=403, code="forbidden")
        if role_code == "super_admin":
            raise AuthError("超级管理员固定拥有全量权限，不可修改。", status=400, code="immutable_role")
        role = self._get_role(role_code)
        if not isinstance(permissions, list):
            raise AuthError("权限列表格式不正确。", status=400)
        cleaned = sorted({str(p).strip() for p in permissions if str(p).strip() in ALL_PERMISSION_CODES and str(p).strip() not in SUPER_ONLY_PERMISSIONS})
        now = time.time()
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role["id"],))
            conn.executemany(
                "INSERT INTO role_permissions (role_id, perm_code) VALUES (?, ?)",
                [(role["id"], code) for code in cleaned],
            )
            conn.execute("UPDATE roles SET updated_at = ? WHERE id = ?", (now, role["id"]))
            conn.commit()
        self.log_audit(
            actor,
            action="role.update_permissions",
            resource_type="role",
            resource_id=role["code"],
            summary=f"调整角色「{role['name']}」的权限配置（共开启 {len(cleaned)} 项权限）",
            detail={"role": role["code"], "permissions": cleaned},
            ip=ip,
        )
        return self.list_roles(actor)

    def reset_role_permissions(self, actor: AuthContext, role_code: str, *, ip: str = "") -> dict[str, Any]:
        if not actor.is_super_admin:
            raise AuthError("仅超级管理员可重置角色权限。", status=403, code="forbidden")
        if role_code not in DEFAULT_ROLE_MAP or role_code == "super_admin":
            raise AuthError("该角色不支持恢复默认权限。", status=400)
        return self.update_role_permissions(actor, role_code, DEFAULT_ROLE_MAP[role_code]["default_permissions"], ip=ip)

    # ---- Per-user Creation Draft ----
    def get_draft(self, user_id: str) -> dict[str, Any]:
        if not user_id:
            return {}
        row = self._conn().execute("SELECT draft_json FROM user_drafts WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row[0])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_draft(self, user_id: str, draft: dict[str, Any]) -> None:
        if not user_id:
            return
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO user_drafts (user_id, draft_json, updated_at) VALUES (?, ?, ?)",
                (user_id, json.dumps(draft, ensure_ascii=False), time.time()),
            )
            conn.commit()

    # ---- Audit Logs ----
    def _log_raw_audit(
        self,
        *,
        user_id: str = "",
        username: str = "",
        display_name: str = "",
        role_code: str = "",
        action: str,
        resource_type: str = "",
        resource_id: str = "",
        summary: str = "",
        detail: Any = None,
        ip: str = "",
    ) -> None:
        try:
            detail_str = json.dumps(detail if detail is not None else {}, ensure_ascii=False)[:4000]
            with self._lock:
                conn = self._conn()
                conn.execute(
                    """
                    INSERT INTO audit_logs (user_id, username, display_name, role_code, action,
                        resource_type, resource_id, summary, detail_json, ip, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, username, display_name, role_code, action, resource_type, str(resource_id or ""), summary[:500], detail_str, ip[:64], time.time()),
                )
                conn.commit()
        except Exception:
            pass

    def log_audit(
        self,
        actor: AuthContext | None,
        *,
        action: str,
        resource_type: str = "",
        resource_id: str = "",
        summary: str = "",
        detail: Any = None,
        ip: str = "",
    ) -> None:
        self._log_raw_audit(
            user_id=actor.user_id if actor else "",
            username=actor.username if actor else "",
            display_name=actor.display_name if actor else "",
            role_code=actor.role_code if actor else "",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            detail=detail,
            ip=ip,
        )

    def query_audit_logs(
        self,
        actor: AuthContext,
        *,
        page: Any = 1,
        page_size: Any = 30,
        user_id: str = "",
        action: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        if not actor.can("audit.read"):
            raise AuthError("你没有查看操作审计日志的权限。", status=403, code="forbidden")
        try:
            p = max(1, int(page))
        except (TypeError, ValueError):
            p = 1
        try:
            ps = max(1, min(100, int(page_size)))
        except (TypeError, ValueError):
            ps = 30
        clauses = ["1=1"]
        params: list[Any] = []
        if not actor.is_super_admin:
            # Admins do not see super-admin secret reveal / role config events
            clauses.append("action NOT IN ('credential.reveal', 'auth.reauth', 'auth.reauth_failed', 'role.update_permissions')")
        if user_id.strip():
            clauses.append("user_id = ?")
            params.append(user_id.strip())
        if action.strip():
            clauses.append("action LIKE ?")
            params.append(action.strip() + "%")
        if q.strip():
            like = f"%{q.strip().lower()}%"
            clauses.append("(LOWER(username) LIKE ? OR LOWER(display_name) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(action) LIKE ?)")
            params.extend([like, like, like, like])
        where = " AND ".join(clauses)
        conn = self._conn()
        total = int(conn.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where}", params).fetchone()[0])
        total_pages = max(1, (total + ps - 1) // ps)
        if p > total_pages:
            p = total_pages
        rows = conn.execute(
            f"""
            SELECT id, user_id, username, display_name, role_code, action,
                   resource_type, resource_id, summary, ip, created_at
            FROM audit_logs
            WHERE {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, ps, (p - 1) * ps),
        ).fetchall()
        return {
            "items": [
                {
                    "id": r["id"],
                    "userId": r["user_id"],
                    "username": r["username"],
                    "displayName": r["display_name"] or r["username"],
                    "role": r["role_code"],
                    "action": r["action"],
                    "resourceType": r["resource_type"],
                    "resourceId": r["resource_id"],
                    "summary": r["summary"],
                    "ip": r["ip"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ],
            "total": total,
            "page": p,
            "pageSize": ps,
            "totalPages": total_pages,
        }

    # ---- CLI emergency reset for super_admin ----
    def reset_superadmin_cli(self, username: str | None = None, password: str | None = None, display_name: str = "超级管理员") -> dict[str, Any]:
        conn = self._conn()
        existing = conn.execute(
            "SELECT u.id, u.username, u.display_name FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'super_admin' ORDER BY u.created_at ASC LIMIT 1"
        ).fetchone()
        if existing:
            target_user = existing["username"]
            pwd = validate_password(password)
            now = time.time()
            with self._lock:
                conn.execute(
                    "UPDATE users SET password_hash = ?, status = 'active', must_change_password = 0, updated_at = ? WHERE id = ?",
                    (hash_password(pwd), now, existing["id"]),
                )
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (existing["id"],))
                conn.commit()
            self.limiter.clear_user(target_user)
            self._log_raw_audit(
                user_id=existing["id"],
                username=target_user,
                display_name=existing["display_name"],
                role_code="super_admin",
                action="auth.cli_reset_superadmin",
                resource_type="user",
                resource_id=existing["id"],
                summary="通过服务器命令行重置超级管理员密码",
            )
            return {"action": "reset", "username": target_user}
        uname = validate_username(username or "admin")
        pwd = validate_password(password)
        _, _, ctx = self.setup_superadmin(uname, pwd, display_name=display_name)
        return {"action": "created", "username": ctx.username}

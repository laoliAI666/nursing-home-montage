# Linux 云服务器公网部署指南

本指南用于在 **Ubuntu 22.04 / 24.04 或 Debian 12** 云服务器上将「养老混剪工作台」以多用户、带登录与权限控制的方式安全对外发布。

---

## 1. 推荐配置与运行依赖

- **最低建议配置**：4 核 CPU / 8 GB 内存 / 100 GB+ 系统与数据盘（视频渲染与素材缓存占用空间）。
- **基础依赖安装**（以 Ubuntu 22.04/24.04 为例）：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg curl git nginx certbot python3-certbot-nginx fonts-noto-cjk

# 安装 Node.js 20 LTS（用于封面与画面渲染）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# 安装 uv（Python 环境管理，供渲染与脚本调用）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- **可选：安装多平台分发 CLI（蚁小二 `yxer`）**：
  - 在 Linux 无桌面环境下，请在工作台「设置 → 多平台分发（蚁小二）」中选择 **云发布通道 (`cloud`)**，或在另一台保持在线的办公机登录蚁小二客户端并填写对应 `ClientID`。

---

## 2. 目录规划与权限

建议创建独立运行用户（如 `montage`），避免以 `root` 直接跑 Web 服务：

```bash
sudo useradd -m -s /bin/bash montage
sudo mkdir -p /srv/montage-project
sudo chown -R montage:montage /srv/montage-project
```

将项目目录与 `.agents/skills/nursing-home-montage` 放到 `/srv/montage-project`（或安装到 `/srv/montage-project/.agents/skills/nursing-home-montage`）。

---

## 3. 使用 `systemd` 托管工作台服务

创建 `/etc/systemd/system/montage-workbench.service`：

```ini
[Unit]
Description=Nursing Home Montage Workbench (Server Mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=montage
Group=montage
WorkingDirectory=/srv/montage-project
Environment=MONTAGE_WORKBENCH_MODE=server
Environment=MONTAGE_WORKBENCH_BIND=127.0.0.1
Environment=MONTAGE_WORKBENCH_PORT=8765
Environment=MONTAGE_TRUST_PROXY=1
Environment=MONTAGE_PUBLIC_ORIGIN=https://studio.example.com
ExecStart=/usr/bin/python3 /srv/montage-project/.agents/skills/nursing-home-montage/scripts/workbench/server.py \
  --project /srv/montage-project \
  --mode server \
  --bind 127.0.0.1 \
  --port 8765 \
  --trust-proxy \
  --public-origin https://studio.example.com
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

启用并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now montage-workbench
sudo systemctl status montage-workbench
```

> **安全要点**：Python 服务仅绑定在 `127.0.0.1:8765`，绝不直接暴露到公网；同时云安全组仅放行 `80/443`（和按需的 `22`）。

---

## 4. Nginx HTTPS 反向代理配置（支持 2GB 素材上传与视频流播放）

创建 `/etc/nginx/sites-available/montage-workbench.conf`：

```nginx
server {
    listen 80;
    server_name studio.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name studio.example.com;

    # 使用 certbot --nginx 自动签发与续期证书
    ssl_certificate     /etc/letsencrypt/live/studio.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/studio.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # 支持单条最大 2GB 的实拍素材上传
    client_max_body_size 2100m;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

启用配置并申请免费 SSL 证书：

```bash
sudo ln -sf /etc/nginx/sites-available/montage-workbench.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d studio.example.com
```

> 若更偏好 **Caddy**（自动签发 HTTPS 证书），`Caddyfile` 仅需 6 行：
> ```caddy
> studio.example.com {
>     reverse_proxy 127.0.0.1:8765 {
>         flush_interval -1
>     }
> }
> ```

---

## 5. 首次初始化与日常运维

1. **初始化超级管理员**：打开 `https://studio.example.com/`，页面自动进入「初始化超级管理员」引导，设置用户名与强密码后即可进入工作台。
2. **调整自动登录过期时间**：超管在「设置 → 登录与会话过期设置」中可自定义自动登录有效期（1–365 天，默认 7 天）和密钥二次认证有效时间（默认 5 分钟）。
3. **分配团队角色与权限**：超管在左侧「账号与权限」创建管理员和运营账号，并在「角色权限矩阵」按页面与操作粒度微调权限（即时生效）。
4. **忘记超级管理员密码时的紧急重置**（在 Linux 服务器终端执行）：

```bash
sudo -u montage /usr/bin/python3 \
  /srv/montage-project/.agents/skills/nursing-home-montage/scripts/workbench/server.py \
  --project /srv/montage-project \
  --reset-superadmin
```

5. **SQLite WAL 定时热备份**（建议每日 `cron` 备份 `workbench.db`、`project.json`、`secrets.json`）：

```bash
# 使用 sqlite3 .backup 确保 WAL 模式下的一致性快照
sqlite3 /srv/montage-project/.montage/workbench/workbench.db \
  ".backup '/var/backups/montage/workbench-$(date +%F).db'"
```

6. **在线自动升级与 `systemd` 自动拉起**：
   - 工作台内置定时检查官方 GitHub 正式 Release（仅正式版 `vX.Y.Z` + `SHA256SUMS` 校验）。发现新版本时，仅超级管理员在顶栏与左下角看到温和提示；点击升级前自动核验全量运行中任务（视频队列、素材整理、发布上传、音色复刻、文件上传），支持**空闲后自动升级预约**。
   - 在上述 `systemd`（`Restart=always`）托管模式下，完成文件原子替换与逐文件哈希校验后，工作台主进程优雅退出并由 `systemd` 在 3 秒内自动拉起新版本，无需 `sudo` 权限，且保留 `.montage/` 数据与 SQLite 登录态。每次升级前自动备份旧版运行代码至 `.montage/updates/backups/`（保留最近 3 份），校验或替换异常自动回滚。
   - 如个别云服务器需要网络代理、下载镜像前缀或提高 GitHub API 限流配额，可在 `systemd` 服务 `[Service]` 中按需添加环境变量（默认留空直连即可）：
     - `Environment=HTTPS_PROXY=http://127.0.0.1:7890`
     - `Environment=MONTAGE_GITHUB_MIRROR=https://...`
     - `Environment=MONTAGE_GITHUB_TOKEN=ghp_...`

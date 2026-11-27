'use strict';
// Non-intrusive auto-upgrade UI: topbar gentle pill, sidebar highlighted card,
// slide-over upgrade drawer, scheduled idle upgrade, reconnect poller & minimal settings panel.

(function initWorkbenchUpdater() {
  const UPD_STATE = {
    summary: null,       // lightweight summary from state.update
    detail: null,        // full payload from GET /api/update/status
    drawerOpen: false,
    pillDismissedForVer: '',
    pollTimer: null,
    reconnecting: false,
    reconnectStartAt: 0,
    reconnectTargetVer: '',
    reconnectPrevVer: '',
    reconnectTimer: null,
    reconnectTimedOut: false,
    settingsLoaded: false,
  };

  // Register arrow-up icon if not present
  if (typeof paths === 'object' && !paths['arrow-up']) {
    paths['arrow-up'] = '<path d="M12 19V5m-6 6 6-6 6 6"/>';
  }

  function isSuperAdmin() {
    return Boolean(window.currentUser && (window.currentUser.isSuperAdmin || window.currentUser.role === 'super_admin'));
  }

  function formatRelativeTime(ts) {
    if (!ts) return '尚未检查';
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    try {
      const d = new Date(Number(ts) * 1000);
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return '较早之前';
    }
  }

  function formatIsoDate(raw) {
    if (!raw) return '';
    try {
      const d = new Date(raw);
      if (Number.isNaN(d.getTime())) return String(raw);
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return String(raw);
    }
  }

  function renderReleaseNotesHtml(rawText) {
    const text = String(rawText || '').trim();
    if (!text) return '<p class="subtle">该正式版本未附带详细更新说明，可前往 GitHub Release 页面查看。</p>';
    const lines = text.split(/\r?\n/);
    const out = [];
    let inList = false;
    const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
    const inlineFmt = str => E(str).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');

    for (const raw of lines) {
      const line = raw.trim();
      if (!line) {
        closeList();
        continue;
      }
      if (/^#{1,3}\s+/.test(line)) {
        closeList();
        out.push(`<h4>${inlineFmt(line.replace(/^#{1,3}\s+/, ''))}</h4>`);
      } else if (/^[-*•]\s+/.test(line)) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push(`<li>${inlineFmt(line.replace(/^[-*•]\s+/, ''))}</li>`);
      } else {
        closeList();
        out.push(`<p>${inlineFmt(line)}</p>`);
      }
    }
    closeList();
    return out.join('');
  }

  // ---- Ensure DOM slots exist (gracefully injected once) ----
  function ensureChromeSlots() {
    // 1. Maintenance banner at top of .workspace
    const workspace = $('.workspace');
    if (workspace && !$('#maintenance-banner')) {
      workspace.insertAdjacentHTML('afterbegin', `
        <div id="maintenance-banner" class="maintenance-banner" role="status" aria-live="polite" hidden>
          <span class="maintenance-banner-spin" aria-hidden="true"></span>
          <div id="maintenance-banner-text"></div>
          <button type="button" class="maintenance-banner-action" id="maintenance-banner-btn" data-upd-action="open-drawer" hidden>查看进度</button>
        </div>
      `);
    }

    // 2. Topbar gentle pill right before #topbar-user
    const topbarActions = $('.topbar-actions');
    const topbarUser = $('#topbar-user');
    if (topbarActions && !$('#topbar-update-pill')) {
      const pillHtml = `
        <div id="topbar-update-pill" class="topbar-update-pill" hidden>
          <button type="button" class="topbar-update-main" data-upd-action="open-drawer">
            <span class="topbar-update-dot" aria-hidden="true"></span>
            <span id="topbar-update-label">发现新版本</span>
          </button>
          <button type="button" class="topbar-update-dismiss" id="topbar-update-dismiss" data-upd-action="dismiss-pill"
            aria-label="稍后提醒" title="稍后提醒（收起胶囊，随时可从左下角或设置页查看）">
            ${icon('close')}
          </button>
        </div>
      `;
      if (topbarUser) topbarUser.insertAdjacentHTML('beforebegin', pillHtml);
      else topbarActions.insertAdjacentHTML('beforeend', pillHtml);
    }

    // 3. Sidebar bottom full-width highlighted upgrade card (above .local-note)
    const sidebarBottom = $('.sidebar-bottom');
    if (sidebarBottom && !$('#sidebar-upgrade-card')) {
      sidebarBottom.insertAdjacentHTML('afterbegin', `
        <button type="button" id="sidebar-upgrade-card" class="sidebar-upgrade-card" data-upd-action="open-drawer" hidden>
          <span class="sidebar-upgrade-left">
            <span class="sidebar-upgrade-dot" aria-hidden="true"></span>
            <span id="sidebar-upgrade-text">可升级</span>
          </span>
          ${icon('chevron-right')}
        </button>
      `);
    }

    // 4. Right slide-over non-blocking drawer
    if (!$('#update-drawer')) {
      document.body.insertAdjacentHTML('beforeend', `
        <div id="update-drawer-backdrop" class="update-drawer-backdrop" data-upd-action="close-drawer"></div>
        <aside id="update-drawer" class="update-drawer" role="dialog" aria-labelledby="update-drawer-title" aria-modal="false">
          <div class="update-drawer-head">
            <h2 id="update-drawer-title">工作台版本更新</h2>
            <button type="button" class="update-drawer-close" aria-label="关闭抽屉" data-upd-action="close-drawer">${icon('close')}</button>
          </div>
          <div id="update-drawer-body" class="update-drawer-body"></div>
          <div id="update-drawer-foot" class="update-drawer-foot"></div>
        </aside>
      `);
    }
  }

  // ---- Render Chrome (Topbar Pill, Sidebar Card, Maintenance Banner) ----
  function renderUpdateChrome() {
    ensureChromeSlots();
    const u = UPD_STATE.summary || state?.update || null;
    const superAdmin = isSuperAdmin();
    const isUpgrading = Boolean(u?.upgrading || UPD_STATE.reconnecting);
    const targetVer = UPD_STATE.reconnectTargetVer || u?.targetVersion || u?.latestVersion || '';
    const hasUpdate = Boolean(superAdmin && u?.hasUpdate);
    const isScheduled = Boolean(superAdmin && u?.scheduled && hasUpdate);

    // 1. Maintenance banner (ALL authenticated users during active upgrade/restart)
    const banner = $('#maintenance-banner');
    const bannerText = $('#maintenance-banner-text');
    const bannerBtn = $('#maintenance-banner-btn');
    if (banner && bannerText) {
      if (isUpgrading) {
        banner.hidden = false;
        if (superAdmin) {
          const stepLabel = UPD_STATE.reconnecting ? '等待服务重启恢复' : `步骤 ${u?.step || 1}/4`;
          bannerText.innerHTML = `正在升级至 <strong>v${E(targetVer)}</strong>（${E(stepLabel)}），已开启维护锁防止新任务写入。`;
          if (bannerBtn) bannerBtn.hidden = false;
        } else {
          bannerText.innerHTML = `工作台正在由管理员升级${targetVer ? `至 <strong>v${E(targetVer)}</strong>` : ''}，服务重启预计 15–30 秒，完成后将自动恢复，无需重新登录。`;
          if (bannerBtn) bannerBtn.hidden = true;
        }
      } else {
        banner.hidden = true;
      }
    }

    // 2. Topbar gentle pill (Super Admin only)
    const pill = $('#topbar-update-pill');
    const pillLabel = $('#topbar-update-label');
    const pillDismiss = $('#topbar-update-dismiss');
    if (pill && pillLabel) {
      if (!superAdmin) {
        pill.hidden = true;
      } else if (isUpgrading) {
        pill.hidden = false;
        pill.className = 'topbar-update-pill is-upgrading';
        pillLabel.textContent = UPD_STATE.reconnecting
          ? `正在重启恢复 v${targetVer}…`
          : `正在升级 v${targetVer} (${u?.step || 1}/4)`;
        if (pillDismiss) pillDismiss.hidden = true;
      } else if (isScheduled) {
        pill.hidden = false;
        pill.className = 'topbar-update-pill is-scheduled';
        pillLabel.textContent = `已预约空闲后升级 v${u.scheduledVersion || targetVer}`;
        if (pillDismiss) pillDismiss.hidden = true;
      } else if (hasUpdate && !u.isIgnored && UPD_STATE.pillDismissedForVer !== u.latestVersion) {
        pill.hidden = false;
        pill.className = 'topbar-update-pill';
        pillLabel.textContent = `新版本 v${u.latestVersion} 可用`;
        if (pillDismiss) pillDismiss.hidden = false;
      } else {
        pill.hidden = true;
      }
    }

    // 3. Sidebar bottom full-width highlighted card (Super Admin only)
    const sideCard = $('#sidebar-upgrade-card');
    const sideText = $('#sidebar-upgrade-text');
    if (sideCard && sideText) {
      if (!superAdmin) {
        sideCard.hidden = true;
      } else if (isUpgrading) {
        sideCard.hidden = false;
        sideCard.className = 'sidebar-upgrade-card is-upgrading';
        sideText.textContent = UPD_STATE.reconnecting ? '正在重启恢复…' : `正在升级 (${u?.step || 1}/4)…`;
      } else if (isScheduled) {
        sideCard.hidden = false;
        sideCard.className = 'sidebar-upgrade-card is-scheduled';
        sideText.textContent = `已预约 v${u.scheduledVersion || targetVer}`;
      } else if (hasUpdate) {
        sideCard.hidden = false;
        sideCard.className = 'sidebar-upgrade-card';
        sideText.textContent = `可升级 v${u.latestVersion}`;
      } else {
        sideCard.hidden = true;
      }
    }

    // Trigger reconnect monitor automatically if backend enters restarting phase
    if (u?.upgrading && u?.phase === 'restarting' && !UPD_STATE.reconnecting) {
      beginReconnectPolling(u.targetVersion || targetVer, u.currentVersion || state?.config?.version || '');
    }
  }

  // ---- Fetch Full Status & Drawer Management ----
  async function fetchFullUpdateStatus() {
    if (!isSuperAdmin()) return null;
    try {
      const data = await api('/api/update/status');
      UPD_STATE.detail = data;
      UPD_STATE.settingsLoaded = true;
      if (data.upgrading && data.phase === 'restarting' && !UPD_STATE.reconnecting) {
        beginReconnectPolling(data.targetVersion || data.latest?.version || '', data.currentVersion || '');
      }
      renderUpdateChrome();
      if (UPD_STATE.drawerOpen) renderDrawer();
      renderSettingsUpdatePanelIfMounted();
      return data;
    } catch (err) {
      // During restart, connection refused is expected — switch smoothly into reconnect polling
      if (UPD_STATE.detail?.upgrading || UPD_STATE.summary?.upgrading) {
        if (!UPD_STATE.reconnecting) {
          beginReconnectPolling(
            UPD_STATE.detail?.targetVersion || UPD_STATE.summary?.targetVersion || '',
            UPD_STATE.detail?.currentVersion || state?.config?.version || ''
          );
        }
      }
      return null;
    }
  }

  function startDrawerPolling() {
    stopDrawerPolling();
    UPD_STATE.pollTimer = setInterval(() => {
      if (UPD_STATE.reconnecting) return;
      if (UPD_STATE.drawerOpen || UPD_STATE.detail?.upgrading || UPD_STATE.detail?.scheduled) {
        fetchFullUpdateStatus();
      } else {
        stopDrawerPolling();
      }
    }, 2200);
  }

  function stopDrawerPolling() {
    if (UPD_STATE.pollTimer) {
      clearInterval(UPD_STATE.pollTimer);
      UPD_STATE.pollTimer = null;
    }
  }

  async function openUpdateDrawer() {
    if (!isSuperAdmin()) return;
    ensureChromeSlots();
    UPD_STATE.drawerOpen = true;
    $('#update-drawer-backdrop')?.classList.add('open');
    $('#update-drawer')?.classList.add('open');
    renderDrawer();
    await fetchFullUpdateStatus();
    startDrawerPolling();
  }

  function closeUpdateDrawer() {
    UPD_STATE.drawerOpen = false;
    $('#update-drawer-backdrop')?.classList.remove('open');
    $('#update-drawer')?.classList.remove('open');
    if (!UPD_STATE.detail?.upgrading && !UPD_STATE.detail?.scheduled) {
      stopDrawerPolling();
    }
  }

  // ---- Auto-Reconnect Polling after Server Restart ----
  function beginReconnectPolling(targetVer, prevVer) {
    if (UPD_STATE.reconnecting) return;
    UPD_STATE.reconnecting = true;
    UPD_STATE.reconnectTimedOut = false;
    UPD_STATE.reconnectStartAt = Date.now();
    UPD_STATE.reconnectTargetVer = targetVer || '';
    UPD_STATE.reconnectPrevVer = prevVer || '';
    stopDrawerPolling();
    renderUpdateChrome();
    if (UPD_STATE.drawerOpen) renderDrawer();

    let sawOffline = false;
    const poll = async () => {
      const elapsedMs = Date.now() - UPD_STATE.reconnectStartAt;
      if (elapsedMs > 95000) {
        UPD_STATE.reconnectTimedOut = true;
        if (UPD_STATE.drawerOpen) renderDrawer();
        return;
      }
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json();
          const ver = String(data?.version || '');
          const stillUpgrading = Boolean(data?.upgrading);
          const versionChanged = Boolean(ver && UPD_STATE.reconnectPrevVer && ver !== UPD_STATE.reconnectPrevVer);
          if (data?.ok && !stillUpgrading && (sawOffline || versionChanged || elapsedMs > 5000)) {
            try {
              sessionStorage.setItem('montage_upgraded_to', ver || UPD_STATE.reconnectTargetVer);
            } catch {}
            location.reload();
            return;
          }
        } else {
          sawOffline = true;
        }
      } catch {
        sawOffline = true;
      }
      UPD_STATE.reconnectTimer = setTimeout(poll, 1500);
    };
    UPD_STATE.reconnectTimer = setTimeout(poll, 1600);
  }

  // ---- Render Right Slide-over Drawer ----
  function renderDrawer() {
    const body = $('#update-drawer-body');
    const foot = $('#update-drawer-foot');
    if (!body || !foot) return;

    const d = UPD_STATE.detail;
    if (!d) {
      body.innerHTML = `<p class="subtle">正在读取版本信息与当前任务状态…</p>`;
      foot.innerHTML = `<span class="subtle">请稍候</span><button type="button" class="secondary" data-upd-action="close-drawer">关闭</button>`;
      return;
    }

    const curVer = d.currentVersion || state?.config?.version || '';
    const latest = d.latest || null;
    const targetVer = d.targetVersion || latest?.version || '';
    const isUpgrading = Boolean(d.upgrading || UPD_STATE.reconnecting);

    // 1. Upgrading / Restarting / Reconnecting Progress View
    if (isUpgrading) {
      const activeStep = UPD_STATE.reconnecting ? 4 : Math.max(1, Number(d.step || 1));
      const pct = UPD_STATE.reconnecting ? 96 : Math.min(100, Math.max(12, Number(d.progress || activeStep * 24)));
      const restartDesc = d.restartMode === 'systemd'
        ? '服务优雅退出由 systemd 自动拉起，页面正在轮询等待新版本就绪…'
        : '服务正在自动重启，页面正在轮询等待新版本就绪…';
      const steps = [
        { n: 1, title: '下载并校验正式发行包', desc: `拉取 ${latest?.zipName || '官方正式包'} 并核对 SHA256SUMS 与配置架构兼容性` },
        { n: 2, title: '锁定新任务并备份当前版本', desc: `二次核验无活跃任务，备份 v${curVer} 至 .montage/updates/backups/` },
        { n: 3, title: '原子替换运行代码并逐文件核验', desc: '仅替换发行运行文件，完整保留配置、数据库、素材与成品' },
        { n: 4, title: '重启工作台并自动等待重连', desc: restartDesc },
      ];
      body.innerHTML = `
        <div class="upd-release-card">
          <div class="upd-ver-line">
            <span class="upd-ver-pill">v${E(curVer)}</span>
            <span style="color:var(--muted)">→</span>
            <span class="upd-ver-pill target">v${E(targetVer)}</span>
            <span class="badge">升级进行中 ${pct}%</span>
          </div>
          <div class="upd-progress-bar"><span style="width:${pct}%"></span></div>
          <p class="subtle" style="margin:10px 0 0">${UPD_STATE.reconnectTimedOut
            ? '等待服务恢复超时（超过 90 秒）。请尝试手动刷新页面，或在服务器查看 .montage/workbench/server.log。'
            : '请保持页面打开（约 15–30 秒）。升级重启后页面将自动刷新，无需重新登录。'}</p>
        </div>
        ${UPD_STATE.reconnectTimedOut ? `
          <div class="upd-callout warn">
            <div class="upd-callout-head">暂未探测到服务恢复</div>
            服务可能仍在启动中，或需要手动查看服务状态。原版本备份安全保留在 <code>.montage/updates/backups/</code>。
          </div>
        ` : ''}
        <ol class="upd-steps">
          ${steps.map(s => {
            const cls = s.n < activeStep ? 'done' : s.n === activeStep ? 'active' : '';
            return `
              <li class="upd-step ${cls}">
                <span class="upd-step-num">${s.n < activeStep ? '✓' : s.n}</span>
                <div class="upd-step-body">
                  <strong>${E(s.title)}</strong>
                  <p>${E(s.desc)}</p>
                </div>
              </li>
            `;
          }).join('')}
        </ol>
      `;
      foot.innerHTML = UPD_STATE.reconnectTimedOut
        ? `<span class="subtle">可手动重试刷新</span><button type="button" class="primary" onclick="location.reload()">刷新页面</button>`
        : `<span class="subtle">升级期间已自动拦截新任务写入</span><button type="button" class="secondary" data-upd-action="close-drawer">后台继续</button>`;
      return;
    }

    // 2. No Newer Release Available
    if (!d.hasUpdate) {
      body.innerHTML = `
        <div class="upd-release-card">
          <div class="upd-ver-line">
            <span class="upd-ver-pill target">当前 v${E(curVer)}</span>
            ${d.lastError ? '<span class="badge warn">上次检查失败</span>' : '<span class="badge">已是最新正式版</span>'}
          </div>
          <div class="upd-release-meta">
            <span>上次检查：${E(formatRelativeTime(d.lastCheckedAt))}</span>
            <span>重启方式：${d.restartMode === 'systemd' ? 'systemd 自动拉起' : '本机自动重启'}</span>
          </div>
        </div>
        ${d.lastError ? `
          <div class="upd-callout warn">
            <div class="upd-callout-head">检查更新失败（未覆盖当前版本状态）</div>
            ${E(d.lastError)}
          </div>
        ` : `
          <div class="upd-callout ok">
            <div class="upd-callout-head">${icon('check')}<span>当前运行的是最新正式发布版</span></div>
            当 GitHub 发布新的正式 Release 后，工作台会在顶栏与左下角以温和方式提醒你。
          </div>
        `}
      `;
      foot.innerHTML = `
        <button type="button" class="text-button" data-upd-action="goto-settings">打开更新设置 →</button>
        <div class="update-drawer-foot-actions">
          <button type="button" class="secondary" data-upd-action="close-drawer">关闭</button>
          <button type="button" class="primary" data-upd-action="check-now">立即检查更新</button>
        </div>
      `;
      return;
    }

    // 3. New Official Release Available (Ready / Blocked + Schedule / Error / Dirty Git)
    const blockers = Array.isArray(d.blockers) ? d.blockers : [];
    const dirtyFiles = Array.isArray(d.dirtyFiles) ? d.dirtyFiles : [];
    const hasBlockers = blockers.length > 0;
    const isDirty = dirtyFiles.length > 0;
    const canStartNow = !hasBlockers && !isDirty;

    let guardBlock = '';
    if (d.phase === 'failed' && d.error && d.errorCode !== 'dirty_git') {
      guardBlock += `
        <div class="upd-callout error">
          <div class="upd-callout-head">上次升级未完成，原版本 v${E(curVer)} 已保留</div>
          ${E(d.error)}
        </div>
      `;
    }

    if (isDirty) {
      guardBlock += `
        <div class="upd-callout warn">
          <div class="upd-callout-head">检测到本地未提交的代码改动，已阻止覆盖</div>
          当前安装目录存在 <code>.git</code> 仓库且有 <strong>${dirtyFiles.length}</strong> 处未提交修改。为防止自动升级覆盖你正在开发的本地代码，系统已自动拦截替换。
        </div>
        <div class="upd-dirty-box">
          <strong>未提交文件：</strong>
          <pre>${E(dirtyFiles.join('\n'))}</pre>
          <p style="margin:8px 0 0">请先在终端提交或暂存本地改动，再执行在线升级（正式 Linux 服务器发行包部署不受此限制）。</p>
        </div>
      `;
    } else if (hasBlockers) {
      guardBlock += `
        <div class="upd-callout warn">
          <div class="upd-callout-head">
            <span>当前有 ${blockers.length} 项任务运行中，暂不允许立即升级</span>
          </div>
          为避免视频制作、素材整理、发布上传或音色复刻被重启中断，请等待以下任务完成：
          <ul class="upd-blocker-list">
            ${blockers.map(b => `
              <li class="upd-blocker-item">
                <div>
                  <strong>${E(b.title)}</strong>
                  <span class="upd-blocker-detail">${E(b.detail)}</span>
                </div>
                <div class="upd-blocker-right">
                  <span class="badge warn">${E(b.kind)}</span>
                  ${b.route ? `<a class="text-button" href="${E(b.route)}" data-upd-action="close-drawer">查看</a>` : ''}
                </div>
              </li>
            `).join('')}
          </ul>
        </div>
        <div class="upd-schedule-box ${d.scheduled ? 'active' : ''}">
          <div>
            <strong>所有任务空闲后自动升级并重启</strong>
            <p>${d.scheduled
              ? '已开启预约：将暂停派发新的排队任务，上述运行中任务全部结束后自动完成下载、校验、替换与重启。'
              : '开启后，工作台会在当前任务全部结束后自动升级并重启，期间可随时取消预约。'}</p>
          </div>
          <input type="checkbox" class="switch" aria-label="所有任务空闲后自动升级并重启"
            ${d.scheduled ? 'checked' : ''} data-upd-toggle="schedule">
        </div>
      `;
    } else {
      guardBlock += `
        <div class="upd-callout ok">
          <div class="upd-callout-head">${icon('check')}<span>当前空闲，可安全升级并重启</span></div>
          已核验制作队列、素材整理、多平台发布、音色复刻及上传任务均为空闲。升级预计耗时 15–30 秒，全部配置、素材、成品与登录态完整保留。
        </div>
      `;
    }

    body.innerHTML = `
      <div class="upd-release-card">
        <div class="upd-ver-line">
          <span class="upd-ver-pill">当前 v${E(curVer)}</span>
          <span style="color:var(--muted)">→</span>
          <span class="upd-ver-pill target">最新 v${E(latest.version)}</span>
          <span class="badge">正式发布版</span>
        </div>
        <div class="upd-release-meta">
          ${latest.publishedAt ? `<span>发布时间：${E(formatIsoDate(latest.publishedAt))}</span>` : ''}
          ${latest.zipSizeLabel ? `<span>包大小：${E(latest.zipSizeLabel)}</span>` : ''}
          <span>SHA-256 校验</span>
          ${latest.htmlUrl ? `<a class="text-button" href="${E(latest.htmlUrl)}" target="_blank" rel="noopener noreferrer" style="min-height:auto;padding:0">GitHub 发布页 ${icon('arrow-up-right')}</a>` : ''}
        </div>
      </div>

      <div>
        <h3 style="font-size:14px;margin:0 0 8px">更新说明</h3>
        <div class="upd-notes">${renderReleaseNotesHtml(latest.body)}</div>
      </div>

      ${guardBlock}
    `;

    foot.innerHTML = `
      <button type="button" class="text-button" data-upd-action="ignore-version" data-version="${E(latest.version)}">忽略此版本</button>
      <div class="update-drawer-foot-actions">
        <button type="button" class="secondary" data-upd-action="close-drawer">稍后再说</button>
        <button type="button" class="primary" data-upd-action="start-upgrade" ${canStartNow ? '' : 'disabled'}
          title="${isDirty ? '存在本地未提交代码改动' : hasBlockers ? '当前有任务运行中，请等待完成或开启空闲后自动升级' : '开始升级并重启工作台'}">
          ${d.phase === 'failed' ? '重试升级并重启' : '立即升级并重启'}
        </button>
      </div>
    `;
  }

  // ---- Minimal Settings Card (Super Admin Only, zero proxy/channel clutter) ----
  function buildSettingsPanelHtml(d) {
    const curVer = d?.currentVersion || state?.config?.version || '…';
    const hasUpdate = Boolean(d?.hasUpdate && d?.latest);
    const settings = d?.settings || { autoCheck: true, checkIntervalMinutes: 60 };
    const history = Array.isArray(d?.history) ? d.history.slice(0, 5) : [];
    const restartLabel = (d?.restartMode || UPD_STATE.summary?.restartMode) === 'systemd'
      ? 'Linux systemd 自动托管重启'
      : '本机服务自动重启';

    let summaryHtml = '';
    if (d?.lastError) {
      summaryHtml = `
        <div class="upd-callout warn" style="margin-bottom:14px">
          <div class="upd-callout-head">上次检查更新失败（未覆盖当前版本状态）</div>
          ${E(d.lastError)}
        </div>
      `;
    }

    summaryHtml += `
      <div class="upd-settings-summary">
        <div>
          <div class="upd-settings-ver-row">
            <span class="upd-settings-ver-num">v${E(curVer)}</span>
            ${hasUpdate ? `
              <span style="color:var(--muted)">→</span>
              <span class="upd-settings-ver-num" style="color:var(--accent)">v${E(d.latest.version)}</span>
              <span class="badge">新正式版可用</span>
              ${d.scheduled ? '<span class="badge warn">已预约空闲后升级</span>' : ''}
            ` : `<span class="badge">${d?.lastError ? '当前版本正常运行' : '已是最新正式版'}</span>`}
          </div>
          <p class="form-help" style="margin:6px 0 0">上次检查：${E(formatRelativeTime(d?.lastCheckedAt))} · 重启方式：${E(restartLabel)}</p>
        </div>
        <div class="upd-settings-actions">
          <button type="button" class="secondary" data-upd-action="check-now">立即检查更新</button>
          ${hasUpdate ? `<button type="button" class="primary" data-upd-action="open-drawer">查看更新并升级</button>` : ''}
        </div>
      </div>
    `;

    const historyHtml = history.length ? `
      <div style="margin-top:18px">
        <div class="field-label" style="margin-bottom:4px">升级与自动备份记录</div>
        <p class="form-help" style="margin:0">每次升级前自动备份上一版运行代码至 <code>.montage/updates/backups/</code>（保留最近 3 份）。</p>
        <ul class="upd-history-list">
          ${history.map(item => `
            <li>
              <div>
                <strong>v${E(item.fromVersion)} → v${E(item.toVersion)}</strong>
                <span class="upd-history-meta" style="margin-left:8px">${E(formatRelativeTime(item.at))} · ${E(item.actorName || '系统')}${item.backupName ? ` · 备份 ${E(item.backupName)}` : ''}</span>
                ${item.error ? `<div class="upd-history-meta" style="color:var(--danger);margin-top:2px">${E(item.error)}</div>` : ''}
              </div>
              <span class="badge ${item.status === 'success' ? '' : item.status === 'rolled_back' ? 'warn' : 'error'}">
                ${item.status === 'success' ? '升级成功' : item.status === 'rolled_back' ? '已自动回滚' : '升级中止'}
              </span>
            </li>
          `).join('')}
        </ul>
      </div>
    ` : '';

    return `
      <div class="settings-section-heading">
        <span>${icon('arrow-up')}</span>
        <div>
          <h2>版本与自动升级</h2>
          <p>自动同步 GitHub 最新正式版本，升级过程保留全部配置、素材、成品与登录状态。</p>
        </div>
      </div>
      ${summaryHtml}
      <div class="switch-row">
        <label for="upd-auto-check">自动检查新版本（检测到后仅温和提醒，不弹窗打断）</label>
        <input type="checkbox" class="switch" id="upd-auto-check" ${settings.autoCheck !== false ? 'checked' : ''} data-upd-setting="autoCheck">
      </div>
      <div class="form-group" style="margin-top:14px;max-width:280px">
        <label class="field-label" for="upd-check-interval">自动检查频率</label>
        <select id="upd-check-interval" class="input" data-native-select data-upd-setting="checkIntervalMinutes">
          <option value="30" ${Number(settings.checkIntervalMinutes) === 30 ? 'selected' : ''}>每 30 分钟</option>
          <option value="60" ${Number(settings.checkIntervalMinutes) === 60 ? 'selected' : ''}>每 1 小时（推荐）</option>
          <option value="360" ${Number(settings.checkIntervalMinutes) === 360 ? 'selected' : ''}>每 6 小时</option>
          <option value="1440" ${Number(settings.checkIntervalMinutes) === 1440 ? 'selected' : ''}>每天一次</option>
        </select>
      </div>
      ${historyHtml}
    `;
  }

  function mountUpdateSettingsPanel(form) {
    if (!form || !isSuperAdmin()) return;
    let panel = $('#update-settings-panel', form);
    if (!panel) {
      panel = document.createElement('section');
      panel.id = 'update-settings-panel';
      panel.className = 'panel';
      const anchor = form.querySelector('#auth-settings-panel') || form.querySelector('.sticky-actions');
      if (anchor && anchor.id === 'auth-settings-panel') {
        anchor.insertAdjacentElement('afterend', panel);
      } else if (anchor) {
        anchor.insertAdjacentElement('beforebegin', panel);
      } else {
        form.appendChild(panel);
      }
    }
    panel.innerHTML = buildSettingsPanelHtml(UPD_STATE.detail);
    if (!UPD_STATE.settingsLoaded) {
      fetchFullUpdateStatus();
    }
  }

  function renderSettingsUpdatePanelIfMounted() {
    const form = $('#settings-form');
    if (form && route === 'settings' && location.hash !== '#settings/music' && isSuperAdmin()) {
      const panel = $('#update-settings-panel', form);
      if (panel) panel.innerHTML = buildSettingsPanelHtml(UPD_STATE.detail);
    }
  }

  window.mountUpdateSettingsPanel = mountUpdateSettingsPanel;

  // ---- Hook into updateChrome & render ----
  if (typeof updateChrome === 'function') {
    const prevUpdateChrome = updateChrome;
    updateChrome = function updateAwareChrome() {
      prevUpdateChrome();
      if (state?.update) UPD_STATE.summary = state.update;
      renderUpdateChrome();
    };
  }

  if (typeof render === 'function') {
    const prevRender = render;
    render = function updateAwareRender() {
      prevRender();
      if (route === 'settings' && location.hash !== '#settings/music') {
        const form = $('#settings-form');
        if (form) mountUpdateSettingsPanel(form);
      }
      renderUpdateChrome();
    };
  }

  // ---- Event Delegation for All Update Controls ----
  document.addEventListener('click', async e => {
    const el = e.target.closest('[data-upd-action]');
    if (!el) return;
    const act = el.dataset.updAction;

    if (act === 'open-drawer') {
      openUpdateDrawer();
      return;
    }
    if (act === 'close-drawer') {
      closeUpdateDrawer();
      return;
    }
    if (act === 'dismiss-pill') {
      const ver = UPD_STATE.summary?.latestVersion || UPD_STATE.detail?.latest?.version || '';
      UPD_STATE.pillDismissedForVer = ver;
      renderUpdateChrome();
      notify('已暂时收起顶栏提示，随时可点击左下角或进入设置页升级');
      return;
    }
    if (act === 'goto-settings') {
      closeUpdateDrawer();
      setRoute('settings');
      requestAnimationFrame(() => $('#update-settings-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      return;
    }
    if (act === 'check-now') {
      await busy(el, async () => {
        try {
          const res = await api('/api/update/check', { force: true });
          UPD_STATE.detail = res;
          UPD_STATE.settingsLoaded = true;
          await refresh(false).catch(() => {});
          if (state?.update) UPD_STATE.summary = state.update;
          renderUpdateChrome();
          if (UPD_STATE.drawerOpen) renderDrawer();
          renderSettingsUpdatePanelIfMounted();
          if (res.lastError) notify(res.lastError, true);
          else if (res.hasUpdate) notify(`检测到最新正式版 v${res.latest.version}`);
          else notify(`当前 v${res.currentVersion} 已是最新正式版`);
        } catch (err) {
          notify(err.message || '检查更新失败', true);
        }
      });
      return;
    }
    if (act === 'ignore-version') {
      const ver = el.dataset.version || UPD_STATE.detail?.latest?.version || '';
      if (!ver) return;
      await busy(el, async () => {
        try {
          const res = await api('/api/update/settings', { ignoredVersion: ver });
          if (res.status) UPD_STATE.detail = res.status;
          UPD_STATE.pillDismissedForVer = ver;
          await refresh(false).catch(() => {});
          if (state?.update) UPD_STATE.summary = state.update;
          closeUpdateDrawer();
          renderUpdateChrome();
          notify(`已忽略 v${ver} 顶栏提醒，下一个正式版发布时将再次通知`);
        } catch (err) {
          notify(err.message || '操作失败', true);
        }
      });
      return;
    }
    if (act === 'start-upgrade') {
      await busy(el, async () => {
        try {
          const res = await api('/api/update/start', {});
          UPD_STATE.detail = res;
          if (state?.update) {
            state.update.upgrading = true;
            state.update.phase = res.phase;
            state.update.step = res.step;
            state.update.targetVersion = res.targetVersion;
            UPD_STATE.summary = state.update;
          }
          renderUpdateChrome();
          renderDrawer();
          startDrawerPolling();
        } catch (err) {
          notify(err.message || '暂时无法开始升级', true);
          await fetchFullUpdateStatus();
        }
      });
      return;
    }
  });

  document.addEventListener('change', async e => {
    const toggle = e.target.closest('[data-upd-toggle="schedule"]');
    if (toggle) {
      const wantSchedule = Boolean(toggle.checked);
      toggle.disabled = true;
      try {
        const res = await api(wantSchedule ? '/api/update/schedule' : '/api/update/cancel-schedule', {});
        UPD_STATE.detail = res;
        await refresh(false).catch(() => {});
        if (state?.update) UPD_STATE.summary = state.update;
        renderUpdateChrome();
        renderDrawer();
        renderSettingsUpdatePanelIfMounted();
        if (res.upgrading) {
          notify(`当前已空闲，正在自动升级至 v${res.targetVersion}…`);
          startDrawerPolling();
        } else if (wantSchedule) {
          notify('已预约空闲后自动升级：运行中任务全部结束后将自动升级并重启');
          startDrawerPolling();
        } else {
          notify('已取消自动升级预约');
        }
      } catch (err) {
        toggle.checked = !wantSchedule;
        notify(err.message || '操作失败', true);
      } finally {
        toggle.disabled = false;
      }
      return;
    }

    const settingEl = e.target.closest('[data-upd-setting]');
    if (settingEl) {
      const key = settingEl.dataset.updSetting;
      const val = settingEl.type === 'checkbox' ? Boolean(settingEl.checked) : Number(settingEl.value);
      try {
        const res = await api('/api/update/settings', { [key]: val });
        if (res.status) UPD_STATE.detail = res.status;
        renderSettingsUpdatePanelIfMounted();
        notify(key === 'autoCheck'
          ? (val ? '已开启自动检查新版本' : '已关闭自动检查，仍可随时手动检查')
          : '自动检查频率已保存');
      } catch (err) {
        notify(err.message || '保存更新配置失败', true);
      }
    }
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && UPD_STATE.drawerOpen) closeUpdateDrawer();
  });

  // Post-upgrade welcome toast after auto-reload
  try {
    const upgradedVer = sessionStorage.getItem('montage_upgraded_to');
    if (upgradedVer) {
      sessionStorage.removeItem('montage_upgraded_to');
      setTimeout(() => {
        if (typeof notify === 'function') {
          notify(`工作台已升级至 v${upgradedVer}，当前登录状态与任务记录已保留`);
        }
      }, 700);
    }
  } catch {}
})();

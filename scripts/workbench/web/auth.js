'use strict';
// Global authentication gate, RBAC permission helpers, UI adaptation & super-admin re-auth modal.

window.currentUser = null;
window.authSettings = { allowAutoLogin: true, sessionTtlDays: 7, reauthWindowSeconds: 300 };
window.deployMode = 'local';
let workbenchBooted = false;

window.hasPerm = function hasPerm(code) {
  const u = window.currentUser;
  if (!u) return false;
  if (u.isSuperAdmin || u.role === 'super_admin') return true;
  return Array.isArray(u.permissions) && u.permissions.includes(code);
};

window.hasAnyPerm = function hasAnyPerm(...codes) {
  return codes.some(c => window.hasPerm(c));
};

window.canDeleteKind = function canDeleteKind(kind) {
  const map = {
    institution: 'library.delete',
    asset: 'library.delete',
    task: 'tasks.delete',
    person: 'people.delete',
    music: 'music.delete',
  };
  return map[kind] ? window.hasPerm(map[kind]) : false;
};

// Register icons for account menu & Admin nav
paths.users = '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>';
paths.key = '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.8 12.2 8.2-8.2M16 7l3 3M19 4l2 2"/>';
paths.logout = '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>';

// Wrap api() to handle 401 session expiry / 403 forced password change cleanly
const apiBeforeAuth = api;
api = async function authAwareApi(path, data, options = {}) {
  const isAuthPublic = path.startsWith('/api/auth/');
  try {
    return await apiBeforeAuth(path, data, options);
  } catch (err) {
    if (!isAuthPublic && err && (err.status === 401 || err.code === 'unauthorized')) {
      window.currentUser = null;
      showAuthGate('login');
    } else if (!isAuthPublic && err && err.code === 'password_change_required') {
      if (window.currentUser) window.currentUser.mustChangePassword = true;
      showAuthGate('force-password');
    } else if (!isAuthPublic && err && err.status === 403 && err.code === 'forbidden') {
      window.refreshCurrentUser?.();
    }
    throw err;
  }
};

// Also attach authSettings into /api/config submissions
const apiBeforeConfigPatch = api;
api = async function configAwareApi(path, data, options = {}) {
  if (path === '/api/config' && data && typeof data === 'object' && !('authSettings' in data)) {
    const form = $('#settings-form');
    const authFields = window.authSettingsFields?.(form);
    if (authFields) data = { ...data, authSettings: authFields };
  }
  return apiBeforeConfigPatch(path, data, options);
};

window.refreshCurrentUser = async function refreshCurrentUser() {
  try {
    const res = await apiBeforeAuth('/api/auth/me');
    if (res?.user) {
      window.currentUser = res.user;
      if (res.authSettings) window.authSettings = { ...window.authSettings, ...res.authSettings };
      if (res.deployMode) window.deployMode = res.deployMode;
      renderSidebarUser();
      applyNavPermissions();
    }
  } catch {}
};

// ---------------------------------------------------------------------------
// Fullscreen Auth Gate (Setup Super Admin / Login / Forced Password Change)
// ---------------------------------------------------------------------------
function showAuthGate(mode, message = '') {
  const gate = $('#auth-gate');
  const shell = $('.app-shell');
  if (shell) shell.hidden = true;
  if (!gate) return;
  gate.hidden = false;

  const isServer = window.deployMode === 'server';
  const modeLabel = isServer ? '云端协作工作台' : '本地视频工作台';
  const ttlDays = window.authSettings?.sessionTtlDays ?? 7;
  const allowAuto = window.authSettings?.allowAutoLogin !== false;

  if (mode === 'setup') {
    gate.innerHTML = `
      <div class="auth-card auth-card-wide">
        <div class="auth-brand">
          <span class="auth-brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
          <div><strong>叙影 · 视频工作台</strong><small>${E(modeLabel)} · 首次启用初始化</small></div>
        </div>
        <span class="auth-mode-pill">初始化超级管理员</span>
        <h1>设置你的超级管理员账号</h1>
        <p class="auth-subtitle">超级管理员拥有工作台全部权限，可管理团队账号、按页面粒度配置管理员和运营权限。完成后即可进入工作台。</p>
        <form id="auth-setup-form" class="auth-form">
          <div class="form-group">
            <label class="field-label" for="setup-username">管理员用户名</label>
            <input id="setup-username" name="username" class="input" required minlength="3" maxlength="32" pattern="[A-Za-z][A-Za-z0-9_\\-]{2,31}" value="admin" autocomplete="username">
          </div>
          <div class="form-group">
            <label class="field-label" for="setup-name">显示姓名</label>
            <input id="setup-name" name="displayName" class="input" required maxlength="40" value="超级管理员" placeholder="例如 张总">
          </div>
          <div class="form-group">
            <label class="field-label" for="setup-pwd">设置登录密码</label>
            <input id="setup-pwd" name="password" class="input" type="password" required minlength="8" maxlength="128" placeholder="至少 8 位，同时包含字母和数字" autocomplete="new-password">
          </div>
          <div class="form-group">
            <label class="field-label" for="setup-pwd2">确认登录密码</label>
            <input id="setup-pwd2" name="password2" class="input" type="password" required minlength="8" maxlength="128" placeholder="请再输入一次密码" autocomplete="new-password">
          </div>
          ${allowAuto ? `
            <div class="auth-remember-row">
              <label><input type="checkbox" name="remember" checked><span>记住登录状态（${E(ttlDays)} 天内自动登录）</span></label>
            </div>
          ` : ''}
          <p class="inline-error" role="alert">${E(message)}</p>
          <button type="submit" class="primary auth-submit">创建超级管理员并进入工作台</button>
        </form>
        <p class="auth-footnote">密码采用高强度不可逆哈希加密存储。若日后忘记超管密码，可在服务器终端执行 <code>--reset-superadmin</code> 重置。</p>
      </div>
    `;
    const form = $('#auth-setup-form');
    form.elements.password.focus();
    form.onsubmit = async e => {
      e.preventDefault();
      const errEl = form.querySelector('[role="alert"]');
      errEl.textContent = '';
      if (form.elements.password.value !== form.elements.password2.value) {
        errEl.textContent = '两次输入的密码不一致，请检查。';
        return;
      }
      await busy(form.querySelector('[type="submit"]'), async () => {
        try {
          const res = await apiBeforeAuth('/api/auth/setup', {
            username: form.elements.username.value.trim(),
            displayName: form.elements.displayName.value.trim(),
            password: form.elements.password.value,
            remember: form.elements.remember ? form.elements.remember.checked : true,
          });
          enterAuthenticatedApp(res.user);
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    };
    return;
  }

  if (mode === 'force-password') {
    const u = window.currentUser;
    gate.innerHTML = `
      <div class="auth-card">
        <div class="auth-brand">
          <span class="auth-brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
          <div><strong>叙影 · 视频工作台</strong><small>账号安全验证</small></div>
        </div>
        <h1>首次登录，请先修改密码</h1>
        <p class="auth-subtitle">当前账号：<strong>${E(u?.displayName || '')}（@${E(u?.username || '')}）</strong>。为保障账号安全，请设置你的专属新密码后继续使用。</p>
        <form id="auth-force-pwd-form" class="auth-form">
          <div class="form-group">
            <label class="field-label" for="fp-old">当前初始密码</label>
            <input id="fp-old" name="oldPassword" class="input" type="password" required autocomplete="current-password">
          </div>
          <div class="form-group">
            <label class="field-label" for="fp-new">新密码</label>
            <input id="fp-new" name="newPassword" class="input" type="password" required minlength="8" maxlength="128" placeholder="至少 8 位，同时包含字母和数字" autocomplete="new-password">
          </div>
          <div class="form-group">
            <label class="field-label" for="fp-new2">确认新密码</label>
            <input id="fp-new2" name="newPassword2" class="input" type="password" required minlength="8" maxlength="128" autocomplete="new-password">
          </div>
          <p class="inline-error" role="alert">${E(message)}</p>
          <button type="submit" class="primary auth-submit">保存新密码并进入工作台</button>
        </form>
        <div style="margin-top:14px;text-align:center">
          <button type="button" class="text-button" id="fp-logout">退出并切换账号</button>
        </div>
      </div>
    `;
    const form = $('#auth-force-pwd-form');
    form.elements.oldPassword.focus();
    $('#fp-logout').onclick = () => performLogout();
    form.onsubmit = async e => {
      e.preventDefault();
      const errEl = form.querySelector('[role="alert"]');
      errEl.textContent = '';
      if (form.elements.newPassword.value !== form.elements.newPassword2.value) {
        errEl.textContent = '两次输入的新密码不一致。';
        return;
      }
      await busy(form.querySelector('[type="submit"]'), async () => {
        try {
          await apiBeforeAuth('/api/auth/change-password', {
            oldPassword: form.elements.oldPassword.value,
            newPassword: form.elements.newPassword.value,
          });
          await window.refreshCurrentUser();
          enterAuthenticatedApp(window.currentUser);
          notify('密码已更新');
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    };
    return;
  }

  // Default: Login mode
  gate.innerHTML = `
    <div class="auth-card">
      <div class="auth-brand">
        <span class="auth-brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <div><strong>叙影 · 视频工作台</strong><small>${E(modeLabel)}</small></div>
      </div>
      <h1>登录工作台</h1>
      <p class="auth-subtitle">请输入团队分配的账号和密码继续工作。</p>
      <form id="auth-login-form" class="auth-form">
        <div class="form-group">
          <label class="field-label" for="login-username">用户名</label>
          <input id="login-username" name="username" class="input" required autocomplete="username" placeholder="请输入用户名">
        </div>
        <div class="form-group">
          <label class="field-label" for="login-password">密码</label>
          <input id="login-password" name="password" class="input" type="password" required autocomplete="current-password" placeholder="请输入密码">
        </div>
        ${allowAuto ? `
          <div class="auth-remember-row">
            <label><input type="checkbox" name="remember" checked><span>记住登录状态（${E(ttlDays)} 天内自动登录）</span></label>
          </div>
        ` : ''}
        <p class="inline-error" role="alert">${E(message)}</p>
        <button type="submit" class="primary auth-submit">登录</button>
      </form>
      <p class="auth-footnote">没有账号或忘记密码，请联系管理员或超级管理员重置。</p>
    </div>
  `;
  const form = $('#auth-login-form');
  form.elements.username.focus();
  form.onsubmit = async e => {
    e.preventDefault();
    const errEl = form.querySelector('[role="alert"]');
    errEl.textContent = '';
    await busy(form.querySelector('[type="submit"]'), async () => {
      try {
        const res = await apiBeforeAuth('/api/auth/login', {
          username: form.elements.username.value.trim(),
          password: form.elements.password.value,
          remember: form.elements.remember ? form.elements.remember.checked : false,
        });
        if (res.user?.mustChangePassword) {
          window.currentUser = res.user;
          showAuthGate('force-password');
          return;
        }
        enterAuthenticatedApp(res.user);
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

function enterAuthenticatedApp(user) {
  window.currentUser = user;
  const gate = $('#auth-gate');
  const shell = $('.app-shell');
  if (gate) { gate.hidden = true; gate.innerHTML = ''; }
  if (shell) shell.hidden = false;
  renderSidebarUser();
  applyNavPermissions();
  const curHead = (location.hash.slice(1) || '').split('/')[0];
  if (curHead && !isRouteAllowed(curHead)) {
    location.hash = firstAllowedRoute();
  }
  if (!workbenchBooted) {
    workbenchBooted = true;
    window.bootWorkbench?.();
  } else {
    refresh(true, true).then(() => render()).catch(() => {});
  }
}

async function performLogout() {
  try { await apiBeforeAuth('/api/auth/logout', {}); } catch {}
  window.currentUser = null;
  state = undefined;
  showAuthGate('login');
}

// ---------------------------------------------------------------------------
// Topbar Right-Corner Account Capsule & Dropdown Menu
// ---------------------------------------------------------------------------
function closeTopbarUserMenu() {
  const trigger = $('#topbar-user-trigger');
  const menu = $('#topbar-user-menu');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  if (menu) menu.hidden = true;
}

function formatSessionExpiryShort(ts) {
  if (!ts) return '本次会话有效';
  try {
    const d = new Date(ts * 1000);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `自动登录有效至 ${mm}/${dd}`;
  } catch {
    return '登录状态有效';
  }
}

function renderSidebarUser() {
  const u = window.currentUser;
  const slot = $('#topbar-user');
  if (!slot || !u) return;
  const initial = (u.displayName || u.username || 'U').trim().slice(0, 1).toUpperCase();
  const canAdmin = u.isSuperAdmin || window.hasAnyPerm('users.manage_subordinate', 'audit.read');
  const canSettings = window.hasPerm('settings.update');
  const wasOpen = $('#topbar-user-trigger')?.getAttribute('aria-expanded') === 'true';

  slot.hidden = false;
  slot.innerHTML = `
    <button type="button" class="topbar-user-trigger" id="topbar-user-trigger"
      aria-haspopup="menu" aria-expanded="${wasOpen ? 'true' : 'false'}"
      aria-label="当前账号：${E(u.displayName)}（${E(u.roleName)}）" data-auth-action="toggle-user-menu">
      <span class="topbar-user-avatar" aria-hidden="true">${E(initial)}</span>
      <span class="topbar-user-name">${E(u.displayName)}</span>
      <span class="topbar-user-role role-${E(u.role)}">${E(u.roleName)}</span>
      <span class="topbar-user-chevron" aria-hidden="true">${icon('chevron-down')}</span>
    </button>
    <div class="topbar-user-menu" id="topbar-user-menu" role="menu" ${wasOpen ? '' : 'hidden'}>
      <div class="topbar-user-menu-head">
        <span class="topbar-user-avatar" aria-hidden="true">${E(initial)}</span>
        <div class="topbar-user-menu-info">
          <strong title="${E(u.displayName)}">${E(u.displayName)}</strong>
          <span>@${E(u.username)}</span>
        </div>
      </div>
      <div class="topbar-user-menu-session">
        <span class="topbar-user-role role-${E(u.role)}">${E(u.roleName)}</span>
        <span>${E(formatSessionExpiryShort(u.sessionExpiresAt))}</span>
      </div>
      <div class="topbar-user-menu-list">
        ${canAdmin ? `<button type="button" class="topbar-user-menu-item" role="menuitem" data-auth-action="goto-admin">${icon('users')}<span>账号与权限中心</span></button>` : ''}
        ${canSettings ? `<button type="button" class="topbar-user-menu-item" role="menuitem" data-auth-action="goto-auth-settings">${icon('shield')}<span>登录与过期设置</span></button>` : ''}
        <button type="button" class="topbar-user-menu-item" role="menuitem" data-auth-action="change-own-pwd">${icon('key')}<span>修改登录密码</span></button>
        <div class="topbar-user-menu-sep" role="separator"></div>
        <button type="button" class="topbar-user-menu-item is-danger" role="menuitem" data-auth-action="logout">${icon('logout')}<span>退出登录</span></button>
      </div>
    </div>
  `;

  // Update sidebar footer note for server mode
  const note = $('.local-note span:last-child');
  if (note) note.textContent = window.deployMode === 'server' ? '云端团队协作模式' : '在你的电脑上运行';
}

function openOwnPasswordDialog(opener) {
  const u = window.currentUser;
  const d = managementDialog('修改登录密码', opener);
  const body = d.querySelector('.management-dialog-content');
  body.innerHTML = `
    <form id="own-pwd-form" class="auth-form">
      <p class="form-help" style="margin-bottom:14px">账号：<strong>${E(u?.displayName || '')}（@${E(u?.username || '')}）</strong>。修改后将自动清除其他设备上的旧登录。</p>
      <div class="form-group">
        <label class="field-label" for="op-old">当前密码</label>
        <input id="op-old" name="oldPassword" class="input" type="password" required autocomplete="current-password">
      </div>
      <div class="form-group">
        <label class="field-label" for="op-new">新密码（至少 8 位含字母与数字）</label>
        <input id="op-new" name="newPassword" class="input" type="password" required minlength="8" maxlength="128" autocomplete="new-password">
      </div>
      <div class="form-group">
        <label class="field-label" for="op-new2">确认新密码</label>
        <input id="op-new2" name="newPassword2" class="input" type="password" required minlength="8" maxlength="128" autocomplete="new-password">
      </div>
      <p class="inline-error" role="alert"></p>
      <div class="auth-dialog-actions">
        <button type="button" class="secondary" data-close>取消</button>
        <button type="submit" class="primary">确认修改</button>
      </div>
    </form>
  `;
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('#own-pwd-form').onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = body.querySelector('[role="alert"]');
    errEl.textContent = '';
    if (f.elements.newPassword.value !== f.elements.newPassword2.value) {
      errEl.textContent = '两次输入的新密码不一致。';
      return;
    }
    await busy(f.querySelector('[type="submit"]'), async () => {
      try {
        await api('/api/auth/change-password', {
          oldPassword: f.elements.oldPassword.value,
          newPassword: f.elements.newPassword.value,
        });
        d.close();
        notify('个人登录密码已修改');
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

// ---------------------------------------------------------------------------
// Super-Admin Re-Auth Modal (before revealing stored API secrets)
// ---------------------------------------------------------------------------
window.promptSuperAdminReauth = function promptSuperAdminReauth(opener) {
  return new Promise(resolve => {
    const d = managementDialog('验证超级管理员密码', opener);
    const body = d.querySelector('.management-dialog-content');
    const sec = window.authSettings?.reauthWindowSeconds || 300;
    let done = false;
    body.innerHTML = `
      <form id="reauth-form" class="auth-form">
        <p class="form-help" style="margin-bottom:14px">API 密钥属于高敏感凭据。请输入当前超级管理员密码确认身份（验证后 ${Math.round(sec / 60)} 分钟内有效，并记入审计日志）。</p>
        <div class="form-group">
          <label class="field-label" for="reauth-pwd">超级管理员密码</label>
          <input id="reauth-pwd" name="password" class="input" type="password" required autocomplete="current-password">
        </div>
        <p class="inline-error" role="alert"></p>
        <div class="auth-dialog-actions">
          <button type="button" class="secondary" data-close>取消</button>
          <button type="submit" class="primary">验证并查看</button>
        </div>
      </form>
    `;
    const finish = ok => { if (!done) { done = true; resolve(ok); } };
    d.addEventListener('close', () => finish(false));
    body.querySelector('[data-close]').onclick = () => d.close();
    body.querySelector('#reauth-pwd').focus();
    body.querySelector('#reauth-form').onsubmit = async e => {
      e.preventDefault();
      const f = e.target;
      const errEl = body.querySelector('[role="alert"]');
      errEl.textContent = '';
      await busy(f.querySelector('[type="submit"]'), async () => {
        try {
          const res = await api('/api/auth/reauth', { password: f.elements.password.value });
          window._reauthVerifiedUntil = Date.now() + (Number(res?.expiresInSeconds) || sec) * 1000;
          if (window.currentUser) window.currentUser.reauthRemainingSeconds = Number(res?.expiresInSeconds) || sec;
          done = true;
          d.close();
          resolve(true);
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    };
  });
};

// ---------------------------------------------------------------------------
// Navigation & In-Page Permission Enforcement
// ---------------------------------------------------------------------------
function firstAllowedRoute() {
  if (window.hasPerm('library.read')) return 'library';
  if (window.hasPerm('tasks.read')) return 'tasks';
  if (window.hasPerm('works.read')) return 'works';
  if (window.hasPerm('publish.read')) return 'publish';
  if (window.hasPerm('people.read')) return 'people';
  if (window.hasPerm('styles.read')) return 'styles';
  if (window.hasPerm('settings.read')) return 'settings';
  if (window.currentUser?.isSuperAdmin || window.hasAnyPerm('users.manage_subordinate', 'audit.read')) return 'admin';
  return 'onboarding';
}

function applyNavPermissions() {
  if (!window.currentUser) return;
  const navRules = {
    create: () => window.hasPerm('tasks.create'),
    library: () => window.hasPerm('library.read'),
    tasks: () => window.hasPerm('tasks.read'),
    works: () => window.hasPerm('works.read'),
    publish: () => window.hasPerm('publish.read'),
    people: () => window.hasPerm('people.read'),
    styles: () => window.hasPerm('styles.read'),
    settings: () => window.hasAnyPerm('settings.read', 'music.read'),
    admin: () => window.currentUser.isSuperAdmin || window.hasAnyPerm('users.manage_subordinate', 'audit.read'),
  };
  document.querySelectorAll('[data-nav]').forEach(el => {
    const check = navRules[el.dataset.nav];
    if (check) el.hidden = !check();
  });
  const mobileNew = $('.mobile-new');
  if (mobileNew) mobileNew.hidden = !window.hasPerm('tasks.create');
  const agentBtn = $('[data-action="agent-settings"]');
  if (agentBtn) agentBtn.hidden = !window.hasPerm('settings.read');
}

function applyPagePermissions() {
  if (!window.currentUser) return;
  const isServer = window.deployMode === 'server';
  const hide = sel => {
    main.querySelectorAll(sel).forEach(el => { el.hidden = true; });
    document.querySelectorAll('dialog.person-editor-dialog').forEach(d => d.querySelectorAll(sel).forEach(el => { el.hidden = true; }));
  };

  // Server mode: hide all host-local filesystem & shutdown controls
  if (isServer) {
    hide('[data-action="pick-source"], [data-action="scan-source"], [data-action="new-institution-folder"], [data-action="add-folder"], [data-action="sync-materials"], [data-action="open-folder"], [data-action="exit-workbench"]');
    const wsInput = main.querySelector('#workspace-directory');
    if (wsInput) {
      const panel = wsInput.closest('section.panel');
      if (panel) panel.hidden = true;
    }
    const exitPanel = main.querySelector('[data-action="exit-workbench"]')?.closest('section.panel');
    if (exitPanel) exitPanel.hidden = true;
  }

  // Library permissions
  if (!window.hasPerm('library.create')) {
    hide('a[href="#institution-new"], [data-action="new-institution-folder"], [data-action="upload-institution-folder"], [data-action="add-folder"], [data-action="add-files"]');
  }
  if (!window.hasPerm('library.update')) {
    hide('[data-action="sync-materials"], [data-action="set-institution-cover"], [data-action="prep-institution"], [data-action="prep-asset"], [data-action="edit-asset-analysis"]');
    main.querySelectorAll('[data-asset-id]').forEach(el => { el.disabled = true; });
    const profForm = main.querySelector('#institution-profile-form');
    if (profForm) {
      profForm.querySelectorAll('input, textarea, button[type="submit"]').forEach(el => { el.disabled = true; });
    }
  }

  // Tasks permissions
  if (!window.hasPerm('tasks.create')) {
    hide('a[href="#create"], [data-action="revise-video"]');
  }
  if (!window.hasPerm('tasks.update')) {
    hide('[data-action="start-task"], [data-action="cancel-task"], [data-action="queue-retry"], [data-action="queue-cancel"], [data-action="queue-recover"], #answer-form');
  }
  if (!window.hasPerm('tasks.accept')) {
    hide('[data-action="review-task"]');
  }

  // Works permissions
  if (!window.hasPerm('works.update')) {
    hide('a[href^="#video/"], [data-action="edit-cover"]');
  }
  if (!window.hasPerm('works.delete')) {
    hide('[data-action="delete-product"]');
  }

  // Publish permissions
  if (!window.hasAnyPerm('publish.create_private', 'publish.create_public')) {
    hide('[data-action="quick-publish-one"], [data-action="open-publish-selected"], [data-action="select-unpublished-works"]');
  }
  if (!window.hasPerm('publish.update')) {
    hide('[data-action="publish-retry-item"], [data-action="publish-refresh-item"], [data-action="sync-person-publish-accounts"]');
  }
  if (!window.hasPerm('publish.delete')) {
    hide('[data-action="publish-delete-batch"]');
  }

  // People permissions
  if (!window.hasPerm('people.create')) {
    hide('[data-action="add-person"]');
  }
  if (!window.hasPerm('people.update')) {
    hide('[data-action="edit-person"], [data-action="upload-person-music"]');
  }
  if (!window.hasPerm('people.voice_clone')) {
    hide('[data-action="start-voice-training"], [data-action="pick-voice-sample"], [data-action="generate-voice-sample"], [data-action="view-voice-slots"]');
  }

  // Styles permissions
  if (!window.hasPerm('styles.set_default')) {
    hide('[data-style-action="set-default"], [data-action="set-default-style"]');
  }
  if (!window.hasPerm('styles.bind')) {
    hide('[data-style-action="bind"], [data-action="bind-style"]');
  }

  // Music permissions
  if (!window.hasPerm('music.create')) {
    hide('[data-action="upload-music"]');
  }
  if (!window.hasPerm('music.update')) {
    hide('[data-action="rename-music-track"], [data-action="default-music-track"], [data-action="toggle-music-track"]');
  }

  // Settings permissions
  if (route === 'settings' && location.hash !== '#settings/music') {
    const form = main.querySelector('#settings-form');
    if (form) {
      if (window.hasPerm('settings.update')) {
        window.mountAuthSettingsPanel?.(form);
      } else {
        form.querySelectorAll('input, select, textarea').forEach(el => { el.disabled = true; });
        form.querySelectorAll('.sticky-actions, [data-test-service], [data-action="reveal"], [data-action="check-agent"]').forEach(el => { el.hidden = true; });
        if (!form.querySelector('.readonly-settings-banner')) {
          form.insertAdjacentHTML('afterbegin', '<p class="inline-note readonly-settings-banner" style="margin-bottom:16px">当前账号仅可查看工作台配置状态（已脱敏），修改全局服务设置需超级管理员权限。</p>');
        }
      }
      if (!window.currentUser.isSuperAdmin) {
        hide('[data-action="reveal"], [data-action="exit-workbench"]');
      }
    }
  }

  // Creator attribution chips on task detail
  if (route === 'task' && state?.tasks) {
    const t = state.tasks.find(x => x.id === selectedTask);
    const meta = main.querySelector('.detail-top p');
    if (t && meta && t.createdByName && !meta.dataset.creatorAdded) {
      meta.dataset.creatorAdded = '1';
      meta.insertAdjacentHTML('beforeend', ` · 创建人：${E(t.createdByName)}${t.acceptedByName ? ' · 验收人：' + E(t.acceptedByName) : ''}`);
    }
  }
}
window.applyPagePermissions = applyPagePermissions;

// Hook labels, updateChrome, render, and refresh
Object.assign(labels, { admin: '账号与权限中心' });

const updateChromeBeforeAuth = updateChrome;
updateChrome = function authAwareUpdateChrome() {
  updateChromeBeforeAuth();
  if (state?.currentUser) {
    window.currentUser = state.currentUser;
    if (state.config?.deployMode) window.deployMode = state.config.deployMode;
    if (state.config?.authSettings) window.authSettings = { ...window.authSettings, ...state.config.authSettings };
    renderSidebarUser();
    applyNavPermissions();
  }
  const activeNav = route === 'admin' ? 'admin' : null;
  if (activeNav) {
    document.querySelectorAll('[data-nav]').forEach(a => {
      if (a.dataset.nav === 'admin') a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    $('#page-label').textContent = '账号与权限中心';
  }
};

function isRouteAllowed(head) {
  const routeChecks = {
    library: () => window.hasPerm('library.read'),
    institution: () => window.hasPerm('library.read'),
    asset: () => window.hasPerm('library.read'),
    'institution-new': () => window.hasPerm('library.create'),
    create: () => window.hasPerm('tasks.create'),
    tasks: () => window.hasPerm('tasks.read'),
    task: () => window.hasPerm('tasks.read'),
    works: () => window.hasPerm('works.read'),
    preview: () => window.hasPerm('works.read'),
    video: () => window.hasPerm('works.update'),
    publish: () => window.hasPerm('publish.read'),
    people: () => window.hasPerm('people.read'),
    styles: () => window.hasPerm('styles.read'),
    settings: () => (location.hash === '#settings/music' ? window.hasPerm('music.read') : window.hasPerm('settings.read')),
    admin: () => window.currentUser?.isSuperAdmin || window.hasAnyPerm('users.manage_subordinate', 'audit.read'),
  };
  return !routeChecks[head] || routeChecks[head]();
}

const renderBeforeAuth = render;
render = function authAwareRender() {
  if (!window.currentUser) return;
  const rawHash = (location.hash.slice(1) || firstAllowedRoute()).split('/');
  const head = rawHash[0] || firstAllowedRoute();

  if (head === 'admin') {
    route = 'admin';
    updateChrome();
    renderAdmin();
    return;
  }

  if (!isRouteAllowed(head)) {
    route = head;
    updateChrome();
    main.innerHTML = emptyState('暂无访问权限', '当前账号没有打开此页面的权限，如需开通请联系超级管理员。', `<a class="primary" href="#${E(firstAllowedRoute())}">返回可用页面</a>`);
    return;
  }

  renderBeforeAuth();
  applyPagePermissions();
};

// Re-apply in-page permissions whenever async paginated grids finish rendering
const permObserver = new MutationObserver(() => {
  if (window.currentUser && state) applyPagePermissions();
});
if (main) permObserver.observe(main, { childList: true, subtree: true });

// Global auth button click handler + click-outside & Escape closing for topbar user menu
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-auth-action]');
  if (!btn) {
    if (!e.target.closest('#topbar-user')) closeTopbarUserMenu();
    return;
  }
  const act = btn.dataset.authAction;
  if (act === 'toggle-user-menu') {
    const menu = $('#topbar-user-menu');
    const nextOpen = menu ? menu.hidden : false;
    if (menu) menu.hidden = !nextOpen;
    btn.setAttribute('aria-expanded', String(nextOpen));
  } else if (act === 'goto-admin') {
    closeTopbarUserMenu();
    setRoute('admin');
  } else if (act === 'goto-auth-settings') {
    closeTopbarUserMenu();
    setRoute('settings');
    requestAnimationFrame(() => $('#auth-settings-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  } else if (act === 'logout') {
    closeTopbarUserMenu();
    performLogout();
  } else if (act === 'change-own-pwd') {
    closeTopbarUserMenu();
    openOwnPasswordDialog($('#topbar-user-trigger') || btn);
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeTopbarUserMenu();
});

// Bootstrap entrypoint
async function bootAuth() {
  hydrateIcons();
  try {
    const status = await apiBeforeAuth('/api/auth/status');
    window.deployMode = status.deployMode || 'local';
    if (status.authSettings) window.authSettings = { ...window.authSettings, ...status.authSettings };
    if (!status.initialized) {
      showAuthGate('setup');
      return;
    }
    if (!status.authenticated || !status.user) {
      showAuthGate('login');
      return;
    }
    window.currentUser = status.user;
    if (status.user.mustChangePassword) {
      showAuthGate('force-password');
      return;
    }
    enterAuthenticatedApp(status.user);
  } catch (err) {
    showAuthGate('login', '暂时无法连接工作台服务：' + (err?.message || '请稍后刷新'));
  }
}

bootAuth();

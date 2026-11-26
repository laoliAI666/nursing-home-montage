'use strict';
// Account management, RBAC role permission matrix, audit logs & session expiry settings UI.

const adminState = {
  tab: 'users', // 'users' | 'roles' | 'audit'
  users: { items: [], total: 0, assignableRoles: [], q: '', role: '', status: '', loading: false },
  roles: { data: null, selected: 'operator', draftPerms: new Set(), dirty: false, loading: false },
  audit: { items: [], total: 0, page: 1, pageSize: 30, totalPages: 1, action: '', q: '', loading: false },
};

const PERM_KIND_LABELS = {
  read: '查',
  create: '增',
  update: '改',
  delete: '删',
  action: '关键动作',
};

function fmtDateTime(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return '—';
  }
}

function generateStrongPassword(len = 12) {
  const letters = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ';
  const digits = '23456789';
  const pool = letters + digits;
  const arr = new Uint32Array(len);
  crypto.getRandomValues(arr);
  let out = letters[arr[0] % letters.length] + digits[arr[1] % digits.length];
  for (let i = 2; i < len; i++) out += pool[arr[i] % pool.length];
  return out;
}

function defaultAdminTab() {
  if (window.hasPerm?.('users.manage_subordinate') || window.currentUser?.isSuperAdmin) return 'users';
  if (window.currentUser?.isSuperAdmin) return 'roles';
  if (window.hasPerm?.('audit.read')) return 'audit';
  return 'users';
}

function renderAdmin() {
  const parts = (location.hash.slice(1) || 'admin').split('/');
  const sub = parts[1];
  const allowedTabs = [];
  if (window.currentUser?.isSuperAdmin || window.hasPerm?.('users.manage_subordinate')) allowedTabs.push(['users', '团队账号']);
  if (window.currentUser?.isSuperAdmin) allowedTabs.push(['roles', '角色权限矩阵']);
  if (window.hasPerm?.('audit.read')) allowedTabs.push(['audit', '操作审计日志']);

  if (!allowedTabs.length) {
    main.innerHTML = emptyState('暂无访问权限', '当前账号没有管理团队账号、角色权限或查看审计日志的权限。', '<a class="secondary" href="#library">返回工作台</a>');
    return;
  }
  adminState.tab = allowedTabs.some(([id]) => id === sub) ? sub : allowedTabs[0][0];

  const subtitle = window.currentUser?.isSuperAdmin
    ? '管理团队账号、按页面与操作粒度分配角色权限，并追溯关键操作记录。'
    : '管理下级角色（运营）账号与查阅业务操作日志。';

  main.innerHTML = `
    ${heading('账号与权限中心', subtitle)}
    <nav class="admin-tabs" role="tablist" aria-label="权限中心分区">
      ${allowedTabs.map(([id, label]) => `<button type="button" role="tab" class="admin-tab ${adminState.tab === id ? 'is-active' : ''}" aria-selected="${adminState.tab === id}" data-admin-tab="${id}">${E(label)}</button>`).join('')}
    </nav>
    <div id="admin-panel"><p class="subtle">正在加载…</p></div>
  `;

  if (adminState.tab === 'users') loadAdminUsers();
  else if (adminState.tab === 'roles') loadAdminRoles();
  else if (adminState.tab === 'audit') loadAdminAudit();
}

// =========================================================================
// Tab 1: Team Users
// =========================================================================
async function loadAdminUsers() {
  adminState.users.loading = true;
  paintAdminUsers();
  try {
    const qs = new URLSearchParams({
      q: adminState.users.q || '',
      role: adminState.users.role || '',
      status: adminState.users.status || '',
    });
    const res = await api('/api/users?' + qs.toString());
    adminState.users.items = res.items || [];
    adminState.users.total = res.total || 0;
    adminState.users.assignableRoles = res.assignableRoles || [];
  } catch (err) {
    notify(err.message, true);
  } finally {
    adminState.users.loading = false;
    if (route === 'admin' && adminState.tab === 'users') paintAdminUsers();
  }
}

function paintAdminUsers() {
  const host = $('#admin-panel');
  if (!host) return;
  const st = adminState.users;
  const canCreate = (st.assignableRoles || []).length > 0;
  host.innerHTML = `
    <div class="admin-toolbar">
      <input type="search" class="input" id="admin-user-q" value="${E(st.q)}" placeholder="搜索用户名或姓名…">
      <select class="input" data-native-select id="admin-user-role" aria-label="按角色筛选">
        <option value="">全部角色</option>
        ${window.currentUser?.isSuperAdmin ? `<option value="super_admin" ${st.role === 'super_admin' ? 'selected' : ''}>超级管理员</option>` : ''}
        <option value="admin" ${st.role === 'admin' ? 'selected' : ''}>管理员</option>
        <option value="operator" ${st.role === 'operator' ? 'selected' : ''}>运营</option>
      </select>
      <select class="input" data-native-select id="admin-user-status" aria-label="按状态筛选">
        <option value="">全部状态</option>
        <option value="active" ${st.status === 'active' ? 'selected' : ''}>正常启用</option>
        <option value="disabled" ${st.status === 'disabled' ? 'selected' : ''}>已停用</option>
      </select>
      ${canCreate ? `<button type="button" class="primary" data-admin-action="new-user">${icon('plus')} 新建账号</button>` : ''}
    </div>
    <div class="admin-table-wrap">
      ${st.loading && !st.items.length ? '<p class="subtle" style="padding:22px">正在读取账号列表…</p>' : !st.items.length ? '<p class="subtle" style="padding:22px">没有匹配的团队账号。</p>' : `
        <table class="admin-table">
          <thead>
            <tr>
              <th>账号 / 姓名</th>
              <th>角色</th>
              <th>状态</th>
              <th>最近登录</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${st.items.map(u => `
              <tr>
                <td class="admin-user-cell">
                  <strong>${E(u.displayName)}${u.isSelf ? ' <span class="badge neutral">当前登录</span>' : ''}</strong>
                  <small>@${E(u.username)}${u.mustChangePassword ? ' · 待首次改密' : ''}</small>
                </td>
                <td>${badge(u.roleName, u.role === 'super_admin' ? '' : u.role === 'admin' ? 'warn' : 'neutral')}</td>
                <td>${u.status === 'active' ? badge('启用') : badge('已停用', 'error')}</td>
                <td>
                  <div>${fmtDateTime(u.lastLoginAt)}</div>
                  ${u.lastLoginIp ? `<small class="subtle">${E(u.lastLoginIp)}</small>` : ''}
                </td>
                <td>
                  <div class="admin-row-actions">
                    ${u.canManage ? `
                      <button type="button" class="text-button" data-admin-action="edit-user" data-id="${E(u.id)}">编辑</button>
                      <button type="button" class="text-button" data-admin-action="reset-pwd" data-id="${E(u.id)}">重置密码</button>
                      ${!u.isSelf ? `<button type="button" class="text-button" data-admin-action="toggle-user" data-id="${E(u.id)}" data-next="${u.status === 'active' ? 'disabled' : 'active'}">${u.status === 'active' ? '停用' : '启用'}</button>` : ''}
                      ${!u.isSelf ? `<button type="button" class="text-button danger" data-admin-action="delete-user" data-id="${E(u.id)}">删除</button>` : ''}
                    ` : '<span class="subtle">受保护</span>'}
                  </div>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `}
    </div>
  `;

  $('#admin-user-q')?.addEventListener('input', e => {
    adminState.users.q = e.target.value;
    debounceRun('admin-users', () => loadAdminUsers(), 250);
  });
  $('#admin-user-role')?.addEventListener('change', e => {
    adminState.users.role = e.target.value;
    loadAdminUsers();
  });
  $('#admin-user-status')?.addEventListener('change', e => {
    adminState.users.status = e.target.value;
    loadAdminUsers();
  });
}

function openCreateUserDialog(opener) {
  const roles = adminState.users.assignableRoles || [];
  const d = managementDialog('新建团队账号', opener);
  const body = d.querySelector('.management-dialog-content');
  const initPwd = generateStrongPassword(12);
  body.innerHTML = `
    <form id="create-user-form" class="auth-form">
      <div class="form-group">
        <label class="field-label" for="nu-username">登录用户名</label>
        <input id="nu-username" name="username" class="input" required minlength="3" maxlength="32" pattern="[A-Za-z][A-Za-z0-9_\\-]{2,31}" placeholder="例如 yunying_wang（字母开头）" autocomplete="off">
      </div>
      <div class="form-group">
        <label class="field-label" for="nu-name">显示姓名</label>
        <input id="nu-name" name="displayName" class="input" required maxlength="40" placeholder="例如 运营-小王">
      </div>
      <div class="form-group">
        <label class="field-label" for="nu-role">分配角色</label>
        <select id="nu-role" name="role" class="input" data-native-select>
          ${roles.map(r => `<option value="${E(r.code)}" ${r.code === 'operator' ? 'selected' : ''}>${E(r.name)}</option>`).join('')}
        </select>
      </div>
      <div class="form-group">
        <label class="field-label" for="nu-pwd">初始密码（至少 8 位含字母与数字）</label>
        <div class="pwd-gen-row">
          <input id="nu-pwd" name="password" class="input" type="text" required minlength="8" maxlength="128" value="${E(initPwd)}" autocomplete="new-password">
          <button type="button" class="secondary" id="nu-gen-pwd">随机生成</button>
        </div>
      </div>
      <label class="auth-remember-row" style="justify-content:flex-start">
        <input type="checkbox" name="mustChangePassword" checked>
        <span>首次登录要求该成员修改密码</span>
      </label>
      <p class="inline-error" role="alert"></p>
      <div class="auth-dialog-actions">
        <button type="button" class="secondary" data-close>取消</button>
        <button type="submit" class="primary">创建账号</button>
      </div>
    </form>
  `;
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('#nu-gen-pwd').onclick = () => { body.querySelector('#nu-pwd').value = generateStrongPassword(12); };
  body.querySelector('#create-user-form').onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = body.querySelector('[role="alert"]');
    errEl.textContent = '';
    await busy(f.querySelector('[type="submit"]'), async () => {
      try {
        await api('/api/users', {
          username: f.elements.username.value.trim(),
          displayName: f.elements.displayName.value.trim(),
          role: f.elements.role.value,
          password: f.elements.password.value,
          mustChangePassword: f.elements.mustChangePassword.checked,
        });
        d.close();
        notify('账号已创建，请将用户名与初始密码安全转交该成员');
        await loadAdminUsers();
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

function openEditUserDialog(user, opener) {
  const roles = adminState.users.assignableRoles || [];
  const d = managementDialog('编辑账号 · ' + user.username, opener);
  const body = d.querySelector('.management-dialog-content');
  const canChangeRole = !user.isSelf && roles.length > 0;
  body.innerHTML = `
    <form id="edit-user-form" class="auth-form">
      <div class="form-group">
        <label class="field-label" for="eu-name">显示姓名</label>
        <input id="eu-name" name="displayName" class="input" required maxlength="40" value="${E(user.displayName)}">
      </div>
      ${canChangeRole ? `
        <div class="form-group">
          <label class="field-label" for="eu-role">角色</label>
          <select id="eu-role" name="role" class="input" data-native-select>
            ${roles.map(r => `<option value="${E(r.code)}" ${r.code === user.role ? 'selected' : ''}>${E(r.name)}</option>`).join('')}
          </select>
        </div>
      ` : ''}
      <p class="inline-error" role="alert"></p>
      <div class="auth-dialog-actions">
        <button type="button" class="secondary" data-close>取消</button>
        <button type="submit" class="primary">保存修改</button>
      </div>
    </form>
  `;
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('#edit-user-form').onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = body.querySelector('[role="alert"]');
    errEl.textContent = '';
    await busy(f.querySelector('[type="submit"]'), async () => {
      try {
        const payload = { displayName: f.elements.displayName.value.trim() };
        if (f.elements.role) payload.role = f.elements.role.value;
        await api('/api/users/' + encodeURIComponent(user.id), payload);
        d.close();
        notify('账号信息已更新');
        await loadAdminUsers();
        if (user.isSelf) await window.refreshCurrentUser?.();
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

function openResetPwdDialog(user, opener) {
  const d = managementDialog('重置密码 · ' + user.displayName, opener);
  const body = d.querySelector('.management-dialog-content');
  const nextPwd = generateStrongPassword(12);
  body.innerHTML = `
    <form id="reset-pwd-form" class="auth-form">
      <p class="form-help" style="margin-bottom:14px">重置后将立即强制「${E(user.username)}」在所有已登录设备上退出。</p>
      <div class="form-group">
        <label class="field-label" for="rp-pwd">新密码（至少 8 位含字母与数字）</label>
        <div class="pwd-gen-row">
          <input id="rp-pwd" name="password" class="input" type="text" required minlength="8" maxlength="128" value="${E(nextPwd)}" autocomplete="new-password">
          <button type="button" class="secondary" id="rp-gen">随机生成</button>
        </div>
      </div>
      <label class="auth-remember-row" style="justify-content:flex-start">
        <input type="checkbox" name="mustChangePassword" checked>
        <span>下次登录强制要求修改密码</span>
      </label>
      <p class="inline-error" role="alert"></p>
      <div class="auth-dialog-actions">
        <button type="button" class="secondary" data-close>取消</button>
        <button type="submit" class="primary">确认重置</button>
      </div>
    </form>
  `;
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('#rp-gen').onclick = () => { body.querySelector('#rp-pwd').value = generateStrongPassword(12); };
  body.querySelector('#reset-pwd-form').onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    const errEl = body.querySelector('[role="alert"]');
    errEl.textContent = '';
    await busy(f.querySelector('[type="submit"]'), async () => {
      try {
        await api(`/api/users/${encodeURIComponent(user.id)}/reset-password`, {
          password: f.elements.password.value,
          mustChangePassword: f.elements.mustChangePassword.checked,
        });
        d.close();
        notify('密码已重置并踢出旧会话');
        await loadAdminUsers();
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

function openDeleteUserDialog(user, opener) {
  const d = managementDialog('删除团队账号', opener);
  const body = d.querySelector('.management-dialog-content');
  body.innerHTML = `
    <p>确认删除账号 <strong>${E(user.displayName)}（@${E(user.username)}）</strong>？</p>
    <p class="acceptance-note">该账号会被立即踢下线且无法再登录；其历史创建的制作任务、成品和审计日志会保留操作人姓名用于追溯。</p>
    <p class="inline-error" role="alert"></p>
    <div class="auth-dialog-actions">
      <button type="button" class="secondary" data-close>取消</button>
      <button type="button" class="danger-button" data-confirm>确认删除</button>
    </div>
  `;
  body.querySelector('[data-close]').onclick = () => d.close();
  body.querySelector('[data-confirm]').onclick = async e => {
    const errEl = body.querySelector('[role="alert"]');
    errEl.textContent = '';
    await busy(e.currentTarget, async () => {
      try {
        await api(`/api/users/${encodeURIComponent(user.id)}/delete`, {});
        d.close();
        notify('账号已删除');
        await loadAdminUsers();
      } catch (err) {
        errEl.textContent = err.message;
      }
    });
  };
}

// =========================================================================
// Tab 2: Role Permission Matrix (Super Admin only)
// =========================================================================
async function loadAdminRoles() {
  adminState.roles.loading = true;
  paintAdminRoles();
  try {
    const data = await api('/api/roles');
    adminState.roles.data = data;
    syncSelectedRoleDraft(adminState.roles.selected);
  } catch (err) {
    notify(err.message, true);
  } finally {
    adminState.roles.loading = false;
    if (route === 'admin' && adminState.tab === 'roles') paintAdminRoles();
  }
}

function syncSelectedRoleDraft(code) {
  const roles = adminState.roles.data?.roles || [];
  const chosen = roles.find(r => r.code === code) || roles.find(r => r.editable) || roles[0];
  if (!chosen) return;
  adminState.roles.selected = chosen.code;
  adminState.roles.draftPerms = new Set(chosen.permissions || []);
  adminState.roles.dirty = false;
}

function paintAdminRoles() {
  const host = $('#admin-panel');
  if (!host) return;
  const data = adminState.roles.data;
  if (!data) {
    host.innerHTML = '<p class="subtle">正在读取角色权限配置…</p>';
    return;
  }
  const roles = data.roles || [];
  const modules = data.modules || [];
  const superOnly = new Set(data.superOnlyPermissions || []);
  const current = roles.find(r => r.code === adminState.roles.selected) || roles[0];
  const editable = Boolean(current?.editable);
  const draft = adminState.roles.draftPerms;

  host.innerHTML = `
    <div class="role-cards" role="radiogroup" aria-label="选择要配置权限的角色">
      ${roles.map(r => `
        <button type="button" class="role-card ${r.code === current.code ? 'is-selected' : ''}" data-select-role="${E(r.code)}">
          <div class="role-card-head">
            <h3>${E(r.name)}</h3>
            ${r.editable ? badge('可按页面配置', 'neutral') : badge('全权限锁定')}
          </div>
          <p>${E(r.description)}</p>
          <div class="role-card-stats">
            <span>成员 ${r.userCount} 人</span>
            <span>已启权限 ${r.code === current.code ? draft.size : r.permissions.length} 项</span>
          </div>
        </button>
      `).join('')}
    </div>

    ${!editable ? '<p class="inline-note" style="margin-bottom:18px">超级管理员固定拥有工作台全部权限（含配置其他角色权限和全局设置），不可取消勾选。</p>' : ''}

    <div class="perm-modules">
      ${modules.map(mod => {
        const assignable = mod.permissions.filter(p => !superOnly.has(p.code));
        const allChecked = assignable.length > 0 && assignable.every(p => draft.has(p.code));
        return `
          <section class="perm-module-card">
            <div class="perm-module-head">
              <div>
                <h3>${E(mod.label)}</h3>
                <p>${E(mod.description)}</p>
              </div>
              ${editable && assignable.length ? `
                <button type="button" class="text-button" data-toggle-module="${E(mod.id)}">${allChecked ? '清空本页' : '全选本页'}</button>
              ` : ''}
            </div>
            <div class="perm-list">
              ${mod.permissions.map(p => {
                const locked = !editable || superOnly.has(p.code);
                const checked = current.code === 'super_admin' ? true : (!superOnly.has(p.code) && draft.has(p.code));
                return `
                  <label class="perm-item ${locked ? 'is-locked' : ''}">
                    <input type="checkbox" data-perm-code="${E(p.code)}" ${checked ? 'checked' : ''} ${locked ? 'disabled' : ''}>
                    <div class="perm-item-body">
                      <div class="perm-item-title">
                        <span class="perm-kind-tag kind-${E(p.kind)}">${E(PERM_KIND_LABELS[p.kind] || p.kind)}</span>
                        <span>${E(p.label)}</span>
                        ${superOnly.has(p.code) ? '<span class="perm-lock-note">· 仅超管专属</span>' : ''}
                      </div>
                      <p class="perm-item-desc">${E(p.description)}</p>
                    </div>
                  </label>
                `;
              }).join('')}
            </div>
          </section>
        `;
      }).join('')}
    </div>

    ${editable ? `
      <div class="perm-sticky-bar">
        <div>
          <strong>正在编辑：${E(current.name)}</strong>
          <p class="form-help" style="margin:2px 0 0">已勾选 ${draft.size} 项权限；保存后对该角色下的所有成员立即生效。</p>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button type="button" class="secondary" data-admin-action="reset-role-perms">恢复默认推荐权限</button>
          <button type="button" class="primary" data-admin-action="save-role-perms" ${adminState.roles.dirty ? '' : ''}>${icon('save')} 保存权限配置</button>
        </div>
      </div>
    ` : ''}
  `;
}

// =========================================================================
// Tab 3: Audit Logs
// =========================================================================
async function loadAdminAudit(resetPage = false) {
  if (resetPage) adminState.audit.page = 1;
  adminState.audit.loading = true;
  paintAdminAudit();
  try {
    const qs = new URLSearchParams({
      page: String(adminState.audit.page),
      pageSize: String(adminState.audit.pageSize),
      action: adminState.audit.action || '',
      q: adminState.audit.q || '',
    });
    const res = await api('/api/audit-logs?' + qs.toString());
    Object.assign(adminState.audit, {
      items: res.items || [],
      total: res.total || 0,
      page: res.page || 1,
      totalPages: res.totalPages || 1,
    });
  } catch (err) {
    notify(err.message, true);
  } finally {
    adminState.audit.loading = false;
    if (route === 'admin' && adminState.tab === 'audit') paintAdminAudit();
  }
}

function paintAdminAudit() {
  const host = $('#admin-panel');
  if (!host) return;
  const st = adminState.audit;
  host.innerHTML = `
    <div class="admin-toolbar">
      <input type="search" class="input" id="admin-audit-q" value="${E(st.q)}" placeholder="搜索操作人、内容摘要…">
      <select class="input" data-native-select id="admin-audit-action" aria-label="按操作类型筛选">
        <option value="">全部操作类型</option>
        <option value="auth." ${st.action === 'auth.' ? 'selected' : ''}>登录与密码</option>
        <option value="library." ${st.action === 'library.' ? 'selected' : ''}>机构资料库</option>
        <option value="task." ${st.action === 'task.' ? 'selected' : ''}>制作任务与验收</option>
        <option value="works." ${st.action === 'works.' ? 'selected' : ''}>成品库</option>
        <option value="publish." ${st.action === 'publish.' ? 'selected' : ''}>多平台发布</option>
        <option value="people." ${st.action === 'people.' ? 'selected' : ''}>数字人</option>
        <option value="settings." ${st.action === 'settings.' ? 'selected' : ''}>全局设置</option>
        <option value="credential." ${st.action === 'credential.' ? 'selected' : ''}>密钥查看</option>
        <option value="user." ${st.action === 'user.' ? 'selected' : ''}>账号管理</option>
        <option value="role." ${st.action === 'role.' ? 'selected' : ''}>权限变更</option>
      </select>
      <button type="button" class="secondary" data-admin-action="refresh-audit">${icon('retry')} 刷新</button>
    </div>
    <div class="admin-table-wrap">
      ${!st.items.length ? '<p class="subtle" style="padding:22px">暂无匹配的操作记录。</p>' : `
        <table class="admin-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>动作</th>
              <th>内容摘要</th>
              <th>来源 IP</th>
            </tr>
          </thead>
          <tbody>
            ${st.items.map(row => `
              <tr>
                <td style="white-space:nowrap;font-variant-numeric:tabular-nums">${fmtDateTime(row.createdAt)}</td>
                <td style="white-space:nowrap"><strong>${E(row.displayName || row.username || '系统')}</strong></td>
                <td><code style="font-size:11px">${E(row.action)}</code></td>
                <td>${E(row.summary)}</td>
                <td style="white-space:nowrap;color:var(--muted)">${E(row.ip || '—')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `}
    </div>
    ${st.total > st.pageSize ? `
      <div class="pagination-bar" style="margin-top:16px">
        <span class="pagination-meta">共 <strong>${st.total}</strong> 条 · 第 <strong>${st.page}</strong> / ${st.totalPages} 页</span>
        <div class="pagination-controls">
          <button type="button" class="secondary pagination-btn" data-audit-page="${st.page - 1}" ${st.page <= 1 ? 'disabled' : ''}>上一页</button>
          <button type="button" class="secondary pagination-btn" data-audit-page="${st.page + 1}" ${st.page >= st.totalPages ? 'disabled' : ''}>下一页</button>
        </div>
      </div>
    ` : ''}
  `;
  $('#admin-audit-q')?.addEventListener('input', e => {
    adminState.audit.q = e.target.value;
    debounceRun('admin-audit', () => loadAdminAudit(true), 250);
  });
  $('#admin-audit-action')?.addEventListener('change', e => {
    adminState.audit.action = e.target.value;
    loadAdminAudit(true);
  });
}

// =========================================================================
// Settings Panel Extension: Session Auto-Login & Expiry Configuration
// =========================================================================
window.mountAuthSettingsPanel = function mountAuthSettingsPanel(form) {
  if (!form || !state?.config?.authSettings || form.querySelector('#auth-settings-panel')) return;
  const s = state.config.authSettings;
  const presets = [1, 7, 14, 30, 90];
  const html = `
    <section class="panel" id="auth-settings-panel">
      <div class="settings-section-heading"><span>${icon('shield')}</span><div><h2>登录与会话过期设置</h2><p>控制本机与云端工作台的自动登录保持时长，以及超管查看密钥的二次认证窗口。</p></div></div>
      <div class="switch-row" style="margin-bottom:16px">
        <label for="auth-allow-auto-login">允许记住登录状态（上次登录过，过期前自动登录）</label>
        <input type="checkbox" class="switch" id="auth-allow-auto-login" name="auth_allow_auto_login" ${s.allowAutoLogin !== false ? 'checked' : ''}>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="field-label" for="auth-session-days">自动登录过期时间（天）</label>
          <input class="input" id="auth-session-days" name="auth_session_ttl_days" type="number" min="1" max="365" step="1" value="${E(s.sessionTtlDays ?? 7)}">
          <div class="ttl-presets">
            ${presets.map(d => `<button type="button" class="ttl-preset-btn ${Number(s.sessionTtlDays) === d ? 'is-active' : ''}" data-ttl-days="${d}">${d} 天</button>`).join('')}
          </div>
          <p class="form-help">持续使用期间会自动顺延；超过设置天数未访问或退出登录后需重新输入密码。</p>
        </div>
        <div class="form-group">
          <label class="field-label" for="auth-reauth-sec">密钥查看二次认证有效时长（秒）</label>
          <input class="input" id="auth-reauth-sec" name="auth_reauth_window_seconds" type="number" min="60" max="1800" step="30" value="${E(s.reauthWindowSeconds ?? 300)}">
          <p class="form-help">超管点击密钥小眼睛输入一次密码后，在该时间内查看其他密钥无需重复输入。</p>
        </div>
      </div>
    </section>
  `;
  const actions = form.querySelector('.sticky-actions');
  if (actions) actions.insertAdjacentHTML('beforebegin', html);
  else form.insertAdjacentHTML('beforeend', html);

  form.querySelectorAll('[data-ttl-days]').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = form.querySelector('#auth-session-days');
      if (input) input.value = btn.dataset.ttlDays;
      form.querySelectorAll('[data-ttl-days]').forEach(b => b.classList.toggle('is-active', b === btn));
    });
  });
};

window.authSettingsFields = function authSettingsFields(form) {
  if (!form || !form.querySelector('#auth-settings-panel')) return undefined;
  return {
    allowAutoLogin: Boolean(form.elements.auth_allow_auto_login?.checked),
    sessionTtlDays: Number(form.elements.auth_session_ttl_days?.value || 7),
    reauthWindowSeconds: Number(form.elements.auth_reauth_window_seconds?.value || 300),
  };
};

// Delegated event listeners for Admin Center
document.addEventListener('click', async e => {
  const tabBtn = e.target.closest('[data-admin-tab]');
  if (tabBtn) {
    setRoute('admin/' + tabBtn.dataset.adminTab);
    return;
  }
  const roleBtn = e.target.closest('[data-select-role]');
  if (roleBtn) {
    syncSelectedRoleDraft(roleBtn.dataset.selectRole);
    paintAdminRoles();
    return;
  }
  const modBtn = e.target.closest('[data-toggle-module]');
  if (modBtn) {
    const data = adminState.roles.data;
    const mod = (data?.modules || []).find(m => m.id === modBtn.dataset.toggleModule);
    const superOnly = new Set(data?.superOnlyPermissions || []);
    if (!mod) return;
    const assignable = mod.permissions.filter(p => !superOnly.has(p.code)).map(p => p.code);
    const allChecked = assignable.every(c => adminState.roles.draftPerms.has(c));
    assignable.forEach(c => allChecked ? adminState.roles.draftPerms.delete(c) : adminState.roles.draftPerms.add(c));
    adminState.roles.dirty = true;
    paintAdminRoles();
    return;
  }
  const auditPage = e.target.closest('[data-audit-page]');
  if (auditPage && !auditPage.disabled) {
    adminState.audit.page = Number(auditPage.dataset.auditPage);
    loadAdminAudit();
    return;
  }
  const actBtn = e.target.closest('[data-admin-action]');
  if (!actBtn) return;
  const action = actBtn.dataset.adminAction;
  if (action === 'new-user') { openCreateUserDialog(actBtn); return; }
  if (action === 'edit-user') {
    const u = adminState.users.items.find(x => x.id === actBtn.dataset.id);
    if (u) openEditUserDialog(u, actBtn);
    return;
  }
  if (action === 'reset-pwd') {
    const u = adminState.users.items.find(x => x.id === actBtn.dataset.id);
    if (u) openResetPwdDialog(u, actBtn);
    return;
  }
  if (action === 'delete-user') {
    const u = adminState.users.items.find(x => x.id === actBtn.dataset.id);
    if (u) openDeleteUserDialog(u, actBtn);
    return;
  }
  if (action === 'toggle-user') {
    const next = actBtn.dataset.next;
    await busy(actBtn, async () => {
      try {
        await api('/api/users/' + encodeURIComponent(actBtn.dataset.id), { status: next });
        notify(next === 'active' ? '账号已重新启用' : '账号已停用并强制下线');
        await loadAdminUsers();
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'save-role-perms') {
    await busy(actBtn, async () => {
      try {
        const res = await api(`/api/roles/${encodeURIComponent(adminState.roles.selected)}/permissions`, {
          permissions: [...adminState.roles.draftPerms],
        });
        adminState.roles.data = res;
        syncSelectedRoleDraft(adminState.roles.selected);
        paintAdminRoles();
        notify('角色权限已保存，对该角色成员立即生效');
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'reset-role-perms') {
    await busy(actBtn, async () => {
      try {
        const res = await api(`/api/roles/${encodeURIComponent(adminState.roles.selected)}/reset`, {});
        adminState.roles.data = res;
        syncSelectedRoleDraft(adminState.roles.selected);
        paintAdminRoles();
        notify('已恢复该角色的默认推荐权限');
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'refresh-audit') {
    await loadAdminAudit();
  }
});

document.addEventListener('change', e => {
  const cb = e.target.closest('input[data-perm-code]');
  if (!cb || cb.disabled) return;
  const code = cb.dataset.permCode;
  if (cb.checked) adminState.roles.draftPerms.add(code);
  else adminState.roles.draftPerms.delete(code);
  adminState.roles.dirty = true;
  paintAdminRoles();
});

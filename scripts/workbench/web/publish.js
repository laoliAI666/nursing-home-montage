'use strict';
// Multi-platform publishing (Yixiaoer CLI): settings, digital-human bindings, accepted-work selection, publish queue & statuses.

paths.send = '<path d="M22 2 11 13"/><path d="M22 2 15 22 11 13 2 9 22 2z"/>';
labels.publish = '发布任务';

const PUBLISH_PLATFORMS = [
  { key: 'xiaohongshu', label: '小红书', idField: 'xiaohongshu_account_id' },
  { key: 'douyin', label: '抖音', idField: 'douyin_account_id' },
  { key: 'channels', label: '视频号', idField: 'channels_account_id' },
];
const PUBLISH_ACTIVE_STATUSES = ['queued', 'checking', 'uploading', 'validating', 'submitting', 'polling'];
const PUBLISH_STATUS_TEXT = {
  queued: '排队中',
  checking: '校验账号',
  uploading: '上传素材',
  validating: '参数校验',
  submitting: '提交发布',
  polling: '等待平台确认',
  published: '已发布',
  failed: '发布失败',
  partial: '部分已发布',
  running: '发布中',
};

let publishFilter = 'all';
let workPublishFilter = 'all';
const publishSelection = new Map(); // key = productKey -> {taskId, index, version, title}

function getPublishState() {
  return state?.publish || {
    settings: {
      provider: 'yixiaoer',
      channel: 'local',
      clientId: '',
      autoFallbackLocal: false,
      visibility: 'private',
      aiDeclaration: true,
      concurrency: 2,
      apiKeyConfigured: false,
      cliInstalled: false,
    },
    accounts: { syncedAt: null, version: '', items: [], summary: { total: 0, online: 0, expired: 0, byPlatform: {} } },
    batches: [],
    productStatuses: {},
    activeCount: 0,
  };
}

function makeProductKey(taskId, index, version = 'original') {
  return `${taskId}:${Number(index)}:${version || 'original'}`;
}

function productPublishInfo(taskId, index, version = 'original') {
  const map = getPublishState().productStatuses || {};
  const exact = map[makeProductKey(taskId, index, version)];
  if (exact) return exact;
  const prefix = `${taskId}:${Number(index)}:`;
  const fallbackKey = Object.keys(map).find(k => k.startsWith(prefix));
  return fallbackKey ? map[fallbackKey] : null;
}

function publishChipClass(status) {
  if (status === 'published') return 'is-published';
  if (status === 'failed') return 'is-failed';
  if (PUBLISH_ACTIVE_STATUSES.includes(status) || status === 'publishing' || status === 'running' || status === 'partial') return 'is-active';
  return '';
}

function renderProductPublishBadges(info, accepted) {
  if (!accepted) {
    return '<div class="work-publish-status-row"><span class="publish-chip">待验收 · 验收后可发布</span></div>';
  }
  if (!info || info.overall === 'unpublished') {
    return '<div class="work-publish-status-row"><span class="publish-chip">未发布</span></div>';
  }
  const overallLabel = info.overall === 'published'
    ? '已发布'
    : info.overall === 'publishing'
      ? `发布中（${info.publishedCount}/${info.totalPlatforms}）`
      : info.overall === 'partial'
        ? `部分发布（${info.publishedCount}/${info.totalPlatforms}）`
        : '发布失败';
  const chips = Object.values(info.platforms || {}).map(p => {
    const suffix = p.status === 'published' ? ' ✓' : p.status === 'failed' ? ' 失败' : ' 发布中';
    const vis = p.visibility === 'private' ? '·私密' : '';
    return `<a href="#publish" class="publish-chip is-link ${publishChipClass(p.status)}" title="${E(p.accountName ? p.accountName + ' · ' + p.message : p.message)}">${E(p.platformName)}${vis}${suffix}</a>`;
  }).join('');
  return `<div class="work-publish-status-row"><span class="publish-chip ${publishChipClass(info.overall)}">${E(overallLabel)}</span>${chips}</div>`;
}

function updatePublishNavBadge() {
  const el = $('#publish-count');
  if (el) el.textContent = String(getPublishState().activeCount || 0);
}

// =========================================================================
// 1. Settings Page — Yixiaoer configuration card
// =========================================================================
function publishSettingsCardHtml() {
  const pub = getPublishState();
  const s = pub.settings || {};
  const acc = pub.accounts || {};
  const summary = acc.summary || { total: 0, online: 0, expired: 0, byPlatform: {} };
  const isLocal = (s.channel || 'local') === 'local';
  const cliNote = s.cliInstalled
    ? `本机已检测到 yxer CLI${acc.version ? ' v' + E(acc.version) : ''}`
    : '本机尚未安装 yxer CLI，请先在终端运行：npm install -g @yixiaoermail/cli';

  const platformSummaryHtml = summary.total
    ? `<div class="publish-account-summary">${PUBLISH_PLATFORMS.map(p => {
        const row = summary.byPlatform?.[p.key] || { total: 0, online: 0, expired: 0 };
        const cls = row.online > 0 ? 'is-published' : row.total > 0 ? 'is-failed' : '';
        return `<span class="publish-chip ${cls}">${p.label}：${row.online} 在线 / 共 ${row.total} 个</span>`;
      }).join('')}${acc.syncedAt ? `<span class="publish-chip">最近同步 ${dateText(acc.syncedAt)}</span>` : ''}</div>`
    : '<p class="form-help" style="margin-top:10px">保存 API Key 后点击「检测连接并同步账号」，即可在数字人资料中直接下拉绑定小红书、抖音、视频号账号。</p>';

  return `<section class="panel" id="publish-settings-section">
    <div class="settings-section-heading"><span>${icon('send')}</span><div><h2>多平台分发（蚁小二）</h2><p>配置蚁小二 CLI 凭证与默认发布策略，用于将已验收成品一键发布到小红书、抖音和视频号。</p></div></div>
    <div class="publish-settings-status">
      <div class="publish-settings-status-text">
        <strong>${s.apiKeyConfigured ? '已配置蚁小二 API Key' : '尚未配置蚁小二 API Key'}</strong>
        <small>${cliNote}</small>
      </div>
      <button type="button" class="secondary" id="publish-test-sync-btn">${icon('retry')} 检测连接并同步账号</button>
    </div>
    <div class="form-row">
      <div class="form-group">
        <div class="label-row"><label for="key-yixiaoer_api_key">蚁小二 API Key</label>${s.apiKeyConfigured ? '<span class="saved-mark">已保存</span>' : '<span class="optional">必填</span>'}</div>
        <div class="input-wrap">
          <input class="input" type="password" id="key-yixiaoer_api_key" name="yixiaoer_api_key" autocomplete="new-password" spellcheck="false" placeholder="${s.apiKeyConfigured ? '已配置 · 留空保留原值' : '粘贴蚁小二开放平台 / CLI API Key'}">
          <button type="button" class="reveal" data-action="reveal" aria-label="显示蚁小二 API Key" aria-pressed="false">${icon('eye')}</button>
        </div>
        <p class="form-help">密钥仅加密级权限保存在本地 .montage/secrets.json，不会写入公开配置或日志。</p>
      </div>
      <div class="form-group">
        <label class="field-label" for="publish-client-id">本机 ClientID<span class="optional" id="publish-client-id-tag">${isLocal ? '本机发布必填' : '选填'}</span></label>
        <input class="input" id="publish-client-id" name="publish_client_id" maxlength="120" value="${E(s.clientId || '')}" placeholder="从本机蚁小二桌面客户端复制 ClientID">
        <p class="form-help">未配置云端代理时使用本机客户端发布，发布期间请保持蚁小二桌面客户端在线。</p>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label class="field-label" for="publish-channel">默认发布通道</label>
        <select id="publish-channel" name="publish_channel">
          <option value="local" ${isLocal ? 'selected' : ''}>本机客户端发布（推荐 · 无需云端代理）</option>
          <option value="cloud" ${!isLocal ? 'selected' : ''}>云发布（需在蚁小二为账号配置代理）</option>
        </select>
      </div>
      <div class="form-group">
        <label class="field-label" for="publish-visibility">默认发布可见性</label>
        <select id="publish-visibility" name="publish_visibility">
          <option value="private" ${(s.visibility || 'private') === 'private' ? 'selected' : ''}>私密发布（推荐首测：抖音/小红书仅自己可见、视频号存草稿）</option>
          <option value="public" ${s.visibility === 'public' ? 'selected' : ''}>公开发布</option>
        </select>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label class="field-label" for="publish-concurrency">发布并发路数</label>
        <select id="publish-concurrency" name="publish_concurrency">
          ${[1, 2, 3, 4].map(n => `<option value="${n}" ${Number(s.concurrency || 2) === n ? 'selected' : ''}>${n} 路并发${n === 2 ? '（推荐）' : ''}</option>`).join('')}
        </select>
        <p class="form-help">同一平台账号会自动串行排队，避免会话互踢。</p>
      </div>
      <div class="form-group" style="display:grid;align-content:center;gap:10px">
        <div class="switch-row" style="margin:0"><label for="publish-ai-decl">自动附带 AI 合成内容声明</label><input type="checkbox" class="switch" id="publish-ai-decl" name="publish_ai_declaration" ${s.aiDeclaration !== false ? 'checked' : ''}></div>
        <div class="switch-row" id="publish-fallback-row" style="margin:0" ${isLocal ? 'hidden' : ''}><label for="publish-auto-fallback">云发布失败时自动回退本机</label><input type="checkbox" class="switch" id="publish-auto-fallback" name="publish_auto_fallback_local" ${s.autoFallbackLocal ? 'checked' : ''}></div>
      </div>
    </div>
    <div id="publish-sync-feedback" role="status">${platformSummaryHtml}</div>
  </section>`;
}

window.publishSettingsFields = function (form) {
  if (!form || !form.elements.publish_channel) return undefined;
  return {
    apiKey: form.elements.yixiaoer_api_key?.value || '',
    channel: form.elements.publish_channel.value,
    clientId: form.elements.publish_client_id?.value || '',
    visibility: form.elements.publish_visibility?.value || 'private',
    concurrency: Number(form.elements.publish_concurrency?.value || 2),
    aiDeclaration: Boolean(form.elements.publish_ai_declaration?.checked),
    autoFallbackLocal: Boolean(form.elements.publish_auto_fallback_local?.checked),
  };
};

function bindPublishSettingsCard() {
  const sec = $('#publish-settings-section');
  if (!sec) return;
  const chSel = $('#publish-channel');
  const tag = $('#publish-client-id-tag');
  const fbRow = $('#publish-fallback-row');
  const syncChannelUi = () => {
    const local = chSel.value === 'local';
    if (tag) tag.textContent = local ? '本机发布必填' : '选填';
    if (fbRow) fbRow.hidden = local;
  };
  chSel?.addEventListener('change', syncChannelUi);
  syncChannelUi();

  $('#publish-test-sync-btn')?.addEventListener('click', async e => {
    const btn = e.currentTarget;
    const form = $('#settings-form');
    const feedback = $('#publish-sync-feedback');
    await busy(btn, async () => {
      try {
        const fields = window.publishSettingsFields(form);
        // Save non-empty settings first if user typed a key/clientId, or test directly with overrides
        const res = await api('/api/publish/test', {
          apiKey: fields.apiKey,
          channel: fields.channel,
          clientId: fields.clientId,
          autoFallbackLocal: fields.autoFallbackLocal,
        });
        if (!res.ok) {
          feedback.innerHTML = `<p class="inline-error">${E(res.doctor?.message || '请先填写蚁小二 API Key')}</p>`;
          notify(res.doctor?.message || '请先填写蚁小二 API Key', true);
          return;
        }
        if (fields.apiKey || fields.clientId) {
          await api('/api/publish/settings', fields);
          if (form.elements.yixiaoer_api_key) form.elements.yixiaoer_api_key.value = '';
        }
        await refresh(false);
        const s = res.accounts?.summary || { total: 0, online: 0 };
        const sugNote = res.suggestions?.length ? ` · 发现 ${res.suggestions.length} 条可自动匹配的数字人账号` : '';
        feedback.innerHTML = `<p class="saved-mark">连接成功（yxer ${E(res.doctor?.version || '')}）：共同步 ${s.total} 个账号（${s.online} 个在线）${E(sugNote)}</p>`;
        notify(`已同步 ${s.total} 个蚁小二账号（${s.online} 个在线）`);
        if (route === 'settings') renderSettings();
      } catch (err) {
        feedback.innerHTML = `<p class="inline-error">${E(err.message)}</p>`;
        notify(err.message, true);
      }
    });
  });
}

const settingsBeforePublish = renderSettings;
renderSettings = function () {
  settingsBeforePublish();
  const prefPanel = $('#cover-header')?.closest('section.panel');
  if (prefPanel && !$('#publish-settings-section')) {
    prefPanel.insertAdjacentHTML('beforebegin', publishSettingsCardHtml());
    bindPublishSettingsCard();
  }
};

// =========================================================================
// 2. Digital Human Editor — Yixiaoer account dropdown binding
// =========================================================================
window.mountPersonPublishBindings = function (form, person) {
  const mod = form?.querySelector('#person-module-platforms');
  if (!mod) return;

  const currentBindings = { ...(person?.platform_bindings || {}) };
  const currentManual = {
    xiaohongshu: person?.xiaohongshu_account_id ?? person?.platform_accounts?.xiaohongshu ?? '',
    douyin: person?.douyin_account_id ?? person?.platform_accounts?.douyin ?? '',
    channels: person?.channels_account_id ?? person?.platform_accounts?.channels ?? '',
  };

  const paintModule = () => {
    const pub = getPublishState();
    const accounts = pub.accounts?.items || [];
    const syncedAt = pub.accounts?.syncedAt;
    const configured = pub.settings?.apiKeyConfigured;

    // Count potential auto-matches for this person
    const autoMatches = {};
    for (const p of PUBLISH_PLATFORMS) {
      if (currentBindings[p.key]?.accountId) continue;
      const m = (currentManual[p.key] || '').trim();
      if (!m) continue;
      const hit = accounts.find(a => a.platform === p.key && (a.id === m || a.authorId === m || a.name === m));
      if (hit) autoMatches[p.key] = hit;
    }
    const autoMatchCount = Object.keys(autoMatches).length;

    const head = mod.querySelector('.person-module-head')?.outerHTML || '';
    const slotsHtml = PUBLISH_PLATFORMS.map(p => {
      const platAccounts = accounts.filter(a => a.platform === p.key);
      const bound = currentBindings[p.key];
      const live = bound ? platAccounts.find(a => a.id === bound.accountId) : null;
      const statusBadge = bound
        ? (live
            ? (live.online ? '<span class="publish-chip is-published">在线 · 可发布</span>' : '<span class="publish-chip is-failed">登录已过期 · 请去蚁小二扫码</span>')
            : (accounts.length ? '<span class="publish-chip is-failed">未在同步列表中</span>' : '<span class="publish-chip">已绑定</span>'))
        : '<span class="publish-chip">未绑定</span>';

      const missingOption = bound && !live
        ? `<option value="${E(bound.accountId)}" selected>${E(bound.accountName || bound.accountId)}（当前未在同步列表中）</option>`
        : '';

      const optionsHtml = `<option value="">不绑定${p.label}账号</option>` +
        platAccounts.map(a => {
          const tag = a.online ? '在线' : a.statusLabel || '已过期';
          const uid = a.authorId ? ` · ID:${a.authorId}` : '';
          return `<option value="${E(a.id)}" ${bound?.accountId === a.id ? 'selected' : ''}>${E(a.name)}（${E(tag)}${E(uid)}）</option>`;
        }).join('') + missingOption;

      const hiddenVal = bound ? JSON.stringify(bound) : '';
      return `<div class="platform-binding-slot" data-platform-slot="${p.key}">
        <div class="platform-binding-head">
          <strong>${p.label}</strong>
          ${statusBadge}
        </div>
        <select class="input" data-native-select data-binding-select="${p.key}" aria-label="选择${p.label}蚁小二账号">${optionsHtml}</select>
        <input type="hidden" name="${p.key}_binding" value="${E(hiddenVal)}">
        <details class="platform-manual-details" ${!bound && currentManual[p.key] ? 'open' : ''}>
          <summary>平台原始账号 ID（备注/兼容）</summary>
          <input class="input" id="person-${p.key}-id" name="${p.idField}" maxlength="100" value="${E(currentManual[p.key])}" placeholder="选填，下拉绑定后自动回填">
        </details>
      </div>`;
    }).join('');

    mod.innerHTML = `${head}
      <div class="person-sync-bar">
        <span>${configured
          ? (syncedAt ? `已同步 ${accounts.length} 个蚁小二账号 · 更新于 ${dateText(syncedAt)}` : '已配置蚁小二，点击右侧同步账号列表即可下拉绑定。')
          : '尚未在设置页配置蚁小二 API Key；配置并同步后可直接下拉绑定账号。'}</span>
        <div class="person-sync-actions">
          ${autoMatchCount ? `<button type="button" class="secondary" data-action="auto-match-person-bindings">自动匹配已有 ID（${autoMatchCount}）</button>` : ''}
          ${configured
            ? `<button type="button" class="secondary" data-action="sync-person-publish-accounts">${icon('retry')} 从蚁小二同步账号</button>`
            : '<a class="text-button" href="#settings">去设置蚁小二 →</a>'}
        </div>
      </div>
      <div class="platform-accounts-grid">${slotsHtml}</div>`;

    mod.querySelectorAll('[data-binding-select]').forEach(sel => {
      sel.addEventListener('change', () => {
        const pkey = sel.dataset.bindingSelect;
        const aid = sel.value;
        if (!aid) {
          delete currentBindings[pkey];
        } else {
          const hit = accounts.find(a => a.id === aid);
          currentBindings[pkey] = hit
            ? { provider: 'yixiaoer', accountId: hit.id, authorId: hit.authorId || '', accountName: hit.name || '' }
            : { provider: 'yixiaoer', accountId: aid, authorId: '', accountName: '' };
          if (hit) {
            currentManual[pkey] = hit.authorId || hit.id;
          }
        }
        paintModule();
      });
    });

    mod.querySelectorAll('input[name$="_account_id"]').forEach(inp => {
      inp.addEventListener('input', () => {
        const plat = PUBLISH_PLATFORMS.find(p => p.idField === inp.name);
        if (plat) currentManual[plat.key] = inp.value;
      });
    });

    mod.querySelector('[data-action="auto-match-person-bindings"]')?.addEventListener('click', () => {
      for (const [pkey, hit] of Object.entries(autoMatches)) {
        currentBindings[pkey] = { provider: 'yixiaoer', accountId: hit.id, authorId: hit.authorId || '', accountName: hit.name || '' };
        currentManual[pkey] = hit.authorId || hit.id;
      }
      paintModule();
      notify(`已自动匹配 ${autoMatchCount} 个平台账号，请点击底部「保存人物资料」生效`);
    });

    mod.querySelector('[data-action="sync-person-publish-accounts"]')?.addEventListener('click', async e => {
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/publish/sync-accounts', {});
          await refresh(false);
          paintModule();
          const s = res.accounts?.summary || { total: 0, online: 0 };
          notify(`已同步 ${s.total} 个蚁小二账号（${s.online} 个在线）`);
        } catch (err) {
          notify(err.message, true);
        }
      });
    });
  };

  paintModule();
};

// =========================================================================
// 3. Works Library (#works) — Accepted-only selection, filter & badges
// =========================================================================
function collectWorksCatalog(institutionFilter = '') {
  const archivedIds = new Set((state.archives || []).map(a => a.id));
  const items = [];

  for (const t of state.tasks || []) {
    if (archivedIds.has(t.id)) continue;
    if (institutionFilter && t.institutionId !== institutionFilter) continue;
    const accepted = t.status === 'accepted';
    for (const r of (t.results || []).filter(productCardVisible)) {
      const version = r.version || 'original';
      const key = makeProductKey(t.id, r.index, version);
      const pubInfo = productPublishInfo(t.id, r.index, version);
      items.push({
        source: 'task',
        accepted,
        deletable: !r.deletionPending,
        key,
        taskId: t.id,
        index: r.index,
        version,
        title: r.title || t.title,
        institutionId: t.institutionId || '',
        institutionName: t.institutionName || '未关联机构',
        personName: r.personName || t.personName || '',
        updated: t.updated,
        cover: r.cover,
        packageUrl: r.package,
        previewUrl: `#preview/${t.id}/${r.index}`,
        task: t,
        result: r,
        pubInfo,
      });
    }
  }

  for (const a of state.archives || []) {
    if (institutionFilter && a.institutionId !== institutionFilter) continue;
    for (const r of a.results || []) {
      const version = r.version || 'original';
      const key = makeProductKey(a.id, r.index, version);
      const pubInfo = productPublishInfo(a.id, r.index, version);
      items.push({
        source: 'archive',
        accepted: true,
        deletable: true,
        key,
        taskId: a.id,
        index: r.index,
        version,
        title: r.title || a.title,
        institutionId: a.institutionId || '',
        institutionName: a.institutionName || '未关联机构',
        personName: r.personName || a.personName || '',
        updated: a.updated,
        cover: r.cover,
        packageUrl: r.package,
        previewUrl: `#preview/archive/${a.id}/${r.index}/${r.version}`,
        archive: a,
        result: r,
        pubInfo,
      });
    }
  }

  return items;
}

function matchesWorkPublishFilter(item) {
  if (workPublishFilter === 'all') return true;
  const overall = item.pubInfo?.overall || 'unpublished';
  if (workPublishFilter === 'published') return overall === 'published' || overall === 'partial';
  if (workPublishFilter === 'publishing') return overall === 'publishing';
  if (workPublishFilter === 'unpublished') return item.accepted && (overall === 'unpublished' || overall === 'failed');
  return true;
}

function renderSingleWorkCard(item) {
  const selected = publishSelection.has(item.key);
  const canPublish = item.accepted && !item.result?.deletionPending;
  const topBar = canPublish
    ? `<div class="work-publish-top">
        <label class="work-publish-checkbox"><input type="checkbox" data-publish-select="${E(item.key)}" ${selected ? 'checked' : ''}><span>勾选发布</span></label>
        <button type="button" class="text-button" data-action="quick-publish-one" data-publish-key="${E(item.key)}">${icon('send')} 发布</button>
      </div>`
    : `<div class="work-publish-top"><span class="publish-chip">待验收</span><a class="text-button" href="#task/${E(item.taskId)}">去验收 →</a></div>`;

  const badges = renderProductPublishBadges(item.pubInfo, item.accepted);

  if (item.source === 'archive') {
    return `<article class="work-card ${selected ? 'is-publish-selected' : ''}" data-work-key="${E(item.key)}">
      ${topBar}
      <a class="work-art" href="${E(item.previewUrl)}" aria-label="播放${E(item.title)}"><img src="${E(item.cover)}" alt="${E(item.title)}封面" loading="lazy"><span class="play-button">${icon('play')}</span></a>
      <h2>${E(item.title)}</h2>
      <p>${E(item.institutionName)} · ${E(item.personName)} · ${E(item.result.label || '已验收')}</p>
      ${badges}
      <div class="work-card-actions"><a href="${E(item.packageUrl)}" class="primary" download>${icon('download')} 下载发布包</a><a href="${E(item.previewUrl)}" class="secondary">${icon('play')} 预览成品</a>${deleteProductButton(item.archive, item.result)}</div>
    </article>`;
  }

  const t = item.task, r = item.result;
  const artwork = r.deletionPending
    ? `<div class="work-art">${icon('video')}</div>`
    : `<a class="work-art" href="${E(item.previewUrl)}" aria-label="播放${E(item.title)}">${r.cover ? `<img src="${E(r.cover)}" alt="${E(item.title)}封面" loading="lazy">` : icon('video')}<span class="play-button">${icon('play')}</span></a>`;
  const actions = r.deletionPending
    ? ''
    : `<a href="${E(r.package)}" class="primary" download>${icon('download')} 下载发布包</a><a href="${E(item.previewUrl)}" class="secondary">${icon('play')} 预览成品</a>${editActions(t.id, r.index)}<a href="#task/${t.id}" class="secondary">制作详情</a>`;

  return `<article class="work-card ${selected ? 'is-publish-selected' : ''}" data-work-key="${E(item.key)}">
    ${topBar}
    ${artwork}
    <h2>${E(item.title)}</h2>
    <p>${E(item.institutionName)} · ${E(item.personName)} · ${r.deletionPending ? '删除待完成' : dateText(t.updated)}</p>
    ${badges}
    <div class="work-card-actions">${actions}${deleteProductButton(t, r)}</div>
  </article>`;
}

function worksPublishToolbarHtml(visibleItems) {
  const publishable = visibleItems.filter(x => x.accepted && !x.result?.deletionPending);
  const allChecked = publishable.length > 0 && publishable.every(x => publishSelection.has(x.key));
  const selectedCount = publishSelection.size;
  return `<div class="works-publish-bar">
    <label class="works-publish-check-all"><input type="checkbox" id="works-select-all-publish" ${allChecked ? 'checked' : ''} ${publishable.length ? '' : 'disabled'}><span>全选当前可发布（${publishable.length}）</span></label>
    <button type="button" class="text-button" data-action="select-unpublished-works" ${publishable.length ? '' : 'disabled'}>只选未发布</button>
    <span class="publish-bar-count" id="works-publish-selected-count">已勾选 ${selectedCount} 条成品</span>
    <button type="button" class="primary" data-action="open-publish-selected" ${selectedCount ? '' : 'disabled'}>${icon('send')} 一键发布所选（${selectedCount}）</button>
    <a class="secondary" href="#publish">查看发布任务</a>
  </div>`;
}

const worksContentBeforePublish = worksContent;
worksContent = function () {
  const allItems = collectWorksCatalog(workInstitution);
  // Prune stale selection keys that no longer exist
  const validKeys = new Set(allItems.filter(x => x.accepted).map(x => x.key));
  for (const k of [...publishSelection.keys()]) {
    if (!validKeys.has(k)) publishSelection.delete(k);
  }
  const filtered = allItems.filter(matchesWorkPublishFilter);
  const refs = workPublishFilter === 'all'
    ? (state.institutions || []).filter(x => x.reference && (!workInstitution || x.id === workInstitution))
    : [];

  if (!filtered.length && !refs.length) {
    const emptyMsg = allItems.length
      ? '当前筛选条件下没有成品，换一个发布状态或机构试试。'
      : '完成的作品会按机构自动归档。尚在制作的内容请到制作任务中查看。';
    return `${worksPublishToolbarHtml([])}${emptyState('没有符合条件的成品', emptyMsg, allItems.length ? '' : '<a class="primary" href="#create">' + icon('plus') + ' 新建制作</a>')}`;
  }
  return `${worksPublishToolbarHtml(filtered)}<div class="works-grid">${filtered.map(renderSingleWorkCard).join('')}${refs.map(referenceCard).join('')}</div>`;
};

const renderWorksBeforePublish = renderWorks;
renderWorks = function () {
  main.innerHTML = `${heading('成品库', '已验收的成品可直接勾选，一键发布到数字人绑定的多平台账号。', `<a class="secondary" href="#publish">${icon('send')} 发布任务</a>`)}
    <div class="filter-tabs" role="group" aria-label="按发布状态筛选成品">
      ${[['all', '全部成品'], ['unpublished', '待发布（已验收）'], ['publishing', '发布中'], ['published', '已发布']].map(([id, label]) =>
        `<button type="button" data-action="works-publish-filter" data-filter="${id}" class="${workPublishFilter === id ? 'active' : ''}" aria-pressed="${workPublishFilter === id}">${label}</button>`
      ).join('')}
    </div>
    <div class="page-tools">
      <span class="subtle">仅「已验收」成品支持发布 · 样片与待验收成品受保护</span>
      <select class="institution-filter" id="work-institution" aria-label="按机构筛选成品">
        <option value="">全部机构</option>
        ${(state.institutions || []).map(x => `<option value="${x.id}" ${workInstitution === x.id ? 'selected' : ''}>${E(x.name)}</option>`).join('')}
      </select>
    </div>
    <div id="works-content">${worksContent()}</div>`;
  $('#work-institution').onchange = e => {
    workInstitution = e.target.value;
    $('#works-content').innerHTML = worksContent();
  };
};

// Also display publish status badges & quick publish inside accepted task detail
const taskBeforePublish = renderTask;
renderTask = function () {
  taskBeforePublish();
  const t = state.tasks.find(x => x.id === selectedTask);
  if (!t || t.status !== 'accepted') return;
  const media = (t.results || []).filter(r => r.video && !r.deletionPending);
  if (!media.length) return;
  const previewCol = $('#task-preview');
  if (!previewCol || previewCol.querySelector('.task-publish-card')) return;
  const rows = media.map(r => {
    const version = r.version || 'original';
    const info = productPublishInfo(t.id, r.index, version);
    const key = makeProductKey(t.id, r.index, version);
    return `<div class="task-publish-row">
      <div><strong>第 ${r.index + 1} 条 · ${E(r.title)}</strong>${renderProductPublishBadges(info, true)}</div>
      <button type="button" class="secondary" data-action="quick-publish-one" data-publish-key="${E(key)}">${icon('send')} 发布这条</button>
    </div>`;
  }).join('');
  previewCol.insertAdjacentHTML('beforeend', `<section class="panel task-publish-card" style="margin-top:20px"><h2 style="font-size:15px">多平台一键发布</h2><p class="subtle">本批已验收，可直接发布到数字人绑定的小红书、抖音和视频号账号。</p>${rows}</section>`);
};

// =========================================================================
// 4. Publish Confirmation Modal
// =========================================================================
async function openPublishDialog(itemsToPublish, opener) {
  if (!itemsToPublish.length) {
    notify('请先勾选至少一条已验收成品', true);
    return;
  }
  const pubSettings = getPublishState().settings || {};
  const d = document.createElement('dialog');
  d.className = 'publish-dialog';
  d.setAttribute('aria-label', '确认发布到多平台');
  d.innerHTML = `<div class="publish-dialog-inner">
    <div class="publish-dialog-head">
      <h2>确认发布（${itemsToPublish.length} 条已验收成品）</h2>
      <button type="button" class="text-button" data-close-publish aria-label="关闭">关闭</button>
    </div>
    <div class="publish-dialog-body"><p class="subtle">正在核验成品文案与蚁小二账号在线状态…</p></div>
    <div class="publish-dialog-foot">
      <span class="inline-error" role="alert" data-publish-dialog-error></span>
      <div class="publish-dialog-foot-actions">
        <button type="button" class="secondary" data-close-publish>取消</button>
        <button type="button" class="primary" data-submit-publish disabled>正在准备…</button>
      </div>
    </div>
  </div>`;
  document.body.append(d);
  d.addEventListener('close', () => { d.remove(); opener?.focus?.(); });
  d.querySelectorAll('[data-close-publish]').forEach(b => { b.onclick = () => d.close(); });
  d.showModal();

  const body = d.querySelector('.publish-dialog-body');
  const submitBtn = d.querySelector('[data-submit-publish]');
  const errSlot = d.querySelector('[data-publish-dialog-error]');

  try {
    const prep = await api('/api/publish/prepare', {
      items: itemsToPublish.map(x => ({ taskId: x.taskId, index: x.index, version: x.version })),
      refreshAccounts: Boolean(pubSettings.apiKeyConfigured),
    });
    const settings = prep.settings || pubSettings;
    const isLocal = (settings.channel || 'local') === 'local';
    const needConfigMsg = !settings.apiKeyConfigured
      ? '尚未配置蚁小二 API Key，请先前往「设置 → 多平台分发（蚁小二）」完成配置。'
      : (isLocal && !settings.clientId ? '当前为本机客户端发布模式，请先前往设置页填写蚁小二 ClientID。' : '');

    const optionsHtml = `<div class="publish-dialog-options">
      <div class="form-group" style="margin:0">
        <label class="field-label" for="dlg-publish-visibility">本次发布可见性</label>
        <select id="dlg-publish-visibility" class="input" data-native-select>
          <option value="private" ${(settings.visibility || 'private') === 'private' ? 'selected' : ''}>私密发布（抖音/小红书仅自己可见，视频号存草稿）</option>
          <option value="public" ${settings.visibility === 'public' ? 'selected' : ''}>公开发布</option>
        </select>
      </div>
      <div class="form-group" style="margin:0">
        <label class="field-label" for="dlg-publish-channel">本次发布通道</label>
        <select id="dlg-publish-channel" class="input" data-native-select>
          <option value="local" ${isLocal ? 'selected' : ''}>本机客户端发布${settings.clientId ? '' : '（未填 ClientID）'}</option>
          <option value="cloud" ${!isLocal ? 'selected' : ''}>云发布（需账号已配代理）</option>
        </select>
      </div>
      <div class="switch-row" style="margin:0;grid-column:1 / -1">
        <label for="dlg-publish-ai">自动附带 AI 合成内容声明（抖音/小红书/视频号）</label>
        <input type="checkbox" class="switch" id="dlg-publish-ai" ${settings.aiDeclaration !== false ? 'checked' : ''}>
      </div>
    </div>`;

    const itemsHtml = (prep.items || []).map((item, idx) => {
      if (!item.eligible) {
        return `<div class="publish-dialog-item"><p class="inline-error">无法发布：${E(item.blockedReason)}</p></div>`;
      }
      const platChecks = (item.platforms || []).map(p => {
        const disabled = !p.canSelect;
        const checked = p.defaultChecked;
        const subText = p.blockedReason
          ? p.blockedReason
          : `${p.accountName || p.accountId}${p.alreadyPublished ? ' · 已发布过（勾选可重发）' : ' · 在线'}`;
        return `<label class="publish-platform-option ${disabled ? 'is-disabled' : ''}">
          <input type="checkbox" data-dlg-item="${idx}" data-dlg-platform="${p.platform}" data-dlg-account="${E(p.accountId || '')}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
          <span><strong>${E(p.platformName)}</strong><small class="${disabled ? 'is-error' : ''}">${E(subText)}</small></span>
        </label>`;
      }).join('');

      return `<div class="publish-dialog-item" data-dlg-card="${idx}">
        <div class="publish-dialog-item-top">
          <div class="publish-item-thumb">${item.coverUrl ? `<img src="${E(item.coverUrl)}" alt="">` : icon('video')}</div>
          <div style="min-width:0;flex:1">
            <strong>${E(item.title)}</strong>
            <p class="publish-item-sub">${E(item.institutionName || '未关联机构')} · 数字人：${E(item.personName || '未识别')}</p>
          </div>
        </div>
        <div class="publish-dialog-platforms">${platChecks}</div>
        <details class="publish-dialog-copy">
          <summary>预览/微调发布标题与文案（默认自动读取发布文案）</summary>
          <div class="publish-dialog-copy-fields">
            <input class="input" data-dlg-title="${idx}" value="${E(item.title)}" maxlength="80" placeholder="发布标题（小红书自动截取前 20 字，抖音前 30 字）">
            <textarea class="input" data-dlg-desc="${idx}" rows="3" maxlength="1000" placeholder="发布正文与话题标签">${E(item.description || '')}</textarea>
          </div>
        </details>
      </div>`;
    }).join('');

    body.innerHTML = `${needConfigMsg ? `<p class="inline-error">${E(needConfigMsg)} <a class="text-button" href="#settings" data-go-settings>前往设置 →</a></p>` : ''}${optionsHtml}${itemsHtml}`;

    body.querySelector('[data-go-settings]')?.addEventListener('click', () => d.close());

    const recalc = () => {
      const checkedCount = body.querySelectorAll('input[data-dlg-platform]:checked').length;
      submitBtn.textContent = checkedCount ? `确认提交发布（${checkedCount} 个平台子项）` : '请至少勾选一个可用平台';
      submitBtn.disabled = Boolean(needConfigMsg) || checkedCount === 0;
    };
    body.addEventListener('change', recalc);
    recalc();

    submitBtn.onclick = async () => {
      errSlot.textContent = '';
      const payloadItems = [];
      (prep.items || []).forEach((item, idx) => {
        if (!item.eligible) return;
        const chosen = [...body.querySelectorAll(`input[data-dlg-item="${idx}"]:checked`)];
        if (!chosen.length) return;
        const accountIds = {};
        const platforms = chosen.map(cb => {
          if (cb.dataset.dlgAccount) accountIds[cb.dataset.dlgPlatform] = cb.dataset.dlgAccount;
          return cb.dataset.dlgPlatform;
        });
        payloadItems.push({
          taskId: item.taskId,
          index: item.index,
          version: item.version,
          platforms,
          accountIds,
          title: body.querySelector(`[data-dlg-title="${idx}"]`)?.value || item.title,
          description: body.querySelector(`[data-dlg-desc="${idx}"]`)?.value ?? item.description,
        });
      });
      if (!payloadItems.length) {
        errSlot.textContent = '请至少勾选一个在线平台账号。';
        return;
      }
      await busy(submitBtn, async () => {
        try {
          await api('/api/publish/batches', {
            visibility: $('#dlg-publish-visibility')?.value || 'private',
            channel: $('#dlg-publish-channel')?.value || 'local',
            aiDeclaration: Boolean($('#dlg-publish-ai')?.checked),
            items: payloadItems,
          });
          publishSelection.clear();
          await refresh(false);
          d.close();
          notify('发布任务已提交，正在后台执行');
          setRoute('publish');
        } catch (err) {
          errSlot.textContent = err.message;
        }
      });
    };
  } catch (err) {
    body.innerHTML = `<p class="inline-error">${E(err.message)}</p>`;
    submitBtn.textContent = '无法发布';
    submitBtn.disabled = true;
  }
}

// =========================================================================
// 5. Publish Tasks Page (#publish)
// =========================================================================
function publishItemRowHtml(batch, item) {
  const active = PUBLISH_ACTIVE_STATUSES.includes(item.status);
  const failed = item.status === 'failed';
  const published = item.status === 'published';
  const statusCls = publishChipClass(item.status);
  const stepText = item.step || PUBLISH_STATUS_TEXT[item.status] || item.status;
  const visLabel = item.visibility === 'private' ? (item.platform === 'channels' ? '草稿/私密' : '私密') : '公开';
  const eventsHtml = (item.events || []).length
    ? `<details class="publish-item-events"><summary>执行记录（${item.events.length} 条）${item.taskSetId ? ' · 任务号 ' + E(item.taskSetId) : ''}</summary><ul>${[...item.events].reverse().map(ev => `<li class="${E(ev.kind || '')}"><time>${dateText(ev.time)}</time><span>${E(ev.message)}</span></li>`).join('')}</ul></details>`
    : '';

  return `<div class="publish-item-row">
    <div class="publish-item-thumb">${item.coverUrl ? `<img src="${E(item.coverUrl)}" alt="">` : icon('video')}</div>
    <div class="publish-item-info">
      <strong title="${E(item.title)}">${E(item.title)}</strong>
      <p class="publish-item-sub">${E(item.institutionName || '未关联机构')} · ${E(item.personName || '')}</p>
      <div class="publish-item-tags">
        <span class="publish-chip">${E(item.platformName)}</span>
        <span class="publish-chip">${E(item.accountName || item.accountId)}</span>
        <span class="publish-chip">${visLabel}</span>
      </div>
    </div>
    <div class="publish-item-progress">
      <div class="publish-item-step">
        <span class="publish-chip ${statusCls}">${active ? cleanupSpinner() : ''}${E(stepText)}</span>
        <small>${dateText(item.publishedAt || item.updated)}</small>
      </div>
      <p class="publish-item-msg ${failed ? 'is-error' : ''}">${E(item.message || '')}</p>
    </div>
    <div class="publish-item-actions">
      ${item.publishUrl ? `<a class="text-button" href="${E(item.publishUrl)}" target="_blank" rel="noopener">查看作品 ${icon('arrow-up-right')}</a>` : ''}
      ${item.taskSetId && !published ? `<button type="button" class="secondary" data-action="publish-refresh-item" data-batch="${E(batch.id)}" data-item="${E(item.id)}">${icon('retry')} 刷新状态</button>` : ''}
      ${failed ? `<button type="button" class="primary" data-action="publish-retry-item" data-batch="${E(batch.id)}" data-item="${E(item.id)}">${icon('retry')} 重试</button>` : ''}
    </div>
    ${eventsHtml}
  </div>`;
}

function publishBatchesHtml() {
  const pub = getPublishState();
  const batches = (pub.batches || []).filter(b => {
    if (publishFilter === 'all') return true;
    const s = b.summary?.status;
    if (publishFilter === 'active') return (b.summary?.active || 0) > 0;
    if (publishFilter === 'published') return s === 'published' || s === 'partial';
    if (publishFilter === 'failed') return (b.summary?.failed || 0) > 0;
    return true;
  });

  if (!batches.length) {
    return emptyState(
      (pub.batches || []).length ? '当前筛选下没有发布任务' : '还没有发布任务',
      (pub.batches || []).length
        ? '切换上方筛选条件查看全部发布记录。'
        : '前往成品库勾选已验收的视频成品，点击「一键发布所选」即可开始分发。',
      `<a class="primary" href="#works">${icon('send')} 去成品库勾选发布</a>`
    );
  }

  return `<div class="publish-batch-list">${batches.map(b => {
    const s = b.summary || { total: 0, published: 0, failed: 0, active: 0, status: 'queued' };
    const statusLabel = PUBLISH_STATUS_TEXT[s.status] || s.status;
    const chLabel = b.channel === 'cloud' ? '云发布' : '本机客户端';
    const visLabel = b.visibility === 'public' ? '公开发布' : '私密发布';
    const canDelete = s.active === 0;
    return `<section class="publish-batch-card">
      <div class="publish-batch-head">
        <div class="publish-batch-title">
          <strong>发布批次 · ${dateText(b.created)}</strong>
          <span class="publish-chip ${publishChipClass(s.status)}">${E(statusLabel)}</span>
          <span class="publish-chip">${chLabel}</span>
          <span class="publish-chip">${visLabel}</span>
        </div>
        <div class="publish-batch-meta">
          <span>已发布 ${s.published} / ${s.total}${s.failed ? ` · 失败 ${s.failed}` : ''}${s.active ? ` · 进行中 ${s.active}` : ''}</span>
          ${canDelete ? `<button type="button" class="text-button" data-action="publish-delete-batch" data-batch="${E(b.id)}">删除记录</button>` : ''}
        </div>
      </div>
      <div class="publish-item-list">${(b.items || []).map(i => publishItemRowHtml(b, i)).join('')}</div>
    </section>`;
  }).join('')}</div>`;
}

function renderPublishPage() {
  const pub = getPublishState();
  const batches = pub.batches || [];
  let totalActive = 0, totalPublished = 0, totalFailed = 0;
  for (const b of batches) {
    totalActive += b.summary?.active || 0;
    totalPublished += b.summary?.published || 0;
    totalFailed += b.summary?.failed || 0;
  }
  const onlineAccounts = pub.accounts?.summary?.online || 0;
  const totalAccounts = pub.accounts?.summary?.total || 0;

  main.innerHTML = `${heading(
    '发布任务',
    '跟踪每条已验收成品在小红书、抖音、视频号的上传、校验与平台确认进度。',
    `<div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="secondary" data-action="publish-sync-page">${icon('retry')} 同步账号</button><a class="primary" href="#works">${icon('send')} 勾选成品发布</a></div>`
  )}
  ${!pub.settings?.apiKeyConfigured ? `<div class="inline-note" style="margin-bottom:18px">尚未配置蚁小二 API Key 与 ClientID。<a class="text-button" href="#settings">前往设置 →</a></div>` : ''}
  <div class="publish-summary-grid">
    <div class="publish-stat-card is-active"><span>进行中子项</span><strong>${totalActive}</strong></div>
    <div class="publish-stat-card is-done"><span>已发布成功</span><strong>${totalPublished}</strong></div>
    <div class="publish-stat-card is-fail"><span>发布失败</span><strong>${totalFailed}</strong></div>
    <div class="publish-stat-card"><span>蚁小二在线账号</span><strong>${onlineAccounts} / ${totalAccounts}</strong></div>
  </div>
  <div class="filter-tabs" role="group" aria-label="按状态筛选发布任务">
    ${[['all', '全部批次'], ['active', '进行中'], ['published', '已发布'], ['failed', '含失败项']].map(([id, label]) =>
      `<button type="button" data-action="publish-filter" data-filter="${id}" class="${publishFilter === id ? 'active' : ''}" aria-pressed="${publishFilter === id}">${label}</button>`
    ).join('')}
  </div>
  <div id="publish-batches-container">${publishBatchesHtml()}</div>`;
}

// =========================================================================
// 6. Hook render / refresh / updateChrome & delegated event handlers
// =========================================================================
const renderBeforePublish = render;
render = function () {
  updatePublishNavBadge();
  const hashHead = (location.hash.slice(1) || '').split('/')[0];
  if (hashHead !== 'publish') {
    return renderBeforePublish();
  }
  route = 'publish';
  updateChrome();
  renderPublishPage();
};

const refreshBeforePublish = refresh;
refresh = async function (paint = true, force = false) {
  const prevPub = JSON.stringify(state?.publish || null);
  const out = await refreshBeforePublish(paint, force);
  updatePublishNavBadge();
  if (paint && JSON.stringify(state?.publish || null) !== prevPub) {
    if (route === 'publish') renderPublishPage();
    else if (route === 'works' && $('#works-content')) $('#works-content').innerHTML = worksContent();
    else if (route === 'task') renderTask();
  }
  return out;
};

document.addEventListener('change', e => {
  const cb = e.target.closest('input[data-publish-select]');
  if (cb) {
    const key = cb.dataset.publishSelect;
    const item = collectWorksCatalog(workInstitution).find(x => x.key === key && x.accepted);
    if (cb.checked && item) publishSelection.set(key, item);
    else publishSelection.delete(key);
    cb.closest('.work-card')?.classList.toggle('is-publish-selected', cb.checked);
    const bar = main.querySelector('.works-publish-bar');
    if (bar) {
      const visible = collectWorksCatalog(workInstitution).filter(matchesWorkPublishFilter);
      bar.outerHTML = worksPublishToolbarHtml(visible);
    }
    return;
  }
  if (e.target.id === 'works-select-all-publish') {
    const visible = collectWorksCatalog(workInstitution).filter(matchesWorkPublishFilter).filter(x => x.accepted && !x.result?.deletionPending);
    if (e.target.checked) visible.forEach(x => publishSelection.set(x.key, x));
    else visible.forEach(x => publishSelection.delete(x.key));
    if ($('#works-content')) $('#works-content').innerHTML = worksContent();
  }
});

document.addEventListener('click', async e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === 'works-publish-filter') {
    workPublishFilter = btn.dataset.filter || 'all';
    renderWorks();
    return;
  }
  if (action === 'select-unpublished-works') {
    publishSelection.clear();
    const visible = collectWorksCatalog(workInstitution).filter(matchesWorkPublishFilter);
    visible.filter(x => x.accepted && !x.result?.deletionPending && (!x.pubInfo || ['unpublished', 'failed'].includes(x.pubInfo.overall)))
      .forEach(x => publishSelection.set(x.key, x));
    if ($('#works-content')) $('#works-content').innerHTML = worksContent();
    return;
  }
  if (action === 'open-publish-selected') {
    openPublishDialog([...publishSelection.values()], btn);
    return;
  }
  if (action === 'quick-publish-one') {
    const key = btn.dataset.publishKey;
    const item = collectWorksCatalog('').find(x => x.key === key && x.accepted);
    if (!item) {
      notify('仅已验收成品支持发起发布', true);
      return;
    }
    openPublishDialog([item], btn);
    return;
  }
  if (action === 'publish-filter') {
    publishFilter = btn.dataset.filter || 'all';
    renderPublishPage();
    return;
  }
  if (action === 'publish-sync-page') {
    await busy(btn, async () => {
      try {
        const res = await api('/api/publish/sync-accounts', {});
        await refresh(false);
        renderPublishPage();
        const s = res.accounts?.summary || { total: 0, online: 0 };
        notify(`已同步 ${s.total} 个蚁小二账号（${s.online} 个在线）`);
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'publish-retry-item') {
    await busy(btn, async () => {
      try {
        await api(`/api/publish/items/${btn.dataset.batch}/${btn.dataset.item}/retry`, {});
        await refresh(false);
        renderPublishPage();
        notify('已重新加入发布队列');
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'publish-refresh-item') {
    await busy(btn, async () => {
      try {
        await api(`/api/publish/items/${btn.dataset.batch}/${btn.dataset.item}/refresh`, {});
        await refresh(false);
        renderPublishPage();
        notify('已刷新平台发布状态');
      } catch (err) {
        notify(err.message, true);
      }
    });
    return;
  }
  if (action === 'publish-delete-batch') {
    await busy(btn, async () => {
      try {
        await api(`/api/publish/batches/${btn.dataset.batch}/delete`, {});
        await refresh(false);
        renderPublishPage();
        notify('发布记录已删除');
      } catch (err) {
        notify(err.message, true);
      }
    });
  }
});

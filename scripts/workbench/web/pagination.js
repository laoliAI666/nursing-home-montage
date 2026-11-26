'use strict';
// SQLite-backed backend pagination + digital-human filtering for Tasks (#tasks), Works (#works) and Publish (#publish).

const pagerState = {
  tasks: { page: 1, pageSize: 20, status: 'all', institutionId: '', personId: '', q: '', data: null, seq: 0 },
  works: { page: 1, pageSize: 24, publishStatus: 'all', institutionId: '', personId: '', q: '', data: null, seq: 0 },
  publish: { page: 1, pageSize: 15, status: 'all', platform: '', personId: '', q: '', data: null, seq: 0 },
};

const PAGE_SIZE_OPTIONS = {
  tasks: [20, 50, 100],
  works: [24, 48, 96],
  publish: [15, 30, 50, 100],
};

let searchDebounceTimers = {};

function debounceRun(key, fn, delay = 220) {
  clearTimeout(searchDebounceTimers[key]);
  searchDebounceTimers[key] = setTimeout(fn, delay);
}

function personFilterOptions(selectedId = '') {
  const people = state?.config?.people || [];
  return `<option value="">全部数字人</option>${people.map(p =>
    `<option value="${E(p.id)}" ${selectedId === p.id ? 'selected' : ''}>${E(p.name)}</option>`
  ).join('')}`;
}

function buildPageNumbers(page, totalPages) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
  const pages = [1];
  const left = Math.max(2, page - 1);
  const right = Math.min(totalPages - 1, page + 1);
  if (left > 2) pages.push('…');
  for (let i = left; i <= right; i++) pages.push(i);
  if (right < totalPages - 1) pages.push('…');
  pages.push(totalPages);
  return pages;
}

function paginationBarHtml(kind, data) {
  if (!data) return '';
  const { total = 0, page = 1, pageSize = 20, totalPages = 1 } = data;
  if (total === 0) return '';
  const unit = kind === 'publish' ? '批' : '条';
  const sizes = PAGE_SIZE_OPTIONS[kind] || [20, 50, 100];
  const nums = buildPageNumbers(page, totalPages);
  return `<nav class="pagination-bar" aria-label="分页导航" data-pager-kind="${kind}">
    <span class="pagination-meta">共 <strong>${total}</strong> ${unit} · 第 <strong>${page}</strong> / ${totalPages} 页</span>
    <div class="pagination-controls">
      <button type="button" class="secondary pagination-btn" data-pager-go="${kind}" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>
      ${nums.map(n => n === '…'
        ? '<span class="pagination-ellipsis" aria-hidden="true">…</span>'
        : `<button type="button" class="pagination-num ${n === page ? 'is-current' : ''}" data-pager-go="${kind}" data-page="${n}" ${n === page ? 'aria-current="page"' : ''}>${n}</button>`
      ).join('')}
      <button type="button" class="secondary pagination-btn" data-pager-go="${kind}" data-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button>
    </div>
    <label class="pagination-size">
      <span>每页</span>
      <select class="input" data-native-select data-pager-size="${kind}" aria-label="每页显示条数">
        ${sizes.map(s => `<option value="${s}" ${Number(pageSize) === s ? 'selected' : ''}>${s} ${unit}</option>`).join('')}
      </select>
    </label>
  </nav>`;
}

function syncNavCountsFromStore() {
  const c = state?.counts;
  if (!c) return;
  const taskEl = $('#task-count');
  if (taskEl && c.activeTasks != null) taskEl.textContent = String(c.activeTasks);
  const worksEl = $('#works-count');
  if (worksEl && c.totalWorks != null) {
    const refs = (state?.institutions || []).filter(x => x.reference).length;
    worksEl.textContent = String(Number(c.totalWorks) + refs);
  }
  const pubEl = $('#publish-count');
  if (pubEl && c.activePublish != null) pubEl.textContent = String(c.activePublish);
}

// =========================================================================
// 1. Tasks Page (#tasks) — Paginated via GET /api/tasks
// =========================================================================
async function loadTasksPage(resetPage = false) {
  const st = pagerState.tasks;
  if (resetPage) st.page = 1;
  const seq = ++st.seq;
  const qs = new URLSearchParams({
    page: String(st.page),
    pageSize: String(st.pageSize),
    status: st.status || 'all',
    institutionId: st.institutionId || '',
    personId: st.personId || '',
    q: st.q || '',
  });
  try {
    const data = await api('/api/tasks?' + qs.toString());
    if (seq !== st.seq) return;
    st.data = data;
    st.page = data.page;
    // Keep fetched tasks accessible for quick acceptance lookups
    for (const item of data.items || []) {
      const idx = (state.tasks || []).findIndex(x => x.id === item.id);
      if (idx === -1) state.tasks.push(item);
      else state.tasks[idx] = { ...state.tasks[idx], ...item };
    }
    if (route === 'tasks') paintTasksPaginated();
  } catch (err) {
    if (route === 'tasks' && $('#tasks-body')) {
      $('#tasks-body').innerHTML = `<p class="inline-error">${E(err.message)}</p>`;
    }
  }
}

function paintTasksPaginated() {
  const host = $('#tasks-body');
  if (!host) return;
  host.innerHTML = tasksBody();
  if (typeof enhanceManagement === 'function') enhanceManagement();
}

tasksBody = function () {
  const st = pagerState.tasks;
  const data = st.data;
  if (!data) return '<p class="subtle">正在加载制作任务…</p>';
  const hasFilter = st.status !== 'all' || st.institutionId || st.personId || st.q;
  return data.items.length
    ? `${taskRows(data.items)}${paginationBarHtml('tasks', data)}`
    : hasFilter
      ? emptyState('没有符合条件的任务', '换一个机构、数字人、状态或关键词再试。')
      : taskRows([]);
};

renderTasks = function () {
  const st = pagerState.tasks;
  const pathInstitution = location.hash.split('/')[1];
  if (pathInstitution && findInstitution(pathInstitution)) {
    st.institutionId = pathInstitution;
    st.page = 1;
  }
  taskFilter = st.status;
  taskInstitution = st.institutionId;
  taskQuery = st.q;

  main.innerHTML = `${heading('制作任务', '进度、补充信息与交付，都在这里接着处理。', `<a class="primary" href="#create">${icon('plus')} 新建制作</a>`)}
    <div class="filter-tabs task-tabs" role="group" aria-label="按任务状态筛选">${[['all', '全部任务'], ['active', '进行中'], ['needs_input', '待补充'], ['pending', '待开始 / 已中断'], ['awaiting_acceptance', '待验收'], ['accepted', '已验收'], ['cancelled', '已停止']].map(([id, name]) =>
      `<button type="button" data-action="tasks-filter-paged" data-filter="${id}" class="${st.status === id ? 'active' : ''}" aria-pressed="${st.status === id}">${name}</button>`
    ).join('')}</div>
    <div class="page-tools paged-tools">
      <label class="search-field"><input type="search" id="task-search" aria-label="搜索任务、机构或人物" placeholder="搜索任务、机构或人物" value="${E(st.q)}"></label>
      <div class="paged-filters">
        <select class="institution-filter" id="task-institution" aria-label="按机构筛选任务"><option value="">全部机构</option>${(state.institutions || []).map(x => `<option value="${x.id}" ${st.institutionId === x.id ? 'selected' : ''}>${E(x.name)}</option>`).join('')}</select>
        <select class="institution-filter" id="task-person" aria-label="按数字人筛选任务">${personFilterOptions(st.personId)}</select>
      </div>
    </div>
    <div id="tasks-body">${st.data ? '' : '<p class="subtle">正在加载制作任务…</p>'}</div>`;

  if (st.data) paintTasksPaginated();
  loadTasksPage(false);

  $('#task-search').oninput = e => {
    st.q = e.target.value;
    debounceRun('tasks-q', () => loadTasksPage(true));
  };
  $('#task-institution').onchange = e => {
    st.institutionId = e.target.value;
    loadTasksPage(true);
  };
  $('#task-person').onchange = e => {
    st.personId = e.target.value;
    loadTasksPage(true);
  };
};

// Ensure opening a task detail from page 2+ always loads full task JSON from /api/tasks/<id>
const renderTaskBeforePaged = renderTask;
let fetchingTaskDetailId = '';
renderTask = function () {
  const existing = (state.tasks || []).find(x => x.id === selectedTask);
  if (existing) {
    renderTaskBeforePaged();
    // If task came from trimmed list summary, hydrate full events in background once
    if (selectedTask && fetchingTaskDetailId !== selectedTask) {
      const targetId = selectedTask;
      fetchingTaskDetailId = targetId;
      api('/api/tasks/' + targetId).then(full => {
        if (fetchingTaskDetailId === targetId) fetchingTaskDetailId = '';
        const idx = (state.tasks || []).findIndex(x => x.id === targetId);
        const prevEventsLen = idx >= 0 ? (state.tasks[idx].events || []).length : 0;
        if (idx >= 0) state.tasks[idx] = full;
        else state.tasks.push(full);
        if (route === 'task' && selectedTask === targetId && (full.events || []).length !== prevEventsLen) {
          renderTaskBeforePaged();
        }
      }).catch(() => {
        if (fetchingTaskDetailId === targetId) fetchingTaskDetailId = '';
      });
    }
    return;
  }
  if (!selectedTask) {
    renderTaskBeforePaged();
    return;
  }
  const targetId = selectedTask;
  main.innerHTML = '<div class="initial-state"><p>正在读取这条制作任务的完整记录…</p></div>';
  api('/api/tasks/' + targetId).then(full => {
    const idx = (state.tasks || []).findIndex(x => x.id === targetId);
    if (idx >= 0) state.tasks[idx] = full;
    else state.tasks.push(full);
    if (route === 'task' && selectedTask === targetId) renderTaskBeforePaged();
  }).catch(() => {
    if (route === 'task' && selectedTask === targetId) renderTaskBeforePaged();
  });
};

// =========================================================================
// 2. Works Library (#works) — Paginated via GET /api/works + Cross-page select
// =========================================================================
function renderPagedWorkCard(item) {
  const selected = publishSelection.has(item.key);
  const canPublish = item.accepted && !item.deletionPending;
  const topBar = canPublish
    ? `<div class="work-publish-top">
        <label class="work-publish-checkbox"><input type="checkbox" data-publish-select-paged="${E(item.key)}" ${selected ? 'checked' : ''}><span>勾选发布</span></label>
        <button type="button" class="text-button" data-action="quick-publish-paged" data-publish-key="${E(item.key)}">${icon('send')} 发布</button>
      </div>`
    : `<div class="work-publish-top"><span class="publish-chip">待验收</span><a class="text-button" href="#task/${E(item.taskId)}">去验收 →</a></div>`;

  const badges = renderProductPublishBadges(item.pubInfo, item.accepted);
  const fakeTask = { id: item.taskId };
  const fakeProd = { index: item.index, title: item.title, deletionPending: item.deletionPending };

  if (item.source === 'archive') {
    return `<article class="work-card ${selected ? 'is-publish-selected' : ''}" data-work-key="${E(item.key)}">
      ${topBar}
      <a class="work-art" href="${E(item.previewUrl)}" aria-label="播放${E(item.title)}"><img src="${E(item.cover)}" alt="${E(item.title)}封面" loading="lazy"><span class="play-button">${icon('play')}</span></a>
      <h2>${E(item.title)}</h2>
      <p>${E(item.institutionName)} · ${E(item.personName)} · ${E(item.label || '已归档')}</p>
      ${badges}
      <div class="work-card-actions"><a href="${E(item.package)}" class="primary" download>${icon('download')} 下载发布包</a><a href="${E(item.previewUrl)}" class="secondary">${icon('play')} 预览成品</a>${deleteProductButton(fakeTask, fakeProd)}</div>
    </article>`;
  }

  const artwork = item.deletionPending
    ? `<div class="work-art">${icon('video')}</div>`
    : `<a class="work-art" href="${E(item.previewUrl)}" aria-label="播放${E(item.title)}">${item.cover ? `<img src="${E(item.cover)}" alt="${E(item.title)}封面" loading="lazy">` : icon('video')}<span class="play-button">${icon('play')}</span></a>`;
  const actions = item.deletionPending
    ? ''
    : `<a href="${E(item.package)}" class="primary" download>${icon('download')} 下载发布包</a><a href="${E(item.previewUrl)}" class="secondary">${icon('play')} 预览成品</a>${editActions(item.taskId, item.index)}<a href="#task/${E(item.taskId)}" class="secondary">制作详情</a>`;

  return `<article class="work-card ${selected ? 'is-publish-selected' : ''}" data-work-key="${E(item.key)}">
    ${topBar}
    ${artwork}
    <h2>${E(item.title)}</h2>
    <p>${E(item.institutionName)} · ${E(item.personName)} · ${item.deletionPending ? '删除待完成' : dateText(item.updated)}</p>
    ${badges}
    <div class="work-card-actions">${actions}${deleteProductButton(fakeTask, fakeProd)}</div>
  </article>`;
}

function worksPagedToolbarHtml(pageItems) {
  const publishable = (pageItems || []).filter(x => x.accepted && !x.deletionPending);
  const allChecked = publishable.length > 0 && publishable.every(x => publishSelection.has(x.key));
  const selectedCount = publishSelection.size;
  return `<div class="works-publish-bar">
    <label class="works-publish-check-all"><input type="checkbox" id="works-select-page-publish" ${allChecked ? 'checked' : ''} ${publishable.length ? '' : 'disabled'}><span>全选本页可发布（${publishable.length}）</span></label>
    <button type="button" class="text-button" data-action="select-unpublished-page-works" ${publishable.length ? '' : 'disabled'}>只选本页未发布</button>
    ${selectedCount ? '<button type="button" class="text-button" data-action="clear-publish-selection">清空已选</button>' : ''}
    <span class="publish-bar-count">已跨页勾选 <strong>${selectedCount}</strong> 条成品</span>
    <button type="button" class="primary" data-action="open-publish-selected" ${selectedCount ? '' : 'disabled'}>${icon('send')} 一键发布所选（${selectedCount}）</button>
    <a class="secondary" href="#publish">查看发布任务</a>
  </div>`;
}

async function loadWorksPage(resetPage = false) {
  const st = pagerState.works;
  if (resetPage) st.page = 1;
  const seq = ++st.seq;
  const qs = new URLSearchParams({
    page: String(st.page),
    pageSize: String(st.pageSize),
    publishStatus: st.publishStatus || 'all',
    institutionId: st.institutionId || '',
    personId: st.personId || '',
    q: st.q || '',
  });
  try {
    const data = await api('/api/works?' + qs.toString());
    if (seq !== st.seq) return;
    st.data = data;
    st.page = data.page;
    if (route === 'works') paintWorksPaginated();
  } catch (err) {
    if (route === 'works' && $('#works-content')) {
      $('#works-content').innerHTML = `<p class="inline-error">${E(err.message)}</p>`;
    }
  }
}

function paintWorksPaginated() {
  const host = $('#works-content');
  if (!host) return;
  host.innerHTML = worksContent();
}

worksContent = function () {
  const st = pagerState.works;
  const data = st.data;
  if (!data) return '<p class="subtle">正在加载成品库…</p>';
  const items = data.items || [];
  const showRefs = st.page === 1 && st.publishStatus === 'all' && !st.personId && !st.q;
  const refs = showRefs
    ? (state.institutions || []).filter(x => x.reference && (!st.institutionId || x.id === st.institutionId))
    : [];
  if (!items.length && !refs.length) {
    const hasFilter = st.publishStatus !== 'all' || st.institutionId || st.personId || st.q;
    return `${worksPagedToolbarHtml([])}${emptyState(
      '没有符合条件的成品',
      hasFilter ? '换一个发布状态、机构、数字人或关键词试试。' : '完成的作品会按机构自动归档。尚在制作的内容请到制作任务中查看。',
      hasFilter ? '' : `<a class="primary" href="#create">${icon('plus')} 新建制作</a>`
    )}`;
  }
  return `${worksPagedToolbarHtml(items)}<div class="works-grid">${items.map(renderPagedWorkCard).join('')}${refs.map(referenceCard).join('')}</div>${paginationBarHtml('works', data)}`;
};

renderWorks = function () {
  const st = pagerState.works;
  workPublishFilter = st.publishStatus;
  workInstitution = st.institutionId;

  main.innerHTML = `${heading('成品库', '已验收的成品支持跨页勾选，一键发布到数字人绑定的多平台账号。', `<a class="secondary" href="#publish">${icon('send')} 发布任务</a>`)}
    <div class="filter-tabs" role="group" aria-label="按发布状态筛选成品">
      ${[['all', '全部成品'], ['unpublished', '待发布（已验收）'], ['publishing', '发布中'], ['published', '已发布']].map(([id, label]) =>
        `<button type="button" data-action="works-publish-filter-paged" data-filter="${id}" class="${st.publishStatus === id ? 'active' : ''}" aria-pressed="${st.publishStatus === id}">${label}</button>`
      ).join('')}
    </div>
    <div class="page-tools paged-tools">
      <label class="search-field"><input type="search" id="work-search" aria-label="搜索成品标题、机构或数字人" placeholder="搜索成品标题、机构或数字人" value="${E(st.q)}"></label>
      <div class="paged-filters">
        <select class="institution-filter" id="work-institution" aria-label="按机构筛选成品">
          <option value="">全部机构</option>
          ${(state.institutions || []).map(x => `<option value="${x.id}" ${st.institutionId === x.id ? 'selected' : ''}>${E(x.name)}</option>`).join('')}
        </select>
        <select class="institution-filter" id="work-person" aria-label="按数字人筛选成品">${personFilterOptions(st.personId)}</select>
      </div>
    </div>
    <div id="works-content">${st.data ? '' : '<p class="subtle">正在加载成品库…</p>'}</div>`;

  if (st.data) paintWorksPaginated();
  loadWorksPage(false);

  $('#work-search').oninput = e => {
    st.q = e.target.value;
    debounceRun('works-q', () => loadWorksPage(true));
  };
  $('#work-institution').onchange = e => {
    st.institutionId = e.target.value;
    loadWorksPage(true);
  };
  $('#work-person').onchange = e => {
    st.personId = e.target.value;
    loadWorksPage(true);
  };
};

// =========================================================================
// 3. Publish Tasks Page (#publish) — Paginated via GET /api/publish/batches
// =========================================================================
async function loadPublishBatchesPage(resetPage = false) {
  const st = pagerState.publish;
  if (resetPage) st.page = 1;
  const seq = ++st.seq;
  const qs = new URLSearchParams({
    page: String(st.page),
    pageSize: String(st.pageSize),
    status: st.status || 'all',
    platform: st.platform || '',
    personId: st.personId || '',
    q: st.q || '',
  });
  try {
    const data = await api('/api/publish/batches?' + qs.toString());
    if (seq !== st.seq) return;
    st.data = data;
    st.page = data.page;
    if (route === 'publish') paintPublishBatchesPaginated();
  } catch (err) {
    if (route === 'publish' && $('#publish-batches-container')) {
      $('#publish-batches-container').innerHTML = `<p class="inline-error">${E(err.message)}</p>`;
    }
  }
}

function paintPublishBatchesPaginated() {
  const host = $('#publish-batches-container');
  if (!host) return;
  const st = pagerState.publish;
  const data = st.data;
  if (!data) {
    host.innerHTML = '<p class="subtle">正在加载发布记录…</p>';
    return;
  }
  const summary = data.summary || { activeItems: 0, publishedItems: 0, failedItems: 0, totalBatches: 0 };
  const sActive = $('#pub-stat-active');
  const sPub = $('#pub-stat-published');
  const sFail = $('#pub-stat-failed');
  if (sActive) sActive.textContent = String(summary.activeItems || 0);
  if (sPub) sPub.textContent = String(summary.publishedItems || 0);
  if (sFail) sFail.textContent = String(summary.failedItems || 0);

  const batches = data.items || [];
  if (!batches.length) {
    const hasFilter = st.status !== 'all' || st.platform || st.personId || st.q;
    host.innerHTML = emptyState(
      hasFilter ? '当前筛选下没有发布任务' : '还没有发布任务',
      hasFilter ? '切换上方筛选条件或关键词查看全部发布记录。' : '前往成品库勾选已验收的视频成品，点击「一键发布所选」即可开始分发。',
      hasFilter ? '' : `<a class="primary" href="#works">${icon('send')} 去成品库勾选发布</a>`
    );
    return;
  }

  host.innerHTML = `<div class="publish-batch-list">${batches.map(b => {
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
  }).join('')}</div>${paginationBarHtml('publish', data)}`;
}

renderPublishPage = function () {
  const st = pagerState.publish;
  const pub = getPublishState();
  const sum = st.data?.summary || pub.summary || { activeItems: pub.activeCount || 0, publishedItems: 0, failedItems: 0 };
  const onlineAccounts = pub.accounts?.summary?.online || 0;
  const totalAccounts = pub.accounts?.summary?.total || 0;

  main.innerHTML = `${heading(
    '发布任务',
    '跟踪每条已验收成品在小红书、抖音、视频号的上传、校验与平台确认进度。',
    `<div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="secondary" data-action="publish-sync-page">${icon('retry')} 同步账号</button><a class="primary" href="#works">${icon('send')} 勾选成品发布</a></div>`
  )}
  ${!pub.settings?.apiKeyConfigured ? `<div class="inline-note" style="margin-bottom:18px">尚未配置蚁小二 API Key 与 ClientID。<a class="text-button" href="#settings">前往设置 →</a></div>` : ''}
  <div class="publish-summary-grid">
    <div class="publish-stat-card is-active"><span>进行中子项</span><strong id="pub-stat-active">${sum.activeItems || 0}</strong></div>
    <div class="publish-stat-card is-done"><span>已发布成功</span><strong id="pub-stat-published">${sum.publishedItems || 0}</strong></div>
    <div class="publish-stat-card is-fail"><span>发布失败</span><strong id="pub-stat-failed">${sum.failedItems || 0}</strong></div>
    <div class="publish-stat-card"><span>蚁小二在线账号</span><strong>${onlineAccounts} / ${totalAccounts}</strong></div>
  </div>
  <div class="filter-tabs" role="group" aria-label="按状态筛选发布任务">
    ${[['all', '全部批次'], ['active', '进行中'], ['published', '已发布'], ['failed', '含失败项']].map(([id, label]) =>
      `<button type="button" data-action="publish-filter-paged" data-filter="${id}" class="${st.status === id ? 'active' : ''}" aria-pressed="${st.status === id}">${label}</button>`
    ).join('')}
  </div>
  <div class="page-tools paged-tools">
    <label class="search-field"><input type="search" id="publish-search" aria-label="搜索标题、账号或数字人" placeholder="搜索标题、账号、机构或数字人" value="${E(st.q)}"></label>
    <div class="paged-filters">
      <select class="institution-filter" id="publish-platform-filter" aria-label="按平台筛选发布任务">
        <option value="">全部平台</option>
        ${PUBLISH_PLATFORMS.map(p => `<option value="${p.key}" ${st.platform === p.key ? 'selected' : ''}>${p.label}</option>`).join('')}
      </select>
      <select class="institution-filter" id="publish-person-filter" aria-label="按数字人筛选发布任务">${personFilterOptions(st.personId)}</select>
    </div>
  </div>
  <div id="publish-batches-container">${st.data ? '' : '<p class="subtle">正在加载发布记录…</p>'}</div>`;

  if (st.data) paintPublishBatchesPaginated();
  loadPublishBatchesPage(false);

  $('#publish-search').oninput = e => {
    st.q = e.target.value;
    debounceRun('publish-q', () => loadPublishBatchesPage(true));
  };
  $('#publish-platform-filter').onchange = e => {
    st.platform = e.target.value;
    loadPublishBatchesPage(true);
  };
  $('#publish-person-filter').onchange = e => {
    st.personId = e.target.value;
    loadPublishBatchesPage(true);
  };
};

// =========================================================================
// 4. Hook chrome counters, live refresh & delegated pagination events
// =========================================================================
const updateChromeBeforePaged = updateChrome;
updateChrome = function () {
  updateChromeBeforePaged();
  syncNavCountsFromStore();
};

const refreshBeforePaged = refresh;
refresh = async function (paint = true, force = false) {
  const prevSig = JSON.stringify([state?.counts, state?.tasks?.map(t => [t.id, t.status, t.progress, t.updated]), state?.publish?.summary, state?.publish?.activeCount]);
  const out = await refreshBeforePaged(paint, force);
  syncNavCountsFromStore();
  const nextSig = JSON.stringify([state?.counts, state?.tasks?.map(t => [t.id, t.status, t.progress, t.updated]), state?.publish?.summary, state?.publish?.activeCount]);
  if (paint && prevSig !== nextSig) {
    if (route === 'tasks') loadTasksPage(false);
    else if (route === 'works') loadWorksPage(false);
    else if (route === 'publish') loadPublishBatchesPage(false);
  }
  return out;
};

document.addEventListener('change', e => {
  const sizeSel = e.target.closest('select[data-pager-size]');
  if (sizeSel) {
    const kind = sizeSel.dataset.pagerSize;
    pagerState[kind].pageSize = Number(sizeSel.value) || pagerState[kind].pageSize;
    if (kind === 'tasks') loadTasksPage(true);
    else if (kind === 'works') loadWorksPage(true);
    else if (kind === 'publish') loadPublishBatchesPage(true);
    return;
  }

  const cb = e.target.closest('input[data-publish-select-paged]');
  if (cb) {
    const key = cb.dataset.publishSelectPaged;
    const items = pagerState.works.data?.items || [];
    const item = items.find(x => x.key === key && x.accepted);
    if (cb.checked && item) publishSelection.set(key, item);
    else publishSelection.delete(key);
    cb.closest('.work-card')?.classList.toggle('is-publish-selected', cb.checked);
    const bar = main.querySelector('.works-publish-bar');
    if (bar) bar.outerHTML = worksPagedToolbarHtml(items);
    return;
  }

  if (e.target.id === 'works-select-page-publish') {
    const items = (pagerState.works.data?.items || []).filter(x => x.accepted && !x.deletionPending);
    if (e.target.checked) items.forEach(x => publishSelection.set(x.key, x));
    else items.forEach(x => publishSelection.delete(x.key));
    paintWorksPaginated();
  }
});

document.addEventListener('click', e => {
  const goBtn = e.target.closest('[data-pager-go]');
  if (goBtn && !goBtn.disabled) {
    const kind = goBtn.dataset.pagerGo;
    const nextPage = Math.max(1, Number(goBtn.dataset.page || 1));
    if (pagerState[kind].page !== nextPage) {
      pagerState[kind].page = nextPage;
      if (kind === 'tasks') loadTasksPage(false);
      else if (kind === 'works') loadWorksPage(false);
      else if (kind === 'publish') loadPublishBatchesPage(false);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
    return;
  }

  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === 'tasks-filter-paged') {
    pagerState.tasks.status = btn.dataset.filter || 'all';
    pagerState.tasks.page = 1;
    renderTasks();
    return;
  }
  if (action === 'works-publish-filter-paged') {
    pagerState.works.publishStatus = btn.dataset.filter || 'all';
    pagerState.works.page = 1;
    renderWorks();
    return;
  }
  if (action === 'publish-filter-paged') {
    pagerState.publish.status = btn.dataset.filter || 'all';
    pagerState.publish.page = 1;
    renderPublishPage();
    return;
  }
  if (action === 'select-unpublished-page-works') {
    const items = (pagerState.works.data?.items || []).filter(
      x => x.accepted && !x.deletionPending && (!x.pubInfo || ['unpublished', 'failed'].includes(x.pubInfo.overall))
    );
    items.forEach(x => publishSelection.set(x.key, x));
    paintWorksPaginated();
    return;
  }
  if (action === 'clear-publish-selection') {
    publishSelection.clear();
    paintWorksPaginated();
    return;
  }
  if (action === 'quick-publish-paged') {
    const key = btn.dataset.publishKey;
    const item = (pagerState.works.data?.items || []).find(x => x.key === key && x.accepted);
    if (!item) {
      notify('仅已验收成品支持发起发布', true);
      return;
    }
    openPublishDialog([item], btn);
  }
});

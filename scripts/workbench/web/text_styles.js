'use strict';
// Text Style Presets (口播写法模板), Friendly Visual Block Modal Editor,
// Per-batch Creation Override & Global Banned Words Settings (全站违禁词与敏感词).

(function () {
  function deepClone(v) {
    return JSON.parse(JSON.stringify(v));
  }

  function hasP(code) {
    return typeof window.hasPerm === 'function' ? window.hasPerm(code) : true;
  }

  function textStylesList() {
    return state?.styles?.textStyles || [];
  }

  function defaultTextStyleId() {
    return state?.styles?.defaultTextStyleId || textStylesList()[0]?.id || 'standard-guided';
  }

  function findTextStyle(id) {
    const list = textStylesList();
    return list.find(x => x.id === id) || list.find(x => x.id === defaultTextStyleId()) || list[0] || null;
  }

  function currentBannedWords() {
    const bw = state?.styles?.globalBannedWords || state?.config?.globalBannedWords;
    if (bw && Array.isArray(bw.words)) return bw;
    return { enabled: true, action: 'repair', words: [], count: bw?.count || 0 };
  }

  function bannedWordPacks() {
    return state?.styles?.bannedWordPacks || state?.config?.bannedWordPacks || [];
  }

  // Friendly domain phrasing helpers
  function charsToSecLabel(chars) {
    const n = Number(chars) || 34;
    const lo = Math.max(4, Math.round(n / 4.2));
    const hi = Math.max(lo + 1, Math.round(n / 3.3));
    return `${n} 字（朗读约 ${lo}–${hi} 秒）`;
  }

  function simLabel(v) {
    const n = Number(v) || 90;
    if (n <= 78) return `严格不同（最多允许 ${n}% 相似）`;
    if (n <= 88) return `适中（推荐，最多允许 ${n}% 相似）`;
    return `较宽松（最多允许 ${n}% 相似）`;
  }

  function omitShortLabel(n) {
    const v = Number(n ?? 2);
    if (v === 0) return '资料卖点尽量全讲';
    if (v <= 2) return `时间紧时最多精简 ${v} 个次要卖点`;
    return `只抓核心（可精简 ${v} 个次要卖点）`;
  }

  function helpTip(title, body, badEx = '', goodEx = '') {
    return `<span class="ts-help-tip">
      <button type="button" class="ts-help-btn" aria-label="查看解释：${E(title)}">?</button>
      <span class="ts-help-popover" role="tooltip">
        <strong>${E(title)}</strong>
        <span>${E(body)}</span>
        ${(badEx || goodEx) ? `<span class="ts-help-ex">
          ${badEx ? `<span class="is-bad">✕ 易踩坑：${E(badEx)}</span>` : ''}
          ${goodEx ? `<span class="is-good">✓ 推荐效果：${E(goodEx)}</span>` : ''}
        </span>` : ''}
      </span>
    </span>`;
  }

  // ---------------------------------------------------------------------------
  // 1. Style Library -> Text Styles Tab (风格库 · 文本风格)
  // ---------------------------------------------------------------------------
  window.renderTextStylesBanner = function renderTextStylesBanner() {
    const bw = currentBannedWords();
    const count = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);
    const canSettings = hasP('settings.read') || hasP('settings.update');
    return `<div class="ts-global-banned-bar">
      <div class="ts-global-banned-info">
        ${icon('shield')}
        <div>
          <strong>全站违禁词与敏感词：${bw.enabled ? `已开启保护（共 ${count} 个词 · ${bw.action === 'warn' ? '写到时验收页提醒' : '写到时自动重写'}）` : '已暂停'}</strong>
          <small>在这里统一管平台容易限流或违规的词（如“根治、第一”），不管选哪套写法模板都会自动避开，不用每个模板重复填。</small>
        </div>
      </div>
      ${canSettings ? `<a class="secondary" href="#settings/banned-words">去设置违禁词 ${icon('arrow-right')}</a>` : ''}
    </div>`;
  };

  function textStyleCardHtml(entry) {
    const canManage = hasP('styles.text_manage');
    const canSetDef = hasP('styles.set_default');
    const op = entry.opening || {};
    const bd = entry.body || {};
    const ed = entry.ending || {};
    const approxSec = Math.max(4, Math.round((op.firstSentenceMaxChars ?? 34) / 3.8));
    const wcText = bd.wordCountMode === 'custom'
      ? `约 ${bd.minChars}–${bd.maxChars} 字`
      : '字数随主播语速自动算';
    return `<article class="ts-card ${entry.isDefault ? 'is-default' : ''}" data-text-style-id="${E(entry.id)}">
      <div class="ts-card-head">
        <h3><span class="style-num-pill">${E(entry.num || '')}</span>${E(entry.name)}</h3>
        <div class="ts-badge-group">
          ${entry.isDefault ? '<span class="ts-chip is-default">全局默认</span>' : ''}
          ${entry.builtin ? '<span class="ts-chip is-builtin">官方内置</span>' : '<span class="ts-chip is-custom">自建模板</span>'}
          ${entry.isModified ? '<span class="ts-chip is-modified">已调整</span>' : ''}
        </div>
      </div>
      <div class="ts-cat-line"><span class="style-cat-tag">${E(entry.category || '自建模板')}</span></div>
      <p class="ts-desc">${E(entry.description || '')}</p>
      <div class="ts-blocks-summary">
        <div class="ts-block-line"><span class="ts-block-tag">开头</span><span>${E(op.prompt || '')}</span></div>
        <div class="ts-block-line"><span class="ts-block-tag">中间</span><span>${E(bd.prompt || '')}</span></div>
        <div class="ts-block-line"><span class="ts-block-tag">结尾</span><span>${E(ed.defaultCta || '')}${ed.respectPersonHook !== false ? '（数字人专属片尾优先）' : ''}</span></div>
      </div>
      <div class="ts-metrics">
        <span>开场约 ${E(approxSec)} 秒内</span>
        <span>${E(wcText)}</span>
        <span>${E(omitShortLabel(bd.omissionLimit ?? 2))}</span>
      </div>
      <div class="ts-card-actions">
        <div class="ts-actions-left">
          <button type="button" class="primary ts-btn-sm" data-ts-edit="${E(entry.id)}">${canManage ? '编辑写法' : '查看写法'}</button>
          ${canManage ? `<button type="button" class="secondary ts-btn-sm" data-ts-copy="${E(entry.id)}">复制一份</button>` : ''}
          ${canSetDef && !entry.isDefault ? `<button type="button" class="text-button" data-ts-default="${E(entry.id)}">设为全局默认</button>` : ''}
        </div>
        <div class="ts-actions-right">
          ${canManage
            ? (entry.builtin
              ? (entry.isModified
                ? `<button type="button" class="secondary ts-btn-sm ts-btn-warn" data-ts-reset="${E(entry.id)}">恢复出厂</button>`
                : `<span class="ts-protected-note">内置模板不可删${helpTip('为什么不能删除？', '官方内置模板受保护，防止误删后找不到稳妥写法。你可以放心修改它，改乱了随时点「恢复出厂」一键还原。')}</span>`)
              : `<button type="button" class="text-button ts-btn-danger" data-ts-delete="${E(entry.id)}">删除</button>`)
            : ''}
        </div>
      </div>
    </article>`;
  }

  window.paintTextStylesTab = function paintTextStylesTab(host) {
    const list = textStylesList();
    const q = (typeof styleQuery === 'string' ? styleQuery : '').trim().toLowerCase();
    const cat = typeof styleCategory === 'string' ? styleCategory : '全部';
    const filtered = list.filter(item => {
      if (cat !== '全部' && (item.category || '自定义模板') !== cat) return false;
      if (!q) return true;
      const hay = [
        item.num, item.id, item.name, item.category, item.description, item.tone,
        item.opening?.prompt, item.body?.prompt, item.ending?.prompt, item.ending?.defaultCta,
        ...(item.opening?.openingStyles || []),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
    if (!filtered.length) {
      host.innerHTML = emptyState('没有找到匹配的口播写法', '试试清空搜索关键词或切换其他分类。', '<button type="button" class="secondary" data-action="reset-style-filters">重置筛选</button>');
      return;
    }
    host.innerHTML = `<div class="ts-grid">${filtered.map(textStyleCardHtml).join('')}</div>`;
  };

  // ---------------------------------------------------------------------------
  // 2. Shared Friendly Visual Block Modal Editor (全局编辑 / 新建 / 单批次微调)
  // ---------------------------------------------------------------------------
  function setNested(obj, path, val) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = val;
  }

  function openTextStyleModal(options) {
    const { mode, source, onApply } = options;
    const readOnly = mode === 'readonly';
    const isBatch = mode === 'batch-override';
    const isNew = mode === 'global-new';
    const draft = deepClone(source);
    if (!draft.opening) draft.opening = { prompt: '', firstSentenceMaxChars: 34, requireInstitutionInOpening: true, institutionMentionOnce: true, openingStyles: ['家属提问', '从具体安排讲起', '直接介绍'] };
    if (!draft.body) draft.body = { prompt: '', omissionLimit: 2, wordCountMode: 'auto', minChars: 195, maxChars: 245, similarityLimit: 90 };
    if (!draft.ending) draft.ending = { prompt: '', defaultCta: '邀请有意了解参观的家属留言。', respectPersonHook: true };
    if (!draft.guard) draft.guard = { forbidParentheses: true, forbidReportTone: true, forbidBodyCta: true };

    let activeBlock = 'basic';
    const dialog = document.createElement('dialog');
    dialog.className = 'style-dialog ts-modal-dialog';

    const titleHtml = isBatch
      ? `只改这一批的写法 <span class="ts-chip is-modified">不影响全局模板</span>`
      : isNew
        ? `新建口播写法模板 <span class="ts-chip is-custom">自建模板</span>`
        : readOnly
          ? `查看写法 · ${E(draft.name)}`
          : `编辑写法 · ${E(draft.name)} ${draft.builtin ? '<span class="ts-chip is-builtin">官方内置 · 可随时还原</span>' : '<span class="ts-chip is-custom">自建模板</span>'}`;

    const saveLabel = isBatch ? '应用到这一批' : isNew ? '创建并保存' : '保存修改';
    const bw = currentBannedWords();
    const bwCount = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);

    dialog.innerHTML = `
      <div class="style-dialog-head">
        <h2>${titleHtml}</h2>
        <button type="button" data-close aria-label="关闭">${icon('close')}</button>
      </div>
      <div class="ts-modal-body">
        <nav class="ts-modal-nav" aria-label="口播写法分块">
          <button type="button" data-ts-block="basic" class="active">1. 名称与说话语气</button>
          <button type="button" data-ts-block="opening">2. 开头怎么讲</button>
          <button type="button" data-ts-block="body">3. 中间怎么展开</button>
          <button type="button" data-ts-block="ending">4. 结尾怎么引导</button>
          <button type="button" data-ts-block="guard">5. 自动避坑检查</button>
          <button type="button" data-ts-block="preview">看成片大概长啥样</button>
        </nav>
        <div class="ts-modal-content" data-ts-content></div>
      </div>
      <div class="ts-modal-foot">
        <div class="ts-modal-foot-left">
          ${mode === 'global-edit' && draft.builtin ? `<button type="button" class="secondary ts-btn-sm ts-btn-warn" data-modal-reset>恢复出厂设置</button><span class="subtle">改乱了随时可一键还原</span>` : ''}
          ${isBatch ? '<span class="subtle">放心改，这里的设置只对当前这一批生效。</span>' : ''}
          <span class="inline-error" data-modal-error role="alert"></span>
        </div>
        <div class="ts-modal-foot-right">
          <button type="button" class="secondary" data-close>${readOnly ? '关闭' : '取消'}</button>
          ${readOnly ? '' : `<button type="button" class="primary" data-modal-save>${saveLabel}</button>`}
        </div>
      </div>`;

    document.body.append(dialog);
    dialog.showModal();

    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelectorAll('[data-close]').forEach(b => { b.onclick = close; });
    dialog.addEventListener('click', e => { if (e.target === dialog) close(); });

    const dis = readOnly ? 'disabled' : '';
    const metaDis = (readOnly || isBatch) ? 'disabled' : '';

    function paintBlock() {
      dialog.querySelectorAll('[data-ts-block]').forEach(b => b.classList.toggle('active', b.dataset.tsBlock === activeBlock));
      const host = dialog.querySelector('[data-ts-content]');
      const op = draft.opening, bd = draft.body, ed = draft.ending, gd = draft.guard;

      if (activeBlock === 'basic') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>给这套写法起个名</h3>
            <p class="form-help">${isBatch ? '当前只微调这一批视频的写法，模板名称与分类不会变动。' : '方便团队在新建视频时一眼认出哪套适合当前机构。'}</p>
            <div class="form-row">
              <div class="form-group">
                <label class="field-label">模板名称</label>
                <input class="input" data-bind="name" maxlength="40" value="${E(draft.name || '')}" ${metaDis} placeholder="例如：重庆探院·交通先导">
              </div>
              <div class="form-group">
                <label class="field-label">适用场景标签</label>
                <input class="input" data-bind="category" maxlength="24" value="${E(draft.category || '')}" ${metaDis} placeholder="例如：综合通用 / 小红书种草">
              </div>
            </div>
            <div class="form-group" style="margin-bottom:0">
              <label class="field-label">一句话说明（什么时候用这套）</label>
              <input class="input" data-bind="description" maxlength="200" value="${E(draft.description || '')}" ${metaDis} placeholder="比如：像熟人带看一样平实讲全核心卖点，适合日常探访">
            </div>
          </div>
          <div class="ts-sec-card">
            <h3>整体说话语气${helpTip('这里怎么写最有效？', '像给新来的文案同事交代一样，用大白话告诉 AI 用什么口气、面向谁说就行（系统会自动保留真实资料核对，不会乱编）。', '用词严谨、强调结构化与转化率', '像熟人带看一样用大白话介绍，面向中老年人和子女，亲切实在不吹牛')}</h3>
            <p class="form-help">决定整条视频听起来像“专业顾问”、“邻里朋友”还是“快节奏探店”。</p>
            <textarea class="input ts-textarea" data-bind="tone" rows="4" maxlength="800" ${dis}>${E(draft.tone || '')}</textarea>
          </div>`;
      } else if (activeBlock === 'opening') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>开头怎么讲（数字人出镜的黄金前几秒）</h3>
            <p class="form-help">告诉 AI 第一段怎么开场最能留住观众。</p>
            <textarea class="input ts-textarea" data-bind="opening.prompt" rows="4" maxlength="800" ${dis}>${E(op.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>开场节奏与养老院名字规则</h3>
            <div class="form-group">
              <div class="label-row">
                <label class="field-label">开场第一句话最长多少字${helpTip('为什么要限制第一句字数？', '开头第一句是数字人出镜讲完就退场的话。如果写太长，数字人会讲超 10 秒显得拖沓；建议 24–34 字（朗读约 6–9 秒）最自然。')}</label>
                <strong data-first-max-label>${E(charsToSecLabel(op.firstSentenceMaxChars))}</strong>
              </div>
              <input type="range" min="18" max="45" value="${E(op.firstSentenceMaxChars)}" data-bind-num="opening.firstSentenceMaxChars" class="ts-range" ${dis}>
              <div class="ts-range-scale"><span>18 字（约 5 秒 · 极速）</span><span>34 字（约 8–10 秒 · 推荐）</span><span>45 字（较长）</span></div>
            </div>
            <div class="switch-row">
              <label><strong>开头必须先说出养老院名字${helpTip('开了和关了有什么区别？', '开启后观众刷到前几秒立刻知道看的是哪家养老院；如果想先抛悬念问题、到中间再揭晓院名，可以关闭。', '“住这里每天都有人照顾…”（观众不知道说哪家）', '“在重庆渝北，椿萱茂老年公寓把失智照护做得很细…”')}</strong><small class="ts-sw-desc">让第一次刷到的观众立刻明白在介绍哪家机构</small></label>
              <input class="switch" type="checkbox" data-bind-bool="opening.requireInstitutionInOpening" ${op.requireInstitutionInOpening ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>整条视频只报一次院名，后面不反复念${helpTip('为什么要只报一次？', '开头报过全名后，中间用“院里/这里”代指听起来更像真人聊天，不会像复读机念广告（数字人专属片尾不受影响）。')}</strong><small class="ts-sw-desc">避免正文与结尾反复念完整院名显得生硬</small></label>
              <input class="switch" type="checkbox" data-bind-bool="opening.institutionMentionOnce" ${op.institutionMentionOnce ? 'checked' : ''} ${dis}>
            </div>
          </div>
          <div class="ts-sec-card">
            <h3>多条视频轮换用的开场方式${helpTip('什么时候会用到？', '一次给同一家养老院做 3 条、5 条视频时，系统会自动轮着用这些开场方式，避免每条视频开头长得一模一样。')}</h3>
            <p class="form-help">批量做多条视频时，AI 会自动轮换这些开场切入角度。</p>
            ${readOnly ? '' : `<div class="ts-tag-input-row">
              <input class="input" data-new-ostyle placeholder="输入开场方式后按回车，如：从周边交通聊起" maxlength="24">
              <button type="button" class="secondary ts-btn-sm" data-add-ostyle>添加</button>
            </div>`}
            <div class="ts-tag-list">
              ${(op.openingStyles || []).map((st, idx) => `<span class="ts-tag">${E(st)}${readOnly ? '' : `<button type="button" data-rm-ostyle="${idx}" aria-label="删除 ${E(st)}">×</button>`}</span>`).join('')}
            </div>
          </div>`;
      } else if (activeBlock === 'body') {
        const omitVal = Number(bd.omissionLimit ?? 2);
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>中间正文怎么展开</h3>
            <p class="form-help">告诉 AI 中间部分先讲什么、后讲什么、要不要挑细节展开。</p>
            <textarea class="input ts-textarea" data-bind="body.prompt" rows="4" maxlength="1000" ${dis}>${E(bd.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>内容详略与防雷同设置</h3>
            <div class="form-group">
              <label class="field-label">资料太多讲不完时，怎么取舍？${helpTip('这个设置管什么？', '一分钟视频大约只能讲 220 字。机构介绍很长时，选「允许精简 1–2 个次要点」能让句子更顺口自然；你在制作页手动点名必讲的要求永远不会被精简。')}</label>
              <div class="ts-choice-cards">
                <button type="button" class="ts-choice-card ${omitVal === 0 ? 'is-active' : ''}" data-set-omit="0" ${dis}>
                  <strong>尽量全讲到</strong>
                  <small>资料要点一个不落（资料多时句子偏紧凑）</small>
                </button>
                <button type="button" class="ts-choice-card ${omitVal === 1 || omitVal === 2 ? 'is-active' : ''}" data-set-omit="2" ${dis}>
                  <strong>允许精简 1–2 个次要点（推荐）</strong>
                  <small>核心卖点全保留，说话更自然顺口</small>
                </button>
                <button type="button" class="ts-choice-card ${omitVal >= 3 ? 'is-active' : ''}" data-set-omit="3" ${dis}>
                  <strong>只挑最核心的讲</strong>
                  <small>适合 40 秒快剪，可精简 3 个次要卖点</small>
                </button>
              </div>
            </div>

            <div class="form-group" style="padding-top:12px;border-top:1px solid var(--line)">
              <div class="label-row">
                <label class="field-label">同一批多条视频之间，防雷同严格度${helpTip('怎么选最合适？', '同机构一次做多条视频时，系统会对比新稿和前面几条像不像。太像会自动换个说法重写；「适中（85%–90%）」既防撞稿又不容易卡住。')}</label>
                <strong data-sim-label>${E(simLabel(bd.similarityLimit))}</strong>
              </div>
              <input type="range" min="60" max="99" value="${E(bd.similarityLimit)}" data-bind-num="body.similarityLimit" class="ts-range" ${dis}>
              <div class="ts-range-scale"><span>70%（每条说法差别很大）</span><span>85%（适中推荐）</span><span>95%（允许句式相近）</span></div>
            </div>

            <div class="form-group" style="margin-bottom:0;padding-top:12px;border-top:1px solid var(--line)">
              <label class="field-label">整篇稿子写多少字？${helpTip('为什么要按数字人语速算？', '有的数字人说话慢、有的说话快。选「按数字人语速自动算」时，系统会根据这位主播实测快慢自动算出刚好 1 分钟的字数，不用手动猜。')}</label>
              <div class="ts-choice-cards ts-choice-2">
                <button type="button" class="ts-choice-card ${bd.wordCountMode !== 'custom' ? 'is-active' : ''}" data-set-wcm="auto" ${dis}>
                  <strong>按数字人语速自动算（推荐）</strong>
                  <small>自动匹配 1 分钟成片（默认约 195–245 字）</small>
                </button>
                <button type="button" class="ts-choice-card ${bd.wordCountMode === 'custom' ? 'is-active' : ''}" data-set-wcm="custom" ${dis}>
                  <strong>我自己指定字数范围</strong>
                  <small>适合做 40 秒短片或 90 秒长解说</small>
                </button>
              </div>
              ${bd.wordCountMode === 'custom' ? `<div class="form-row" style="margin-top:12px">
                <div class="form-group" style="margin-bottom:0">
                  <label class="field-label">最少写多少字</label>
                  <input class="input" type="number" min="60" max="600" data-bind-num="body.minChars" value="${E(bd.minChars)}" ${dis}>
                </div>
                <div class="form-group" style="margin-bottom:0">
                  <label class="field-label">最多写多少字</label>
                  <input class="input" type="number" min="80" max="700" data-bind-num="body.maxChars" value="${E(bd.maxChars)}" ${dis}>
                </div>
              </div>` : ''}
            </div>
          </div>`;
      } else if (activeBlock === 'ending') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>结尾怎么收尾引导</h3>
            <p class="form-help">控制最后一段的说话语气和引导方式。</p>
            <textarea class="input ts-textarea" data-bind="ending.prompt" rows="4" maxlength="600" ${dis}>${E(ed.prompt || '')}</textarea>
          </div>
          <div class="ts-sec-card">
            <h3>默认片尾话术与数字人专属片尾</h3>
            <div class="form-group">
              <label class="field-label">默认片尾引导话术${helpTip('什么时候会用这句话？', '如果选中的数字人没有在「数字人管理」里配置专属片尾钩子，AI 就会参考这句话来收尾。')}</label>
              <input class="input" data-bind="ending.defaultCta" maxlength="120" value="${E(ed.defaultCta || '')}" ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>优先用数字人自己的专属片尾（一字不改）${helpTip('举个例子', '比如数字人“小李”绑了专属片尾「我是小李，带你看完重庆100家养老院…」。开启后最后一句会原样保留他的口头禅；关闭后才改用上面的默认片尾。')}</strong><small class="ts-sw-desc">保持每个数字人账号固定的个人人设与口播风格（推荐开启）</small></label>
              <input class="switch" type="checkbox" data-bind-bool="ending.respectPersonHook" ${ed.respectPersonHook !== false ? 'checked' : ''} ${dis}>
            </div>
          </div>`;
      } else if (activeBlock === 'guard') {
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>自动避坑检查（写完稿后系统自动帮你把关）</h3>
            <p class="form-help">把鼠标移到或点击「?」可以看真实正反话术例子，一眼看懂每个开关管什么。</p>
            <div class="switch-row">
              <label><strong>自动去掉括号和括号里的注释${helpTip('为什么要去掉括号？', '很多机构名或文档里带有括号，配音和字幕念出来会非常别扭，开启后会自动删掉。', '“椿萱茂（重庆回兴）老年公寓” → 念成带括弧的生硬长句', '直接顺口念作“椿萱茂老年公寓”，地名用正常句子交代')}</strong><small class="ts-sw-desc">避免配音把括号里的补充说明生硬念出来</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidParentheses" ${gd.forbidParentheses !== false ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>不念“资料显示 / 画面可见”这类机器腔${helpTip('什么是机器腔？', 'AI 有时候会像念报告一样说“根据资料介绍”“实拍镜头里可以看到”，听起来不像真人。开启后会自动改成自然带看话术。', '“根据机构介绍显示，实拍可见这里有适老扶手。”', '“房间走廊和卫生间都提前装好了适老扶手。”')}</strong><small class="ts-sw-desc">让口播听起来像真人在现场介绍，而不是念文档报告</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidReportTone" ${gd.forbidReportTone !== false ? 'checked' : ''} ${dis}>
            </div>
            <div class="switch-row">
              <label><strong>中间不提前催留言，把引导留到最后${helpTip('为什么要开这个？', '如果视频才播到一半就说“欢迎评论区扣1”，观众容易划走，还会和片尾引导重复。开启后会拦住中间的催单话术。')}</strong><small class="ts-sw-desc">保证中间专心讲养老院，最后一段统一引导咨询</small></label>
              <input class="switch" type="checkbox" data-bind-bool="guard.forbidBodyCta" ${gd.forbidBodyCta !== false ? 'checked' : ''} ${dis}>
            </div>
          </div>
          <div class="ts-sec-card ts-sec-muted">
            <h3>全站违禁词与敏感词（自动叠加生效）</h3>
            <p class="form-help" style="margin-bottom:10px">
              当前状态：<strong>${bw.enabled ? `已开启保护（共 ${bwCount} 个词 · ${bw.action === 'warn' ? '写到时验收页提醒' : '写到时自动重写'}）` : '已暂停'}</strong>。
              违禁词在设置页统一维护，所有模板都会自动避开，不用每个模板单独填。
            </p>
            ${hasP('settings.read') || hasP('settings.update') ? `<a class="secondary ts-btn-sm" href="#settings/banned-words" data-jump-banned>去设置页管理违禁词 ${icon('arrow-right')}</a>` : ''}
          </div>`;
      } else {
        const approxSec = Math.max(4, Math.round((op.firstSentenceMaxChars ?? 34) / 3.8));
        host.innerHTML = `
          <div class="ts-sec-card">
            <h3>按当前设置，生成的口播大概长这样</h3>
            <p class="form-help">用大白话帮你检查这套设置的成片结构，确认没问题就可以保存。</p>
            <div class="ts-story-preview">
              <div class="ts-story-step">
                <span class="ts-story-badge">开头 · 约 ${E(approxSec)} 秒</span>
                <div>
                  <strong>数字人出镜开场</strong>
                  <small>${op.requireInstitutionInOpening !== false ? '先说出养老院名字' : '先抛话题/悬念，不强制先报院名'} · 第 1 句控制在 ${E(op.firstSentenceMaxChars ?? 34)} 字以内 · 多条轮换「${E((op.openingStyles || []).join(' / '))}」</small>
                  <div class="ts-story-quote">${E(op.prompt || '')}</div>
                </div>
              </div>
              <div class="ts-story-step">
                <span class="ts-story-badge is-mid">中间正文</span>
                <div>
                  <strong>实拍画面 + 口播讲解</strong>
                  <small>${bd.wordCountMode === 'custom' ? `全稿约 ${E(bd.minChars)}–${E(bd.maxChars)} 字` : '字数按主播语速自动匹配 1 分钟'} · ${E(omitShortLabel(bd.omissionLimit ?? 2))} · 防雷同：${E(simLabel(bd.similarityLimit ?? 90))}</small>
                  <div class="ts-story-quote">${E(bd.prompt || '')}</div>
                </div>
              </div>
              <div class="ts-story-step">
                <span class="ts-story-badge is-end">结尾引导</span>
                <div>
                  <strong>单句引导咨询</strong>
                  <small>${ed.respectPersonHook !== false ? '如果数字人设了专属片尾则原样保留；否则参考下面话术：' : '统一按下面话术收尾：'}</small>
                  <div class="ts-story-quote">${E(ed.defaultCta || '')}</div>
                </div>
              </div>
              <div class="ts-story-guard-strip">
                自动避坑：
                <span>${gd.forbidParentheses !== false ? '✓ 自动去括号' : '✕ 未开去括号'}</span> ·
                <span>${gd.forbidReportTone !== false ? '✓ 去掉机器腔' : '✕ 未开去机器腔'}</span> ·
                <span>${gd.forbidBodyCta !== false ? '✓ 中间不提前催留言' : '✕ 允许中间引导'}</span> ·
                <span>${bw.enabled ? `✓ 全站违禁词保护（${bwCount} 词）` : '✕ 全站违禁词未开'}</span>
              </div>
            </div>
          </div>`;
      }

      // Bind inputs inside current block
      host.querySelectorAll('[data-bind]').forEach(el => {
        el.addEventListener('input', () => setNested(draft, el.dataset.bind, el.value));
      });
      host.querySelectorAll('[data-bind-num]').forEach(el => {
        el.addEventListener('input', () => {
          const val = Number(el.value);
          setNested(draft, el.dataset.bindNum, val);
          if (el.dataset.bindNum === 'opening.firstSentenceMaxChars') {
            const lbl = host.querySelector('[data-first-max-label]');
            if (lbl) lbl.textContent = charsToSecLabel(val);
          }
          if (el.dataset.bindNum === 'body.similarityLimit') {
            const lbl = host.querySelector('[data-sim-label]');
            if (lbl) lbl.textContent = simLabel(val);
          }
        });
      });
      host.querySelectorAll('[data-bind-bool]').forEach(el => {
        el.addEventListener('change', () => setNested(draft, el.dataset.bindBool, el.checked));
      });
      host.querySelectorAll('[data-set-omit]').forEach(btn => {
        btn.addEventListener('click', () => {
          draft.body.omissionLimit = Number(btn.dataset.setOmit);
          paintBlock();
        });
      });
      host.querySelectorAll('[data-set-wcm]').forEach(btn => {
        btn.addEventListener('click', () => {
          draft.body.wordCountMode = btn.dataset.setWcm;
          paintBlock();
        });
      });
      const addOStyle = () => {
        const inp = host.querySelector('[data-new-ostyle]');
        const v = (inp?.value || '').trim();
        if (!v) return;
        draft.opening.openingStyles = draft.opening.openingStyles || [];
        if (!draft.opening.openingStyles.includes(v)) draft.opening.openingStyles.push(v);
        paintBlock();
      };
      host.querySelector('[data-add-ostyle]')?.addEventListener('click', addOStyle);
      host.querySelector('[data-new-ostyle]')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); addOStyle(); }
      });
      host.querySelectorAll('[data-rm-ostyle]').forEach(btn => {
        btn.addEventListener('click', () => {
          const idx = Number(btn.dataset.rmOstyle);
          if ((draft.opening.openingStyles || []).length <= 1) {
            notify('至少保留 1 种开场方式', true);
            return;
          }
          draft.opening.openingStyles.splice(idx, 1);
          paintBlock();
        });
      });
      host.querySelector('[data-jump-banned]')?.addEventListener('click', () => close());
    }

    dialog.querySelectorAll('[data-ts-block]').forEach(btn => {
      btn.addEventListener('click', () => {
        activeBlock = btn.dataset.tsBlock;
        paintBlock();
      });
    });

    dialog.querySelector('[data-modal-reset]')?.addEventListener('click', async e => {
      const errEl = dialog.querySelector('[data-modal-error]');
      errEl.textContent = '';
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/styles/text/reset', { id: draft.id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将官方模板「${res.entry.name}」恢复出厂设置`);
          close();
          if (route === 'styles') renderStylesPage();
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    });

    dialog.querySelector('[data-modal-save]')?.addEventListener('click', async e => {
      const errEl = dialog.querySelector('[data-modal-error]');
      errEl.textContent = '';
      if (!isBatch && !(draft.name || '').trim()) {
        errEl.textContent = '请填写模板名称';
        activeBlock = 'basic';
        paintBlock();
        return;
      }
      if (draft.body.wordCountMode === 'custom' && Number(draft.body.minChars) > Number(draft.body.maxChars)) {
        errEl.textContent = '最少字数不能大于最多字数';
        activeBlock = 'body';
        paintBlock();
        return;
      }
      if (isBatch) {
        onApply?.(draft);
        close();
        return;
      }
      await busy(e.currentTarget, async () => {
        try {
          const payload = deepClone(draft);
          if (isNew) delete payload.id;
          const res = await api('/api/styles/text', payload);
          state.styles = res.styles;
          state.config = res.config;
          notify(isNew ? `已创建口播写法「${res.entry.name}」` : `已保存口播写法「${res.entry.name}」`);
          close();
          if (route === 'styles') renderStylesPage();
        } catch (err) {
          errEl.textContent = err.message;
        }
      });
    });

    paintBlock();
  }

  // Global click delegation: Help tooltip popovers + Text Style actions
  document.addEventListener('click', e => {
    const helpBtn = e.target.closest('.ts-help-btn');
    if (helpBtn) {
      const tip = helpBtn.closest('.ts-help-tip');
      const wasOpen = tip.classList.contains('is-open');
      document.querySelectorAll('.ts-help-tip.is-open').forEach(x => x.classList.remove('is-open'));
      if (!wasOpen) tip.classList.add('is-open');
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (!e.target.closest('.ts-help-popover')) {
      document.querySelectorAll('.ts-help-tip.is-open').forEach(x => x.classList.remove('is-open'));
    }
  }, true);

  document.addEventListener('click', async e => {

    const newBtn = e.target.closest('[data-action="new-text-style"]');
    if (newBtn) {
      const base = findTextStyle('standard-guided') || textStylesList()[0];
      if (!base) return;
      const copy = deepClone(base);
      delete copy.id;
      copy.builtin = false;
      copy.isDefault = false;
      copy.isModified = false;
      copy.name = '';
      copy.category = '自建模板';
      copy.description = '';
      openTextStyleModal({ mode: 'global-new', source: copy });
      return;
    }
    const editBtn = e.target.closest('[data-ts-edit]');
    if (editBtn) {
      const entry = findTextStyle(editBtn.dataset.tsEdit);
      if (!entry) return;
      openTextStyleModal({ mode: hasP('styles.text_manage') ? 'global-edit' : 'readonly', source: entry });
      return;
    }
    const copyBtn = e.target.closest('[data-ts-copy]');
    if (copyBtn) {
      const entry = findTextStyle(copyBtn.dataset.tsCopy);
      if (!entry) return;
      const copy = deepClone(entry);
      delete copy.id;
      copy.builtin = false;
      copy.isDefault = false;
      copy.isModified = false;
      copy.name = `${entry.name} · 副本`.slice(0, 40);
      copy.category = '自建模板';
      openTextStyleModal({ mode: 'global-new', source: copy });
      return;
    }
    const defBtn = e.target.closest('[data-ts-default]');
    if (defBtn) {
      const id = defBtn.dataset.tsDefault;
      await busy(defBtn, async () => {
        try {
          const res = await api('/api/styles/text/default', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将「${res.entry.name}」设为全局默认口播写法`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
      return;
    }
    const resetBtn = e.target.closest('[data-ts-reset]');
    if (resetBtn) {
      const id = resetBtn.dataset.tsReset;
      const entry = findTextStyle(id);
      if (!entry) return;
      await busy(resetBtn, async () => {
        try {
          const res = await api('/api/styles/text/reset', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已将官方模板「${res.entry.name}」恢复出厂设置`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
      return;
    }
    const delBtn = e.target.closest('[data-ts-delete]');
    if (delBtn) {
      const id = delBtn.dataset.tsDelete;
      const entry = findTextStyle(id);
      if (!entry || entry.builtin) return;
      if (!window.confirm(`确定删除自建模板「${entry.name}」吗？以前做过的视频不受影响。`)) return;
      await busy(delBtn, async () => {
        try {
          const res = await api('/api/styles/text/delete', { id });
          state.styles = res.styles;
          state.config = res.config;
          notify(`已删除模板「${entry.name}」`);
          if (route === 'styles') renderStylesPage();
        } catch (err) { notify(err.message, true); }
      });
    }
  });

  // ---------------------------------------------------------------------------
  // 3. Create Page -> Per-batch Text Style Selection & Override (单批次配置)
  // ---------------------------------------------------------------------------
  function effectiveBatchStyle() {
    const selectedId = createDraft.textStyleId || defaultTextStyleId();
    const base = findTextStyle(selectedId);
    if (!base) return { base: null, effective: null, overridden: false };
    const ov = createDraft.textStyleOverride && typeof createDraft.textStyleOverride === 'object'
      ? createDraft.textStyleOverride : null;
    return { base, effective: ov ? { ...deepClone(base), ...deepClone(ov), name: base.name, id: base.id } : base, overridden: Boolean(ov) };
  }

  function renderBatchTextStyleBox(container) {
    const list = textStylesList();
    if (!list.length || !container) return;
    const { base, effective, overridden } = effectiveBatchStyle();
    if (!base) return;
    if (!createDraft.textStyleId) createDraft.textStyleId = base.id;
    const op = effective.opening || {};
    const bd = effective.body || {};
    const ed = effective.ending || {};
    const bw = currentBannedWords();
    const bwCount = Array.isArray(bw.words) ? bw.words.length : (bw.count || 0);
    const approxSec = Math.max(4, Math.round((op.firstSentenceMaxChars ?? 34) / 3.8));

    container.innerHTML = `
      <div class="label-row">
        <label for="batch-text-style-select">这一批视频的口播写法${helpTip('改这里会影响其他批次吗？', '不会。点击「只改这一批的写法」后，修改仅对当前提交的这批视频生效，不会改动风格库里的原模板。')}</label>
        <a class="text-button" href="#styles/text">去风格库管理模板 ${icon('chevron-right')}</a>
      </div>
      <div class="ts-batch-box ${overridden ? 'is-overridden' : ''}">
        <div class="ts-batch-top">
          <select id="batch-text-style-select" class="input" data-native-select>
            ${list.map(s => `<option value="${E(s.id)}" ${s.id === base.id ? 'selected' : ''}>${E(s.num)} · ${E(s.name)}${s.isDefault ? '（当前全局默认）' : ''}${s.builtin ? '' : ' [自建]'}</option>`).join('')}
          </select>
          <button type="button" class="secondary ts-btn-sm" id="btn-batch-ts-tweak">只改这一批的写法</button>
          ${overridden ? '<button type="button" class="secondary ts-btn-sm ts-btn-warn" id="btn-batch-ts-reset">还原回模板</button>' : ''}
        </div>
        <div class="ts-batch-meta">
          ${overridden
            ? '<span class="ts-chip is-modified">已单独微调本批次（不影响原模板）</span>'
            : '<span class="ts-chip is-builtin">按所选模板原样执行</span>'}
          <span>开场约 ${E(approxSec)} 秒内</span>
          <span>${bd.wordCountMode === 'custom' ? `约 ${E(bd.minChars)}–${E(bd.maxChars)} 字` : '字数随主播语速自动算'}</span>
          <span>${E(omitShortLabel(bd.omissionLimit ?? 2))}</span>
          ${bw.enabled ? `<span>违禁词保护中（${E(bwCount)} 词）</span>` : ''}
        </div>
        <div class="ts-blocks-summary ts-blocks-compact">
          <div class="ts-block-line"><span class="ts-block-tag">开头</span><span>${E(op.prompt || '')}</span></div>
          <div class="ts-block-line"><span class="ts-block-tag">中间</span><span>${E(bd.prompt || '')}</span></div>
          <div class="ts-block-line"><span class="ts-block-tag">结尾</span><span>${E(ed.defaultCta || '')}${ed.respectPersonHook !== false ? '（数字人专属片尾优先）' : ''}</span></div>
        </div>
      </div>
      <p class="form-help">提交后这一批视频会固定使用上面的写法和当前违禁词，以后再改全局模板也不会影响它。</p>`;

    container.querySelector('#batch-text-style-select')?.addEventListener('change', e => {
      createDraft.textStyleId = e.target.value;
      delete createDraft.textStyleOverride;
      saveDraft();
      renderBatchTextStyleBox(container);
    });
    container.querySelector('#btn-batch-ts-tweak')?.addEventListener('click', () => {
      const cur = effectiveBatchStyle();
      openTextStyleModal({
        mode: 'batch-override',
        source: cur.effective,
        onApply: updated => {
          createDraft.textStyleId = cur.base.id;
          createDraft.textStyleOverride = {
            tone: updated.tone,
            opening: updated.opening,
            body: updated.body,
            ending: updated.ending,
            guard: updated.guard,
          };
          saveDraft();
          renderBatchTextStyleBox(container);
          notify('已应用到当前这一批视频');
        },
      });
    });
    container.querySelector('#btn-batch-ts-reset')?.addEventListener('click', () => {
      delete createDraft.textStyleOverride;
      saveDraft();
      renderBatchTextStyleBox(container);
      notify('已还原为所选模板的默认写法');
    });
  }

  const createBeforeTextStyle = renderCreate;
  renderCreate = function () {
    createBeforeTextStyle();
    const form = $('#create-form');
    if (!form || !textStylesList().length) return;
    if ($('#batch-text-style-group', form)) return;
    const reqGroup = $('#requirements', form)?.closest('.form-group');
    const holder = document.createElement('div');
    holder.className = 'form-group';
    holder.id = 'batch-text-style-group';
    if (reqGroup) reqGroup.insertAdjacentElement('afterend', holder);
    else form.insertAdjacentElement('afterbegin', holder);
    renderBatchTextStyleBox(holder);
  };

  // Show frozen text style summary in Task Detail page
  const taskBeforeTextStyle = renderTask;
  renderTask = function () {
    taskBeforeTextStyle();
    const t = state.tasks.find(x => x.id === selectedTask);
    const nr = t?.narrationRules;
    if (!nr) return;
    const brief = main.querySelector('.task-brief');
    if (!brief) return;
    const bw = nr.globalBannedWords || {};
    const bwCount = Array.isArray(bw.words) ? bw.words.length : 0;
    const op = nr.rules?.opening || {};
    const bd = nr.rules?.body || {};
    const approxSec = Math.max(4, Math.round((op.firstSentenceMaxChars ?? 34) / 3.8));
    brief.insertAdjacentHTML('beforeend',
      `<h3>这一批固定的口播写法</h3>
      <p>模板：${E(nr.styleName || nr.styleId || '标准综合带看')}${nr.overridden ? '（本批次已单独微调）' : ''} · 开场约 ${E(approxSec)} 秒内 · ${E(omitShortLabel(bd.omissionLimit ?? 2))}${bw.enabled ? ` · 违禁词保护 ${bwCount} 词（${bw.action === 'warn' ? '验收提醒' : '自动重写'}）` : ''}</p>`);
  };

  // ---------------------------------------------------------------------------
  // 4. Settings Page -> Global Banned Words Panel (全站违禁词与敏感词)
  // ---------------------------------------------------------------------------
  let settingsBannedDraft = null;

  function renderSettingsBannedPanel() {
    const canEdit = hasP('settings.update');
    const bw = settingsBannedDraft || deepClone(currentBannedWords());
    if (!Array.isArray(bw.words)) bw.words = [];
    settingsBannedDraft = bw;
    const packs = bannedWordPacks();
    const dis = canEdit ? '' : 'disabled';

    return `<section class="panel" id="settings-banned-words-panel" aria-labelledby="banned-words-title">
      <div class="settings-section-heading">
        <span>${icon('shield')}</span>
        <div>
          <h2 id="banned-words-title">全站违禁词与敏感词${helpTip('什么时候会生效？', '每次 AI 写口播稿时，系统会提前告诉它避开这些词；写完后再自动逐句检查一遍。所有写法模板和新建批次都会自动生效。')}</h2>
          <p>把抖音、视频号、小红书容易限流或违规的词填在这里，帮团队自动把关，不用靠人工逐字盯。</p>
        </div>
      </div>

      <div class="switch-row">
        <label for="global-banned-enabled"><strong>开启全站违禁词自动检查</strong><small class="ts-sw-desc">建议保持开启，降低发片被平台限流或判违规的风险</small></label>
        <input type="checkbox" class="switch" id="global-banned-enabled" ${bw.enabled ? 'checked' : ''} ${dis}>
      </div>

      <div class="form-group" style="margin-top:14px">
        <label class="field-label">如果 AI 不小心写到了这些词，怎么处理？${helpTip('两种方式有什么区别？', '「自动重写」最省心，AI 会自己换个合规说法；如果你想先快速出片、自己人工决定删不删，可以选「先正常出片，但在验收页提醒我」。')}</label>
        <div class="ts-choice-cards ts-choice-2">
          <button type="button" class="ts-choice-card ${bw.action !== 'warn' ? 'is-active' : ''}" data-bw-action="repair" ${dis}>
            <strong>自动重写，直到不含违禁词（推荐）</strong>
            <small>发现违禁词后自动改写，确保成品安全合规</small>
          </button>
          <button type="button" class="ts-choice-card ${bw.action === 'warn' ? 'is-active' : ''}" data-bw-action="warn" ${dis}>
            <strong>先正常出片，但在验收页提醒我</strong>
            <small>不卡制作进度，只在验收页标出哪句包含了哪个词</small>
          </button>
        </div>
      </div>

      ${canEdit && packs.length ? `<div class="form-group">
        <label class="field-label">一键添加养老行业常用违禁词</label>
        <div class="ts-pack-row">
          ${packs.map(p => `<button type="button" class="secondary ts-btn-sm" data-bw-import-pack="${E(p.id)}" title="${E(p.description || '')}">＋ ${E(p.label)}</button>`).join('')}
          <button type="button" class="text-button" data-bw-reset-default>恢复推荐词库</button>
        </div>
      </div>` : ''}

      <div class="form-group">
        <div class="label-row">
          <label class="field-label" for="global-banned-input">当前生效的违禁词（共 <span id="global-banned-count">${bw.words.length}</span> 个）</label>
          ${canEdit && bw.words.length ? '<button type="button" class="text-button ts-btn-danger" data-bw-clear>清空全部</button>' : ''}
        </div>
        ${canEdit ? `<div class="ts-tag-input-row">
          <input class="input" id="global-banned-input" placeholder="输入不想出现的词后按回车（多个词可用逗号或空格隔开）" maxlength="300">
          <button type="button" class="secondary ts-btn-sm" id="btn-bw-add">添加</button>
        </div>` : ''}
        <div class="ts-tag-list" id="global-banned-tags">
          ${bw.words.length
            ? bw.words.map((w, i) => `<span class="ts-tag is-sensitive">${E(w)}${canEdit ? `<button type="button" data-bw-rm="${i}" aria-label="删除 ${E(w)}">×</button>` : ''}</span>`).join('')
            : '<span class="subtle">暂未添加违禁词</span>'}
        </div>
      </div>

      ${canEdit ? `<div class="ts-banned-actions">
        <button type="button" class="secondary" id="btn-bw-save">${icon('save')} 保存违禁词设置</button>
        <span class="form-help" id="bw-save-status" role="status" style="margin:0"></span>
      </div>` : '<p class="form-help">当前账号仅可查看全站违禁词配置，如需修改请联系超级管理员。</p>'}
    </section>`;
  }

  function bindSettingsBannedPanel(panel) {
    if (!panel) return;
    const repaint = () => {
      const html = renderSettingsBannedPanel();
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      const fresh = wrap.firstElementChild;
      panel.replaceWith(fresh);
      bindSettingsBannedPanel(fresh);
    };

    panel.querySelector('#global-banned-enabled')?.addEventListener('change', e => {
      settingsBannedDraft.enabled = e.target.checked;
    });
    panel.querySelectorAll('[data-bw-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        settingsBannedDraft.action = btn.dataset.bwAction;
        repaint();
      });
    });

    const addWords = () => {
      const inp = panel.querySelector('#global-banned-input');
      const raw = (inp?.value || '').trim();
      if (!raw) return;
      raw.split(/[,，、;\s]+/).map(x => x.trim()).filter(Boolean).forEach(w => {
        if (!settingsBannedDraft.words.includes(w)) settingsBannedDraft.words.push(w.slice(0, 40));
      });
      repaint();
    };
    panel.querySelector('#btn-bw-add')?.addEventListener('click', addWords);
    panel.querySelector('#global-banned-input')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addWords(); }
    });
    panel.querySelectorAll('[data-bw-rm]').forEach(btn => {
      btn.addEventListener('click', () => {
        settingsBannedDraft.words.splice(Number(btn.dataset.bwRm), 1);
        repaint();
      });
    });
    panel.querySelector('[data-bw-clear]')?.addEventListener('click', () => {
      settingsBannedDraft.words = [];
      repaint();
    });
    panel.querySelectorAll('[data-bw-import-pack]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pack = bannedWordPacks().find(p => p.id === btn.dataset.bwImportPack);
        if (!pack) return;
        let added = 0;
        (pack.words || []).forEach(w => {
          if (!settingsBannedDraft.words.includes(w)) {
            settingsBannedDraft.words.push(w);
            added++;
          }
        });
        repaint();
        notify(added ? `新添加了 ${added} 个行业违禁词（记得点保存）` : '这批词都已经在列表里了');
      });
    });
    panel.querySelector('[data-bw-reset-default]')?.addEventListener('click', async e => {
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/settings/banned-words', { resetDefault: true });
          state.styles = res.styles;
          state.config = res.config;
          settingsBannedDraft = deepClone(res.globalBannedWords);
          repaint();
          notify('已恢复默认推荐违禁词');
        } catch (err) { notify(err.message, true); }
      });
    });
    panel.querySelector('#btn-bw-save')?.addEventListener('click', async e => {
      const statusEl = panel.querySelector('#bw-save-status');
      if (statusEl) statusEl.textContent = '';
      await busy(e.currentTarget, async () => {
        try {
          const res = await api('/api/settings/banned-words', settingsBannedDraft);
          state.styles = res.styles;
          state.config = res.config;
          settingsBannedDraft = deepClone(res.globalBannedWords);
          if (statusEl) statusEl.textContent = '已保存';
          notify('全站违禁词设置已保存');
        } catch (err) {
          notify(err.message, true);
        }
      });
    });
  }

  const settingsBeforeBanned = renderSettings;
  renderSettings = function () {
    settingsBeforeBanned();
    const form = $('#settings-form');
    if (!form || $('#settings-banned-words-panel', form)) return;
    settingsBannedDraft = deepClone(currentBannedWords());
    const sticky = form.querySelector('.sticky-actions');
    const html = renderSettingsBannedPanel();
    if (sticky) sticky.insertAdjacentHTML('beforebegin', html);
    else form.insertAdjacentHTML('beforeend', html);
    const panel = $('#settings-banned-words-panel', form);
    bindSettingsBannedPanel(panel);
    if (location.hash === '#settings/banned-words' && panel) {
      requestAnimationFrame(() => panel.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    }
  };

  // ---------------------------------------------------------------------------
  // 5. Transparently attach textStyleId/Override & globalBannedWords on API calls
  // ---------------------------------------------------------------------------
  const apiBeforeTextStyles = api;
  api = async function textStyleAwareApi(path, data, options = {}) {
    if (data && typeof data === 'object') {
      if ((path === '/api/tasks' || path === '/api/draft') && !('textStyleId' in data)) {
        const extra = {};
        if (createDraft.textStyleId) extra.textStyleId = createDraft.textStyleId;
        if (createDraft.textStyleOverride && typeof createDraft.textStyleOverride === 'object') {
          extra.textStyleOverride = createDraft.textStyleOverride;
        }
        if (Object.keys(extra).length) data = { ...data, ...extra };
      } else if (path === '/api/config' && !('globalBannedWords' in data) && $('#settings-banned-words-panel') && settingsBannedDraft) {
        data = { ...data, globalBannedWords: settingsBannedDraft };
      }
    }
    return apiBeforeTextStyles(path, data, options);
  };
})();

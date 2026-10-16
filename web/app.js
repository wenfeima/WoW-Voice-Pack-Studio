/* WoW 语音包工坊 —— 前端逻辑 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let ENV = null, POLL = null, playing = null;

/* ---------- 工具 ---------- */
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show ' + kind;
  clearTimeout(t._h);
  t._h = setTimeout(() => t.className = 'toast ' + kind, 3200);
}
const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB'
  : b > 1024 ? (b / 1024).toFixed(0) + ' KB' : b + ' B';

async function api(path, body) {
  const opt = body ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  } : {};
  const r = await fetch(path, opt);
  return r.json();
}
async function get(path) { return api('/api?a=' + path); }

function renderLog(lines) {
  const el = $('#log');
  el.innerHTML = (lines || []).map(l =>
    `<div><span class="t">${l.t}</span> <span class="${l.level}">${esc(l.msg)}</span></div>`
  ).join('');
  el.scrollTop = el.scrollHeight;
}
const esc = s => String(s).replace(/[&<>]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/* ---------- 启动 ---------- */
async function boot() {
  ENV = await get('env');
  renderBadges();
  fillVoices();
  fillCorpusSel();
  loadConfigToForm();
  renderChecks();
  renderVoicePick();
  countLines();
  await refreshList();
  renderLog(ENV.log);
  startPoll();
}

function renderBadges() {
  const qv = (ENV.qwen_voices || []).length;
  const b = [
    ['Qwen3 音色', qv > 0, qv ? qv + ' 个：' + ENV.qwen_voices.map(v => v.name.split('（')[0]).join('/') : '未找到 .pt'],
    ['本地 VITS', (ENV.local_voices || []).length > 0,
      (ENV.local_voices || []).length ? ENV.local_voices.length + ' 个' : '无'],
    ['WoW AddOns', ENV.wow_found, ENV.wow_found ? shortPath(ENV.wow_addons) : '未找到'],
    ['ffmpeg', ENV.ffmpeg_ok, ENV.ffmpeg_ok ? shortPath(ENV.ffmpeg) : '未找到'],
    ['LibSharedMedia', ENV.lsm_ok, ENV.lsm_ok ? '已安装' : '未找到'],
    ['已生成', true, ENV.ogg_count + ' 条 / ' + fmtSize(ENV.ogg_bytes)],
  ];
  $('#badges').innerHTML = b.map(([k, ok, v]) =>
    `<span class="badge ${ok ? 'ok' : 'bad'}" title="${esc(v)}">${k} · ${esc(String(v).slice(0, 42))}</span>`
  ).join('');
}
const shortPath = p => p.length > 46 ? '…' + p.slice(-45) : p;

function fillVoices() {
  const sel = $('#voice');
  const qv = ENV.qwen_voices || [];
  const lv = ENV.local_voices || [];
  const g = {};
  ENV.voices.forEach(v => (g[v.engine] ||= []).push(v));

  let html = '';
  // ① Qwen3-TTS 自定义克隆音色（.pt）★ 首选
  if (qv.length) {
    html += '<optgroup label="⭐ 我的 Qwen3 克隆音色（.pt）">' +
      qv.map(v => `<option value="${esc(v.id)}">${esc(v.name.replace('（Qwen3 克隆音色）', ''))}</option>`).join('') +
      '</optgroup>';
  }
  // ② 本地 VITS 音色目录
  if (lv.length) {
    html += '<optgroup label="🎤 本地 VITS 音色（sherpa）">' +
      lv.map(v => `<option value="${esc(v.id)}">${esc(v.name.replace('（本地音色）', ''))}</option>`).join('') +
      '</optgroup>';
  }
  // ③ edge-tts
  html += '<optgroup label="☁️ 在线音色（edge-tts）">' +
    (g.edge || []).map(v => `<option value="${esc(v.id)}">${v.name}</option>`).join('') + '</optgroup>';
  // ④ 系统
  html += '<optgroup label="💻 系统语音（离线兜底）">' +
    (g.sapi || []).map(v => `<option value="${esc(v.id)}">${v.name}</option>`).join('') + '</optgroup>';

  sel.innerHTML = html;
  if (qv.length) sel.value = qv[0].id;
  else if (lv.length) sel.value = lv[0].id;
  else sel.value = 'zh-CN-XiaoxiaoNeural';
  sel.onchange = toggleSapi;
  toggleSapi();
  updateVoiceTip();
}

function curVoice() {
  return $('#voice').value;
}
function updateVoiceTip() {
  const tip = $('#voiceTip');
  const id = curVoice();
  const qv = (ENV.qwen_voices || []).find(x => x.id === id);
  const lv = (ENV.local_voices || []).find(x => x.id === id);
  const isQ = !!qv;
  // Qwen 用温度，本地 VITS 用语速
  $('#tempWrap').style.display = isQ ? '' : 'none';
  $('#tempHint').style.display = isQ ? '' : 'none';
  $('#rateWrap').style.display = isQ ? 'none' : '';
  if (qv) {
    tip.innerHTML = `Qwen3-TTS 克隆 · 首次生成需加载模型（约 20 秒），之后常驻复用
      · 温度越低越稳定、越高越有表现力<br>文件：<code>${esc(qv.path)}</code>`;
  } else if (lv) {
    const i = lv.info || {};
    tip.innerHTML = `本地 VITS 推理 · ${i.sample_rate ? (i.sample_rate / 1000) + ' kHz' : '?'}
      · 语速由滑杆控制（长度缩放）<br>目录：<code>${esc(lv.path)}</code>`;
  } else {
    tip.textContent = '在线音色，需要联网。语速由滑杆控制。';
  }
  tip.style.display = '';
}
$('#voice').addEventListener('change', () => { toggleSapi(); updateVoiceTip(); });
function toggleSapi() {
  const isSys = $('#voice').value === 'sys';
  $$('.sapi-only').forEach(e => e.style.display = isSys ? '' : 'none');
}

function fillCorpusSel() {
  $('#corpusSel').innerHTML = Object.keys(ENV.corpus_meta)
    .map(k => `<option value="${k}">${k} — ${ENV.corpus_meta[k]}</option>`).join('');
}
const CORPUS = {};
async function loadCorpus() {
  if (!CORPUS.k) Object.assign(CORPUS, (await get('corpus')).data);
  return CORPUS;
}

function loadConfigToForm() {
  const c = ENV.config;
  $('#wowDir').value = c.wow_addons_dir || '';
  $('#ffDir').value = c.ffmpeg || '';
  $('#pkg').value = c.package || '';
  $('#disp').value = c.display_name || '';
  $('#tag').value = c.author_tag || '';
  $('#iface').value = c.interface_version || '';
  $('#voiceRoots').value = c.voice_roots || '';
}
function renderChecks() {
  const qv = ENV.qwen_voices || [];
  const lv = ENV.local_voices || [];
  const rows = [
    ['Qwen3-TTS 自定义音色（.pt）', ENV.qwen_ok && qv.length,
      ENV.qwen_ok ? (qv.length ? qv.map(v => v.name.split('（')[0] + ' → ' + v.path).join(' ; ')
        : '工程在，但没有 .pt 音色') : '未找到 Qwen3-TTS 工程'],
    ['本地 VITS 音色（sherpa-onnx）', ENV.local_ok && lv.length,
      ENV.local_ok ? (lv.length ? lv.map(v => v.path).join(' ; ') : '未扫描到音色目录')
        : '未安装 sherpa-onnx'],
    ['WoW 客户端 AddOns', ENV.wow_found, ENV.wow_addons || '未探测到'],
    ['ffmpeg', ENV.ffmpeg_ok, ENV.ffmpeg || '未探测到'],
    ['LibSharedMedia-3.0', ENV.lsm_ok, ENV.lsm_ok ? '已安装（语音可被识别）' : '未找到，语音不会出现在插件设置里'],
    ['edge-tts（在线音色）', ENV.edge_ok, ENV.edge_ok ? '已安装' : '未安装，用自定义音色即可'],
    ['语音输出目录', true, ENV.work],
  ];
  $('#checks').innerHTML = rows.map(([k, ok, v]) =>
    `<div class="check"><span class="dot ${ok ? 'ok' : 'bad'}"></span>
     <span>${k}</span><span class="path" title="${esc(v)}">${esc(v)}</span></div>`).join('');
}

/* ---------- tabs ---------- */
$$('.tab').forEach(t => t.onclick = () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.pane').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $('#pane-' + t.dataset.tab).classList.add('active');
});

/* ---------- 生成页 ---------- */
/* 条数统计与后端 parse_lines 保持一致，避免"显示 11 条实际 1 条" */
let _cntTimer = null;
function countLines() {
  const t = $('#text').value;
  $('#lineHint').textContent = '…';
  clearTimeout(_cntTimer);
  _cntTimer = setTimeout(async () => {
    try {
      const r = await api('parse', { text: t });
      $('#lineHint').textContent = (r.count || 0) + ' 条待生成';
      $('#lineHint').style.color = r.count > 0 ? '' : 'var(--err)';
    } catch (e) { $('#lineHint').textContent = '—'; }
  }, 250);
}
$('#text').addEventListener('input', countLines);

$('#rate').addEventListener('input', e => $('#rateVal').textContent = e.target.value + '%');
$('#temp').addEventListener('input', e => $('#tempVal').textContent = (+e.target.value).toFixed(2));$('#sapiRate').addEventListener('input', e => $('#sapiRateVal').textContent = e.target.value);

$('#btnLoadCorpus').onclick = async () => {
  const c = await loadCorpus();
  $('#text').value = c[$('#corpusSel').value] || '';
  countLines(); toast('已载入 ' + $('#lineHint').textContent, 'ok');
};
$('#btnAppendCorpus').onclick = async () => {
  const c = await loadCorpus();
  const add = c[$('#corpusSel').value] || '';
  $('#text').value = $('#text').value.trim() + '\n' + add;
  countLines(); toast('已追加', 'ok');
};

$('#btnTry').onclick = async () => {
  const text = $('#tryText').value.trim();
  if (!text) return;
  const btn = $('#btnTry');
  btn.disabled = true; btn.textContent = '合成中…';
  const r = await api('preview_tts', {
    text, voice: curVoice(),
    rate: +$('#rate').value, sapi_rate: +$('#sapiRate').value,
    temperature: +$('#temp').value
  });
  btn.disabled = false; btn.textContent = '▶ 试听';
  if (!r.ok) return toast(r.err, 'err');
  const a = $('#player');
  a.src = '/audio?f=' + encodeURIComponent(r.file) + '&_=' + Date.now();
  a.play().catch(() => { });
  toast('试听已生成', 'ok');
};

$('#btnStart').onclick = async () => {
  const text = $('#text').value;
  if (!text.trim()) return toast('台词表是空的', 'err');
  const btn = $('#btnStart');
  btn.disabled = true;
  const r = await api('start', {
    text, voice: curVoice(),
    rate: +$('#rate').value, sapi_rate: +$('#sapiRate').value,
    temperature: +$('#temp').value,
    tag: $('#tag').value, auto_tag: $('#autoTag').checked
  });
  if (r.ok) {
    toast(`开始生成 ${r.total} 条` + (r.voice_tag ? `（标识：${r.voice_tag}）` : ''), 'ok');
  } else {
    btn.disabled = false;
    toast(r.err, 'err');
  }
};
$('#btnCancel').onclick = async () => {
  await api('cancel', {});
  toast('已请求停止');
};

/* ---------- 轮询 ---------- */
let LAST_POLL_KEYS = '';
let LAST_VOICE_KEYS = '';
function startPoll() {
  if (POLL) return;
  POLL = setInterval(async () => {
    let s;
    try { s = await get('status'); } catch (e) { return; }
    renderLog(s.log);
    if (s.job) {
      const p = s.job.done.length + s.job.failed.length;
      const pct = s.job.total ? p / s.job.total * 100 : 0;
      $('#bar').style.width = pct + '%';
      $('#btnStart').disabled = !!s.job.running;
      $('#btnCancel').disabled = !s.job.running;
      const eta = s.job.running && s.job.eta_sec
        ? ` · 剩余约 ${s.job.eta_sec}s`
        : (s.job.running && s.job.pending ? ` · 剩余 ${s.job.pending} 条` : '');
      $('#stat').textContent = s.job.running
        ? `${s.job.current}　(${p}/${s.job.total}${eta})`
        : `完成 ${s.job.done.length} 条，失败 ${s.job.failed.length} 条`;
      if (s.job.failed.length && !s.job.running) {
        const f = s.job.failed[0];
        $('#stat').textContent += `　首个错误：${f.name} → ${f.err}`;
      }
    } else {
      $('#bar').style.width = '0%';
      $('#btnStart').disabled = false;
      $('#btnCancel').disabled = true;
      $('#stat').textContent = '待命';
    }
    // 列表只在「文件名集合真的变了」时才重绘，避免冲掉用户的勾选/滚动
    if (s.preview) {
      const keys = s.preview.map(i => i.name).sort().join('\u0001');
      if (keys !== LAST_POLL_KEYS) {
        LAST_POLL_KEYS = keys;
        drawList(s.preview);
      }
      // 音色集合变了就重渲打包页的勾选
      const vk = (s.ogg_voices || []).map(v => v.key + ':' + v.count).join('|');
      if (vk !== LAST_VOICE_KEYS) {
        LAST_VOICE_KEYS = vk;
        ENV.ogg_voices = s.ogg_voices;
        renderVoicePick();
      }
    }
  }, 900);
}

/* ---------- 清单 ---------- */
let PREVIEW = [];
async function refreshList() {
  const e = await get('env');
  ENV.preview = e.preview;
  renderBadges();
  drawList(e.preview);
}

/* 从文件名里抽音色标识：形如 【我】君仪2_1分担.ogg → 君仪2 */
function voiceOf(name) {
  const m = name.match(/^【[^】]*】(.+?)[_.]([^_.]*)\.ogg$/i);
  if (m) return m[1];
  const m2 = name.match(/^【[^】]*】(.+?)\.ogg$/);
  return m2 ? m2[1] : '（无标识）';
}

function fillVoiceFilter() {
  const sel = $('#voiceFilter');
  const set = [...new Set(PREVIEW.map(i => voiceOf(i.name)))].sort();
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部音色（' + PREVIEW.length + '）</option>' +
    set.map(v => `<option value="${esc(v)}">${esc(v)}（${PREVIEW.filter(i => voiceOf(i.name) === v).length}）</option>`).join('');
  if (set.includes(cur)) sel.value = cur;
}

function drawList(items) {
  PREVIEW = items || [];
  fillVoiceFilter();
  const kw = $('#filter').value.trim().toLowerCase();
  const vf = $('#voiceFilter').value;
  const show = PREVIEW.filter(i =>
    (!kw || i.name.toLowerCase().includes(kw)) &&
    (!vf || voiceOf(i.name) === vf));
  $('#listCount').textContent = PREVIEW.length;
  const total = show.reduce((a, b) => a + b.size, 0);
  $('#listSize').textContent = `${show.length} / ${PREVIEW.length} 条 · ${fmtSize(total)}`;
  $('#list').innerHTML = show.map(i =>
    `<div class="item" data-n="${esc(i.name)}" data-v="${esc(voiceOf(i.name))}">
      <input type="checkbox" class="ck" value="${esc(i.name)}">
      <button class="pl" title="播放">▶</button>
      <span class="vtag" title="音色">${esc(voiceOf(i.name))}</span>
      <span class="nm">${esc(i.name.replace(/^【[^】]*】/, '').replace(new RegExp('^' + voiceOf(i.name) + '[_.]'), ''))}</span>
      <span class="sz">${fmtSize(i.size)}</span>
      <span class="nm" style="flex:0 0 auto"><input class="rn" value="${esc(i.name)}"
        style="margin:0;width:130px;font-size:11px;padding:3px 6px" title="改名"></span>
    </div>`).join('') || '<div class="hint" style="padding:20px;text-align:center">还没有语音，先去「批量生成」</div>';

  $$('#list .pl').forEach(b => b.onclick = e => {
    const nm = e.target.closest('.item').dataset.n;
    if (playing) { playing.pause(); playing = null; }
    $$('#list .item').forEach(x => x.classList.remove('playing'));
    const a = new Audio('/audio?f=' + encodeURIComponent(ENV.work + '/' + nm));
    a.play().catch(() => toast('播放失败', 'err'));
    playing = a;
    e.target.closest('.item').classList.add('playing');
    a.onended = () => e.target.closest('.item').classList.remove('playing');
  });
}
$('#filter').addEventListener('input', () => drawList(PREVIEW));
$('#voiceFilter').addEventListener('change', () => drawList(PREVIEW));
$('#btnRefresh').onclick = refreshList;

$('#btnDelSel').onclick = async () => {
  const names = $$('#list .ck:checked').map(c => c.value);
  if (!names.length) return toast('没勾选任何文件', 'err');
  if (!confirm(`删除 ${names.length} 个文件？`)) return;
  const r = await api('delete', { names });
  toast(`已删除 ${r.count} 个`, 'ok');
  refreshList();
};
$('#btnClearAll').onclick = async () => {
  if (!PREVIEW.length) return toast('已经是空的', 'err');
  if (!confirm(`清空全部 ${PREVIEW.length} 个语音文件？此操作不可恢复。`)) return;
  const r = await api('clear', {});
  toast(`已清空 ${r.count} 个`, 'ok');
  refreshList();
};
$('#btnOpenWork').onclick = () => api('open_folder', { path: ENV.work });

/* 改名：失焦即提交 */
document.addEventListener('change', e => {
  if (!e.target.classList.contains('rn')) return;
  const item = e.target.closest('.item');
  const old = item.dataset.n, nw = e.target.value.trim();
  if (!nw || nw === old) { e.target.value = old; return; }
  api('rename', { mapping: { [old]: nw } }).then(() => {
    toast(`已改名 → ${nw}`, 'ok'); refreshList();
  });
});

/* ---------- 打包 ---------- */
/* 渲染音色勾选：默认全选 = 全部打包 */
function renderVoicePick() {
  const vs = ENV.ogg_voices || [];
  const list = $('#vpickList');
  list.innerHTML = vs.map(v =>
    `<label><input type="checkbox" class="vck" value="${esc(v.key)}" checked>
      ${esc(v.key || '（无标识）')}<span class="cnt">${v.count}</span></label>`).join('') ||
    '<span class="hint" style="padding:4px 0">output 里还没有语音</span>';
  $$('#vpickList .vck').forEach(c => c.onchange = syncAllBox);
  $('#vAll').checked = true;
  $('#vAll').onchange = () => {
    $$('#vpickList .vck').forEach(c => c.checked = $('#vAll').checked);
  };
}
function syncAllBox() {
  const all = $$('#vpickList .vck');
  $('#vAll').checked = all.length > 0 && all.every(c => c.checked);
}
/* 取勾选的音色；全选/无勾选时返回 null 表示"全部" */
function pickedVoices() {
  const all = $$('#vpickList .vck');
  if (!all.length || $('#vAll').checked) return null;
  const got = all.filter(c => c.checked).map(c => c.value);
  return got.length ? got : null;
}

$('#btnPack').onclick = async () => {
  const r = await api('package', {
    package: $('#pkg').value.trim(), display: $('#disp').value.trim(),
    group_by_voice: $('#groupVoice').checked,
    only_voices: pickedVoices()
  });
  $('#packResult').innerHTML = r.ok
    ? `<span class="ok">✓ 已生成 ${r.count} 条 → ${esc(r.dir)}</span><br>
       <span class="hint">本次音色：${esc((r.voices || []).join('、') || '全部')}<br>
       重启游戏，在 EXBoss / BigWigs / WA 的语音下拉里选「${esc($('#disp').value)}」。</span>`
    : `<span class="err">✗ ${esc(r.err)}</span>`;
  r.ok && toast('打包完成', 'ok');
};
$('#btnZip').onclick = async () => {
  const r = await api('zip', {
    group_by_voice: $('#groupVoice').checked,
    only_voices: pickedVoices()
  });
  $('#packResult').innerHTML = r.ok
    ? `<span class="ok">✓ 已导出 → ${esc(r.file)}</span>`
    : `<span class="err">✗ ${esc(r.err)}</span>`;
};

/* ---------- 设置 ---------- */
$('#btnSave').onclick = async () => {
  const r = await api('save_config', {
    wow_addons_dir: $('#wowDir').value.trim(),
    ffmpeg: $('#ffDir').value.trim(),
    package: $('#pkg').value.trim(),
    display_name: $('#disp').value.trim(),
    author_tag: $('#tag').value.trim(),
    interface_version: $('#iface').value.trim(),
    voice_roots: $('#voiceRoots').value.trim()
  });
  $('#setResult').innerHTML = `<span class="ok">✓ 已保存（前缀规范化为 ${esc(r.config.author_tag)}）</span>`;
  ENV = await get('env'); renderBadges(); renderChecks(); fillVoices();
  toast('设置已保存', 'ok');
};
$('#btnScanWow').onclick = async () => {
  const r = await get('scan_wow');
  r.ok ? ($('#wowDir').value = r.dir, toast('已探测到 AddOns 目录', 'ok'))
    : toast('没探测到，请手动填写', 'err');
};
$('#btnOpenWow').onclick = () => api('open_folder', { path: $('#wowDir').value });
$('#btnScanFf').onclick = async () => {
  const e = await get('env');
  e.ffmpeg ? ($('#ffDir').value = e.ffmpeg, toast('已填入 ffmpeg 路径', 'ok'))
    : toast('没找到 ffmpeg', 'err');
};
$('#btnOpenFf').onclick = () => api('open_folder', { path: $('#ffDir').value });
$('#btnOpenVr').onclick = () => api('open_folder', { path: $('#voiceRoots').value });

boot();

'use strict';
const configure = new URLSearchParams(location.search).get('configure') === '1';
document.body.classList.toggle('configure', configure);
document.getElementById('dashboard').hidden = !configure;
const endpoint = '/api/counters/settings/AimAssist';
const canvas = document.getElementById(configure ? 'view' : 'overlay-view');
const ctx = canvas.getContext('2d');
const form = document.getElementById('settings');
const saved = document.getElementById('saved');
const messages = {
  'waiting for hand movement': 'Aguardando movimento da mão', 'outside timing window': 'Fora da janela de tempo',
  'outside assist radius': 'Fora do FOV', 'on-note hold': 'Mantendo correção sobre a nota',
  'slider lateral correction': 'Correção lateral no slider', 'slider tracking': 'Acompanhando slider', 'assisting': 'Corrigindo a mira',
  'following jump curve': 'Seguindo curva do jump',
  'idle skating': 'Dançando entre notas',
  'inertial glide': 'Deslizando com inércia',
  'spinner active': 'Spinner detectado · mouse livre', 'waiting for spinner': 'Aguardando spinner',
  'hand moving away from target': 'Mão se afastando do alvo', 'paused, unfocused or not playing': 'Pausado / sem foco / fora da partida',
  'assist disabled': 'Assistência desativada', 'program stopped': 'Programa encerrado',
  'game telemetry stale': 'Aguardando atualização do jogo', 'cursor sample unavailable': 'Leitura do cursor indisponível',
  'waiting for osu! focus': 'Aguardando foco no osu!',
};
let schema = [], values = {}, presets = {}, defaults = {}, state = {}, dirty = false, saveTimer, loaded = false;
let stream = null, streamURL = null, lastUIUpdate = 0;
function message(text, error = false) { saved.textContent = text; saved.classList.toggle('error', error); }
function syncControls() {
  for (const item of schema) {
    const input = document.getElementById('setting-' + item.uniqueID);
    if (!input) continue;
    if (item.type === 'checkbox') input.checked = values[item.uniqueID];
    else input.value = values[item.uniqueID];
    const range = document.getElementById('range-' + item.uniqueID);
    if (range) range.value = values[item.uniqueID];
  }
}
function buildForm() {
  const groups = new Map();
  for (const item of schema) {
    const group = item.section || 'Ajustes';
    if (!groups.has(group)) {
      const fieldset = document.createElement('fieldset'), legend = document.createElement('legend');
      legend.textContent = group; fieldset.append(legend); form.append(fieldset); groups.set(group, fieldset);
    }
    const box = document.createElement('div'); box.className = 'control';
    const label = document.createElement('label'); label.htmlFor = 'setting-' + item.uniqueID;
    const title = document.createElement('span'); title.textContent = item.title; label.append(title);
    const input = document.createElement('input'); input.id = label.htmlFor;
    input.type = item.type === 'number' ? 'number' : item.type === 'checkbox' ? 'checkbox' : item.type === 'color' ? 'color' : 'text';
    input.setAttribute('aria-describedby', 'hint-' + item.uniqueID);
    if (item.type === 'number') { input.min = item.min; input.max = item.max; input.step = item.step; input.required = true; }
    if (item.uniqueID === 'holdkey') { input.maxLength = 40; input.placeholder = 'Sem tecla obrigatória'; }
    if (item.type !== 'text') label.append(input);
    box.append(label);
    const hint = document.createElement('p'); hint.id = 'hint-' + item.uniqueID; hint.textContent = item.description; box.append(hint);
    if (item.type === 'text') box.append(input);
    input.addEventListener('input', () => {
      if (!input.validity.valid) { message('Valor fora do intervalo permitido. Alteração não aplicada.', true); return; }
      values[item.uniqueID] = item.type === 'checkbox' ? input.checked : item.type === 'number' ? input.valueAsNumber : input.value;
      const range = document.getElementById('range-' + item.uniqueID); if (range) range.value = input.value;
      changed();
    });
    if (item.type === 'number') {
      const range = document.createElement('input'); range.type = 'range'; range.id = 'range-' + item.uniqueID;
      range.min = item.min; range.max = item.max; range.step = item.step; range.setAttribute('aria-label', item.title);
      range.addEventListener('input', () => { values[item.uniqueID] = range.valueAsNumber; input.value = range.value; changed(); });
      box.append(range);
    }
    groups.get(group).append(box);
  }
  syncControls();
}
function changed() { dirty = true; message('Salvando…'); clearTimeout(saveTimer); saveTimer = setTimeout(save, 400); }
async function save() {
  if (!loaded || !form.reportValidity()) return;
  clearTimeout(saveTimer);
  try {
    const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(schema.map(item => ({uniqueID: item.uniqueID, value: values[item.uniqueID]})))});
    const result = await response.json();
    if (!response.ok || result.error || result.result !== 'success') throw new Error(result.error || 'O tosu não confirmou a gravação');
    dirty = false; message('Salvo no tosu. Aguardando aplicação no assist…');
  } catch (error) { message('Não foi possível salvar: ' + error.message, true); }
}
document.getElementById('save').addEventListener('click', save);
document.getElementById('reset-defaults').addEventListener('click', () => {
  Object.assign(values, defaults); syncControls(); changed();
});
form.addEventListener('submit', event => { event.preventDefault(); save(); });
for (const button of document.querySelectorAll('[data-preset]')) {
  button.addEventListener('click', () => {
    if (!presets[button.dataset.preset]) return;
    Object.assign(values, presets[button.dataset.preset]); syncControls(); changed();
  });
}
async function loadSettings() {
  const response = await fetch(endpoint, {cache: 'no-store'}), data = await response.json();
  if (!response.ok || data.error || !Array.isArray(data.settings)) throw new Error(data.error || 'Configurações indisponíveis');
  if (!loaded) {
    schema = data.settings; values = Object.fromEntries(schema.map(item => [item.uniqueID, item.value]));
    Object.assign(values, data.values); buildForm(); loaded = true;
    message('Ajustes prontos. Alterações são aplicadas automaticamente.');
    presets = await (await fetch('presets.json', {cache: 'no-store'})).json();
    defaults = await (await fetch('defaults.json', {cache: 'no-store'})).json();
    document.getElementById('reset-defaults').disabled = false;
  } else if (!dirty && !form.contains(document.activeElement)) {
    Object.assign(values, data.values); syncControls();
  }
}
function connected() { return state.connected && Date.now() / 1000 - state.updated < .7; }
function receiveState(data) {
  state = data;
  if (!configure) values = state.settings || {};
}
async function connectStream() {
  try {
    const response = await fetch('transport.json?t=' + Date.now(), {cache: 'no-store'});
    if (!response.ok) return;
    const transport = await response.json(), url = new URL(transport.url);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.pathname !== '/events') return;
    if (streamURL === url.href && stream && stream.readyState !== EventSource.CLOSED) return;
    if (stream) stream.close();
    streamURL = url.href;
    const source = new EventSource(streamURL); stream = source;
    source.onmessage = event => { if (stream === source) { try { receiveState(JSON.parse(event.data)); } catch (_) {} } };
    source.addEventListener('cursor', event => {
      if (stream === source) { try { receiveState({...state, ...JSON.parse(event.data)}); } catch (_) {} }
    });
    source.onerror = () => { if (stream === source) state.connected = false; };
  } catch (_) { /* Older backends still use the snapshot fallback. */ }
}
async function poll() {
  if (!stream) {
    try {
      const response = await fetch('state.json?t=' + Date.now(), {cache: 'no-store'});
      if (response.ok) { const data = await response.json(); if (!stream) receiveState(data); }
    } catch (_) { /* Freshness timeout hides an unavailable snapshot. */ }
  }
  setTimeout(poll, stream ? 1000 : 33);
}
function refreshStatus() {
  if (configure) {
    if (connected()) {
      if (state.settings_error) message('Alteração não aplicada: ' + state.settings_error, true);
      else if (!dirty && schema.every(item => state.settings?.[item.uniqueID] === values[item.uniqueID])) message('Aplicado ao assist · salvo no tosu');
    }
    const online = connected();
    const connection = document.getElementById('connection');
    connection.textContent = online ? 'Programa conectado' : 'Programa desconectado';
    connection.classList.toggle('offline', !online);
    const live = online && state.active;
    document.getElementById('view-mode').textContent = live ? 'Partida ao vivo' : 'Prévia · assist inativo';
    document.getElementById('view-title').textContent = live ? 'FOV ao vivo' : 'Prévia do FOV';
    document.getElementById('preview-note').hidden = live;
    document.getElementById('reason').textContent = online ? messages[state.reason] || state.reason || 'Aguardando partida' : 'Inicie o programa com --overlay';
    document.getElementById('offset').textContent = online && state.offset ? Math.hypot(...state.offset).toFixed(1) + ' px' : '—';
    document.getElementById('timing-calibration').textContent = online
      ? 'Timing: ' + Number(state.timing_correction_ms || 0).toFixed(1) + ' ms'
        + (Number.isFinite(state.hit_error_median_ms) ? ' · erro mediano: ' + state.hit_error_median_ms.toFixed(1) + ' ms' : ' · aprendendo') : '—';
    document.getElementById('target-kind').textContent = live ? state.idle ? 'Dança na folga' : state.tracking ? 'Curva do slider' : state.target ? 'Próxima nota' : 'Sem alvo' : '—';
    document.getElementById('latency').textContent = online ? Math.max(0, Date.now() - state.updated * 1000).toFixed(0) + ' ms · ' + (stream ? 'ao vivo' : 'arquivo') : '—';
  }
}
function draw() {
  const now = performance.now();
  if (now - lastUIUpdate > 150) { refreshStatus(); lastUIUpdate = now; }
  const box = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(box.width * dpr) || canvas.height !== Math.round(box.height * dpr)) {
    canvas.width = Math.round(box.width * dpr); canvas.height = Math.round(box.height * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, box.width, box.height);
  const live = connected() && state.active && state.window && state.cursor;
  const options = configure ? values : state.settings || {};
  const hud = document.getElementById('hud');
  hud.style.display = !configure && connected() && options.showstatus ? 'block' : 'none';
  if (hud.style.display === 'block') hud.textContent = 'Aim Assist · ' + (messages[state.reason] || state.reason || '') + (state.settings_error ? '\n' + state.settings_error : '')
    + (options.debug ? '\nCursor: ' + Math.max(0, Date.now() - state.updated * 1000).toFixed(0) + ' ms · estado: ' + Number(state.source_age_ms || 0).toFixed(0) + ' ms' : '');
  if (live || configure && loaded) {
    const scale = live ? Math.min(box.width / (state.window[2] - state.window[0]), box.height / (state.window[3] - state.window[1])) : box.width / 640;
    const padX = live ? (box.width - (state.window[2] - state.window[0]) * scale) / 2 : 0;
    const padY = live ? (box.height - (state.window[3] - state.window[1]) * scale) / 2 : 0;
    const project = point => live ? [padX + (point[0] - state.window[0]) * scale, padY + (point[1] - state.window[1]) * scale] : [box.width / 2, box.height / 2];
    const cursor = live ? project(state.cursor) : project(null), hand = live && state.hand ? project(state.hand) : cursor;
    const circle = (point, radius, color, dashed = false) => {
      ctx.beginPath(); ctx.setLineDash(dashed ? [5, 5] : []); ctx.strokeStyle = color;
      ctx.lineWidth = Number(options.fovwidth) || 1.5; ctx.arc(point[0], point[1], radius, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    };
    ctx.globalAlpha = Number(options.fovopacity ?? .65);
    if (options.showfov) circle(cursor, live ? state.radius * scale : Number(options.radius) * scale, options.fovcolor || '#55dfcf');
    if (options.showlimit) circle(hand, live ? state.max_offset * scale : Number(options.maxoffset) * scale, '#9cbdcc', true);
    ctx.globalAlpha = .8;
    if (live && options.showpath && state.path?.length) {
      ctx.beginPath(); ctx.strokeStyle = '#e9b67b'; ctx.lineWidth = 2;
      state.path.forEach((p, i) => { const q = project(p); if (i) ctx.lineTo(...q); else ctx.moveTo(...q); }); ctx.stroke();
    }
    if (live && options.showtarget && state.target) {
      const target = project(state.target); ctx.beginPath(); ctx.strokeStyle = '#f3b274'; ctx.lineWidth = 1;
      ctx.moveTo(...cursor); ctx.lineTo(...target); ctx.stroke(); circle(target, 5, '#ffd1a0');
    }
    ctx.globalAlpha = 1; ctx.beginPath(); ctx.fillStyle = '#dffcf8'; ctx.arc(cursor[0], cursor[1], 2, 0, Math.PI * 2); ctx.fill();
  }
  requestAnimationFrame(draw);
}
if (configure) {
  loadSettings().catch(error => message('Não foi possível carregar: ' + error.message, true));
  setInterval(() => loadSettings().catch(error => message(error.message, true)), 2000);
}
connectStream(); setInterval(connectStream, 1000); poll(); draw();

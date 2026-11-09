(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const base = document.body.dataset.base;
  const demo = new URLSearchParams(location.search).get('demo') === '1';
  const state = {products: [], selected: null, filter: 'all', days: '7', loading: false, historyRequest: 0};
  const statuses = {tracking: '监控中', target: '已到目标', error: '采集异常', waiting: '等待采集', paused: '已暂停', out_of_stock: '暂时缺货'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  const numeric = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  function money(value, currency = '') {
    if (!numeric(value)) return '—';
    try {
      if (/^[A-Z]{3}$/.test(currency)) return new Intl.NumberFormat('zh-CN', {style: 'currency', currency, maximumFractionDigits: 6}).format(Number(value));
    } catch (_) { /* 未识别的币种按普通数字展示。 */ }
    return new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 6}).format(Number(value));
  }
  function safeURL(value) {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : '#'; }
    catch (_) { return '#'; }
  }
  function domain(value) { try { return new URL(value).hostname; } catch (_) { return '商品网页'; } }
  function clock(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {hour12: false, timeZone: 'Asia/Shanghai'}) : '等待首次采集';
  }
  let toastTimer;
  function toast(message) {
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
  }
  async function api(path, options = {}) {
    if (demo) throw new Error('演示模式不执行监控操作，请返回真实工作台。');
    const response = await fetch(base + 'api/products' + path, {
      credentials: 'same-origin', signal: AbortSignal.timeout(20000), ...options,
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': $('meta[name="csrf-token"]').content, ...(options.headers || {})}
    });
    if (response.status === 204) return null;
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) throw new Error(response.redirected ? '登录状态已变化，请刷新页面并登录。' : `请求失败（HTTP ${response.status}），请检查登录状态或刷新页面。`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `请求失败（HTTP ${response.status}）`);
    return result;
  }
  function ruleText(product) {
    if (product.alert_mode === 'target') return `低于 ${money(product.target_price, product.currency)} 时，或恢复有货时提醒`;
    if (product.alert_mode === 'percentage') return `相较上次有效价波动超过 ${product.threshold_percent}% 时，或恢复有货时提醒`;
    return '价格发生变化，或恢复有货时提醒';
  }
  function demoData() {
    const now = Math.floor(Date.now() / 1000);
    const product = (id, title, price, previous, target, status, stock = true) => ({
      id, title, price: String(price), previous_price: String(previous), target_price: target ? String(target) : null,
      currency: 'CNY', url: `https://example.com/demo/${id}`, change_percent: ((price - previous) / previous * 100).toFixed(2),
      status, paused: false, in_stock: stock, target_reached: status === 'target', last_checked: now - 180,
      alert_mode: target ? 'target' : 'all', threshold_percent: null, error: '', fetch_backend: 'html_requests'
    });
    return {products: [product('demo-headphones', '桌面主动降噪耳机 · 雾灰色', 899, 1099, 950, 'target'),
      product('demo-drive', '便携固态硬盘 · 1TB / 深空灰', 459, 499, 399, 'tracking'),
      product('demo-lamp', '无线桌面阅读灯 · 暖白色', 269, 269, 249, 'tracking'),
      product('demo-keyboard', '轻量机械键盘 · 84键 / 茶轴', 399, 369, null, 'out_of_stock', false)],
      stats: {total: 4, active: 4, down: 2, target: 1, errors: 0}, checked_at: now};
  }
  async function load() {
    if (state.loading) return;
    state.loading = true;
    $('#refresh').disabled = true;
    try {
      const result = demo ? demoData() : await api('');
      state.products = result.products;
      $('#stat-total').textContent = result.stats.total;
      $('#stat-active').textContent = `${result.stats.active} 个商品正在监控`;
      $('#stat-down').textContent = result.stats.down;
      $('#stat-target').textContent = result.stats.target;
      $('#stat-errors').textContent = result.stats.errors;
      $('#nav-count').textContent = result.stats.total;
      $('#product-count').textContent = result.stats.total;
      $('#last-update').textContent = `更新于 ${clock(result.checked_at)}`;
      $('#connection-error').hidden = true;
      if (!state.products.some(item => item.id === state.selected)) state.selected = state.products[0]?.id || null;
      renderProducts();
      await renderDetail();
    } catch (error) {
      $('#connection-error').textContent = `数据读取失败，当前展示的旧数据可能已过期。${error.message}`;
      $('#connection-error').hidden = false;
    } finally {
      state.loading = false;
      $('#refresh').disabled = false;
    }
  }
  function renderProducts() {
    const query = $('#search').value.trim().toLocaleLowerCase();
    const visible = state.products.filter(product => {
      const match = state.filter === 'all' || (state.filter === 'down' ? numeric(product.change_percent) && Number(product.change_percent) < 0 && !['error', 'paused', 'waiting'].includes(product.status) : product.status === state.filter);
      return match && `${product.title} ${product.url}`.toLocaleLowerCase().includes(query);
    });
    $('#empty-state').hidden = state.products.length !== 0;
    $('#no-results').hidden = !state.products.length || visible.length > 0;
    $('.table-wrap').hidden = visible.length === 0;
    $('#products').innerHTML = visible.map((product, index) => {
      const change = numeric(product.change_percent) ? Number(product.change_percent) : null;
      const changeHTML = change === null || change === 0 ? '<span class="change flat">—</span>' : `<span class="change ${change > 0 ? 'up' : ''}">${change > 0 ? '↗ +' : '↘ '}${escape(Math.abs(change).toFixed(1))}%</span>`;
      return `<tr data-id="${escape(product.id)}" tabindex="0" aria-selected="${state.selected === product.id}" class="${state.selected === product.id ? 'selected' : ''}"><td><div class="product-cell"><span class="product-avatar" aria-hidden="true">${['◉', '▤', '◒', '▦'][index % 4]}</span><div><div class="product-title">${escape(product.title)}</div><div class="product-domain">${escape(domain(product.url))}</div></div></div></td><td><div class="${product.price === null ? 'price-waiting' : 'money'}">${product.price === null ? '等待采集' : escape(money(product.price, product.currency))}</div>${product.target_price ? `<div class="price-target">目标 ${escape(money(product.target_price, product.currency))}</div>` : ''}</td><td>${changeHTML}</td><td><span class="status ${escape(product.status)}">${escape(statuses[product.status] || '未知')}</span></td></tr>`;
    }).join('');
  }
  function current() { return state.products.find(product => product.id === state.selected); }
  async function renderDetail() {
    const product = current();
    $('#detail-empty').hidden = Boolean(product);
    $('#detail-content').hidden = !product;
    if (!product) { $('#detail-name').textContent = '选择商品，查看变化'; return; }
    $('#detail-name').textContent = product.title;
    $('#detail-price').textContent = money(product.price, product.currency);
    $('#detail-change').textContent = `最近检查 ${clock(product.last_checked)} · ${statuses[product.status] || '未知状态'}`;
    $('#detail-rule').textContent = ruleText(product);
    $('#detail-error').textContent = product.error;
    $('#detail-error').hidden = !product.error;
    $('#open-product').href = safeURL(product.url);
    $('#pause-one').textContent = product.paused ? '恢复监控' : '暂停监控';
    $('#check-one').disabled = product.paused;
    $('#export-history').href = demo ? '#' : `${base}api/products/${encodeURIComponent(product.id)}/history.csv?days=${state.days}`;
    await loadHistory(product);
  }
  async function loadHistory(product) {
    const request = ++state.historyRequest;
    $('#chart').textContent = '正在读取价格快照…';
    $('#history-min').textContent = '—';
    $('#history-max').textContent = '—';
    try {
      let data;
      if (demo) {
        const now = Math.floor(Date.now() / 1000);
        const prices = [1199, 1199, 1149, 1149, 1099, 1099, 1099, 999, 999, 999, 1049, 1049, 999, 999, 949, 949, 899];
        const scale = Number(product.price) / 899;
        data = {points: prices.map((price, index) => ({timestamp: now - (prices.length - 1 - index) * 21600, price: String(Math.round(price * scale)), in_stock: true})), truncated: false, unreadable: 0};
      } else data = await api(`/${encodeURIComponent(product.id)}/history?days=${state.days}`);
      if (request !== state.historyRequest || product.id !== state.selected) return;
      drawChart(data.points, product.currency);
      if (data.unreadable) toast(`${data.unreadable} 条快照读取失败，图中保留了缺口。`);
      if (data.truncated) toast('记录较多，当前仅显示所选范围中最近 500 条快照。');
    } catch (error) {
      if (request === state.historyRequest) $('#chart').textContent = `历史读取失败：${error.message}`;
    }
  }
  function drawChart(points, currency) {
    const valid = points.filter(point => numeric(point.price) && numeric(point.timestamp));
    if (!valid.length) { $('#chart').textContent = '暂无有效价格快照，等待首次采集。'; return; }
    const prices = valid.map(point => Number(point.price));
    const min = Math.min(...prices), max = Math.max(...prices);
    $('#history-min').textContent = money(min, currency);
    $('#history-max').textContent = money(max, currency);
    const floor = min - (max - min || Math.max(min * .05, 1)) * .18;
    const ceiling = max + (max - min || Math.max(min * .05, 1)) * .18;
    const first = Number(valid[0].timestamp), last = Number(valid[valid.length - 1].timestamp);
    const x = timestamp => last === first ? 160 : 44 + (Number(timestamp) - first) / (last - first) * 222;
    const y = price => 122 - (Number(price) - floor) / (ceiling - floor) * 102;
    const groups = []; let group = [];
    points.forEach(point => {
      if (numeric(point.price) && numeric(point.timestamp)) group.push([x(point.timestamp), y(point.price)]);
      else if (group.length) { groups.push(group); group = []; }
    });
    if (group.length) groups.push(group);
    const paths = groups.map(segment => {
      const path = segment.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(' ');
      return `<path d="${path} L${segment.at(-1)[0].toFixed(2)},130 L${segment[0][0].toFixed(2)},130 Z" fill="#eff5e6" stroke="none"/><path d="${path}" fill="none" stroke="#789759" stroke-width="2"/>`;
    }).join('');
    const label = timestamp => new Date(timestamp * 1000).toLocaleDateString('zh-CN', {month: '2-digit', day: '2-digit', timeZone: 'Asia/Shanghai'});
    const grid = [min, (min + max) / 2, max].map(value => `<line x1="43" y1="${y(value)}" x2="274" y2="${y(value)}" stroke="#e9efdf" stroke-dasharray="3 4"/><text x="0" y="${y(value) + 3}">${escape(Number(value.toFixed(1)))}</text>`).join('');
    $('#chart').innerHTML = `<svg viewBox="0 0 280 156" role="img" aria-label="${valid.length} 条有效价格快照"><title>所选范围价格变化快照</title>${grid}${paths}<circle cx="${x(last)}" cy="${y(prices.at(-1))}" r="3" fill="#789759" stroke="white" stroke-width="2"/><text x="44" y="151">${escape(label(first))}</text><text x="269" y="151" text-anchor="end">${escape(label(last))}</text></svg>`;
  }
  function modeFields(form) {
    const mode = form.elements.alert_mode.value;
    for (const [selector, selected] of [['.target-field', mode === 'target'], ['.percentage-field', mode === 'percentage']]) {
      const field = form.querySelector(selector);
      field.hidden = !selected;
      field.querySelector('input').required = selected;
    }
  }
  function payload(form, adding) {
    const values = Object.fromEntries(new FormData(form));
    if (adding) values.interval_minutes = Number(values.interval_minutes);
    if (values.alert_mode !== 'target') delete values.target_price;
    if (values.alert_mode !== 'percentage') delete values.change_percent;
    return values;
  }
  async function submitForm(event, adding) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    const errorElement = form.querySelector('.form-error');
    errorElement.hidden = true;
    submit.disabled = true;
    try {
      const path = adding ? '' : `/${encodeURIComponent(state.selected)}/rule`;
      const result = await api(path, {method: 'POST', body: JSON.stringify(payload(form, adding))});
      if (adding) state.selected = result.id;
      form.closest('dialog').close();
      if (adding) form.reset();
      toast(adding ? '商品已加入关注，首次采集已排队。' : '提醒规则已保存。');
      await load();
    } catch (error) { errorElement.textContent = error.message; errorElement.hidden = false; }
    finally { submit.disabled = false; }
  }
  async function action(button, fn) {
    if (demo) { toast('当前为演示数据，请返回真实工作台操作。'); return; }
    button.disabled = true;
    try { await fn(); } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }
  $$('[data-open-add]').forEach(button => button.addEventListener('click', () => {
    if (demo) { toast('当前为演示数据，请返回真实工作台添加。'); return; }
    $('#product-form .form-error').hidden = true;
    modeFields($('#product-form'));
    $('#product-dialog').showModal();
  }));
  $$('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $('#show-help').addEventListener('click', () => $('#help-dialog').showModal());
  $('#refresh').addEventListener('click', load);
  $('#search').addEventListener('input', renderProducts);
  $$('.tab').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    $$('.tab').forEach(tab => { tab.classList.toggle('active', tab === button); tab.setAttribute('aria-pressed', String(tab === button)); });
    renderProducts();
  }));
  function selectRow(event) {
    const row = event.target.closest('tr[data-id]');
    if (!row) return;
    state.selected = row.dataset.id;
    renderProducts(); renderDetail();
  }
  $('#products').addEventListener('click', selectRow);
  $('#products').addEventListener('keydown', event => { if (event.key === 'Enter') selectRow(event); });
  $$('.range').forEach(button => button.addEventListener('click', () => {
    state.days = button.dataset.days;
    $$('.range').forEach(range => range.classList.toggle('active', range === button));
    if (current()) renderDetail();
  }));
  for (const form of [$('#product-form'), $('#rule-form')]) form.elements.alert_mode.addEventListener('change', () => modeFields(form));
  $('#product-form').addEventListener('submit', event => submitForm(event, true));
  $('#rule-form').addEventListener('submit', event => submitForm(event, false));
  $('#edit-rule').addEventListener('click', () => {
    if (demo) { toast('演示模式不修改规则。'); return; }
    const product = current(); if (!product) return;
    const form = $('#rule-form');
    form.elements.alert_mode.value = product.alert_mode;
    form.elements.target_price.value = product.target_price || '';
    form.elements.change_percent.value = product.threshold_percent || '';
    form.querySelector('.form-error').hidden = true;
    modeFields(form); $('#rule-dialog').showModal();
  });
  $('#check-one').addEventListener('click', event => action(event.currentTarget, async () => {
    if (!current()) return;
    await api(`/${encodeURIComponent(state.selected)}/check`, {method: 'POST', body: '{}'});
    toast('已加入采集队列，完成后可刷新查看。');
  }));
  $('#pause-one').addEventListener('click', event => action(event.currentTarget, async () => {
    const product = current(); if (!product) return;
    await api(`/${encodeURIComponent(product.id)}/pause`, {method: 'POST', body: JSON.stringify({paused: !product.paused})});
    toast(product.paused ? '监控已恢复。' : '监控已暂停。'); await load();
  }));
  $('#delete-one').addEventListener('click', () => {
    if (demo) { toast('演示模式不删除数据。'); return; }
    if (current()) $('#delete-dialog').showModal();
  });
  $('#confirm-delete').addEventListener('click', event => action(event.currentTarget, async () => {
    if (!current()) return;
    await api(`/${encodeURIComponent(state.selected)}`, {method: 'DELETE'});
    $('#delete-dialog').close(); state.selected = null; await load(); toast('商品及对应历史已删除。');
  }));
  $('#export-history').addEventListener('click', event => { if (demo) { event.preventDefault(); toast('演示模式不导出虚构数据。'); } });
  if (demo) { $('#demo-banner').hidden = false; $('#demo-link').textContent = '演示模式'; }
  load();
  if (!demo) setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30000);
})();

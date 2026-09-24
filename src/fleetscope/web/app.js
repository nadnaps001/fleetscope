/* All displayed quantities come from the evaluated API run. */
const $ = id => document.getElementById(id);
const number = value => new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(value);
const decimal = value => new Intl.NumberFormat('en-US', {maximumFractionDigits: 1}).format(value);
const timeLabel = value => new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', timeZoneName: 'short'
}).format(new Date(value));
const svgNS = 'http://www.w3.org/2000/svg';
let metadata, geometry, current, selectedZone = null, layer = 'predicted_pickups';
let forecastRequest = 0, detailRequest = 0, view = [0, 0, 820, 650], drag = null;
const paths = new Map();
const staticMode = document.documentElement.dataset.mode === 'static';
const historyCache = new Map();

async function getJSON(url) {
  if (staticMode) return getStaticJSON(url);
  return fetchJSON(url);
}
async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* HTTP fallback */ }
    throw new Error(message);
  }
  return response.json();
}
async function getStaticJSON(url) {
  const request = new URL(url, window.location.origin);
  const fixed = {'/api/metadata': 'metadata', '/api/zones': 'zones', '/api/metrics': 'metrics', '/api/quality': 'quality'};
  if (fixed[request.pathname]) return fetchJSON(`./data/${fixed[request.pathname]}.json`);
  const hour = request.searchParams.get('hour');
  if (request.pathname === '/api/forecast') {
    const index = metadata.hours.indexOf(hour);
    if (index < 0) throw new Error('This hour is outside the saved replay');
    return fetchJSON(`./data/hours/${index}.json`);
  }
  const match = request.pathname.match(/^\/api\/zones\/(\d+)$/);
  if (match) {
    const zoneId = Number(match[1]);
    if (!historyCache.has(zoneId)) {
      historyCache.set(zoneId, fetchJSON(`./data/history/zone-${zoneId}.json`).catch(error => {
        historyCache.delete(zoneId); throw error;
      }));
    }
    const history = await historyCache.get(zoneId);
    const cutoff = new Date(hour).getTime() - metadata.config.observation_delay_hours * 3600000;
    const start = cutoff - 24 * 3600000;
    const zone = geometry.features.find(feature => feature.id === zoneId);
    if (!zone) throw new Error('Unknown taxi zone');
    return {...zone.properties, target_hour: hour,
      history: history.filter(point => {const time = new Date(point.hour).getTime(); return time >= start && time < cutoff;})};
  }
  throw new Error('This view is not included in the saved replay');
}
function reportError(error) {
  $('error').hidden = false;
  $('error').textContent = `Unable to load this view: ${error.message}. Please try again.`;
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function interpolate(a, b, ratio) {
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * ratio)).join(',')})`;
}
function fill(value) {
  if (layer === 'error') {
    const ratio = Math.sqrt(Math.min(1, Math.abs(value) / metadata.error_max));
    return interpolate([243, 243, 242], value < 0 ? [29, 117, 152] : [190, 92, 44], ratio);
  }
  // Fixed square-root scale reveals low-volume zones without changing between hours.
  const ratio = Math.sqrt(Math.min(1, value / metadata.legend_max));
  return ratio < .5 ? interpolate([230, 238, 248], [99, 134, 217], ratio * 2)
    : interpolate([99, 134, 217], [36, 46, 112], (ratio - .5) * 2);
}
function buildMap() {
  const points = [];
  const rings = feature => feature.geometry.type === 'Polygon'
    ? feature.geometry.coordinates : feature.geometry.coordinates.flat();
  const projection = ([lon, lat]) => [lon * Math.cos(40.7 * Math.PI / 180), -lat];
  geometry.features.forEach(feature => rings(feature).forEach(ring => ring.forEach(p => points.push(projection(p)))));
  const bounds = points.reduce((b, [x, y]) => [Math.min(b[0], x), Math.max(b[1], x), Math.min(b[2], y), Math.max(b[3], y)], [Infinity, -Infinity, Infinity, -Infinity]);
  const [minX, maxX, minY, maxY] = bounds;
  const scale = Math.min(750 / (maxX - minX), 590 / (maxY - minY));
  const offsetX = (820 - (maxX - minX) * scale) / 2, offsetY = (650 - (maxY - minY) * scale) / 2;
  geometry.features.forEach(feature => {
    const path = document.createElementNS(svgNS, 'path');
    const d = rings(feature).map(ring => ring.map((point, i) => {
      const [x, y] = projection(point);
      return `${i ? 'L' : 'M'}${((x - minX) * scale + offsetX).toFixed(2)},${((y - minY) * scale + offsetY).toFixed(2)}`;
    }).join(' ') + ' Z').join(' ');
    path.setAttribute('d', d);
    path.setAttribute('fill-rule', 'evenodd');
    path.setAttribute('fill', '#dce4ee');
    const title = document.createElementNS(svgNS, 'title');
    title.textContent = feature.properties.name;
    path.appendChild(title);
    path.addEventListener('click', () => { if (!drag?.moved) selectZone(feature.id); });
    $('map').appendChild(path);
    paths.set(feature.id, path);
  });
  geometry.features.slice().sort((a, b) => a.properties.name.localeCompare(b.properties.name)).forEach(feature => {
    const option = element('option', '', `${feature.properties.name} · ${feature.properties.borough}`);
    option.value = feature.id;
    $('zone-select').appendChild(option);
  });
}
function render() {
  const rows = current.zones;
  const byId = new Map(rows.map(row => [row.zone_id, row]));
  for (const [id, path] of paths) {
    const row = byId.get(id);
    path.setAttribute('fill', row ? fill(row[layer]) : '#d8dce2');
    path.classList.toggle('selected', id === selectedZone);
    path.firstChild.textContent = row ? `${row.name}: ${decimal(row[layer])} ${layer === 'error' ? 'prediction − recorded' : layer.replace('_pickups', '')} pickups/hour` : 'Unavailable';
  }
  $('display-hour').textContent = timeLabel(current.target_hour);
  $('total-predicted').textContent = number(rows.reduce((sum, r) => sum + r.predicted_pickups, 0));
  $('total-recorded').textContent = number(rows.reduce((sum, r) => sum + r.recorded_pickups, 0));
  $('hour-mae').textContent = decimal(rows.reduce((sum, r) => sum + Math.abs(r.error), 0) / rows.length);
  $('legend-label').textContent = layer === 'error' ? 'Error: predicted − recorded · pickups/hour' : `${layer === 'predicted_pickups' ? 'Predicted' : 'Recorded'} pickups/hour · √ scale`;
  $('legend-min').textContent = layer === 'error' ? `−${number(metadata.error_max)}` : '0';
  $('legend-max').textContent = number(layer === 'error' ? metadata.error_max : metadata.legend_max);
  $('legend-gradient').style.background = layer === 'error'
    ? 'linear-gradient(to right,#1d7598,#f3f3f2,#be5c2c)' : '';
  $('ranking').replaceChildren();
  rows.slice().sort((a, b) => b.predicted_pickups - a.predicted_pickups).slice(0, 5).forEach((row, i) => {
    const li = element('li');
    const button = element('button', 'rank-button');
    button.append(element('span', 'rank-number', String(i + 1).padStart(2, '0')),
      element('span', 'rank-name', row.name), element('span', 'rank-count', number(row.predicted_pickups)));
    button.addEventListener('click', () => selectZone(row.zone_id));
    li.appendChild(button); $('ranking').appendChild(li);
  });
  if (selectedZone !== null) renderZoneValues();
}
function renderZoneValues() {
  const row = current.zones.find(r => r.zone_id === selectedZone);
  if (!row) {
    $('zone-values').hidden = true;
    $('zone-hint').hidden = false;
    $('zone-hint').textContent = 'This zone-hour is unavailable under the evaluation rules.';
    return;
  }
  $('zone-name').textContent = row.name;
  $('zone-borough').textContent = row.borough.toUpperCase();
  $('zone-forecast').textContent = decimal(row.predicted_pickups);
  $('zone-recorded').textContent = number(row.recorded_pickups);
  $('zone-baseline').textContent = number(row.previous_week);
  $('zone-values').hidden = false;
  $('zone-hint').hidden = true;
}
async function selectZone(zone) {
  selectedZone = Number(zone);
  $('zone-select').value = String(selectedZone);
  render();
  await loadDetail();
}
async function loadDetail() {
  if (selectedZone === null) return;
  const request = ++detailRequest;
  $('trend').replaceChildren();
  try {
    const detail = await getJSON(`/api/zones/${selectedZone}?hour=${encodeURIComponent(current.target_hour)}`);
    if (request !== detailRequest) return;
    const max = Math.max(1, ...detail.history.map(p => p.recorded_pickups ?? 0));
    detail.history.forEach((point, i) => {
      if (point.recorded_pickups === null) return;
      const rect = document.createElementNS(svgNS, 'rect');
      const height = point.recorded_pickups / max * 72;
      rect.setAttribute('x', i * 12.5 + 1); rect.setAttribute('y', 78 - height);
      rect.setAttribute('width', 9); rect.setAttribute('height', Math.max(.5, height));
      rect.setAttribute('rx', 1.5); rect.setAttribute('fill', '#8fa6e8');
      const title = document.createElementNS(svgNS, 'title');
      title.textContent = `${timeLabel(point.hour)}: ${number(point.recorded_pickups)} pickups`;
      rect.appendChild(title); $('trend').appendChild(rect);
    });
  } catch (error) { if (request === detailRequest) reportError(error); }
}
async function loadHour(index) {
  index = Math.max(0, Math.min(metadata.hours.length - 1, Number(index)));
  const request = ++forecastRequest;
  ++detailRequest;
  $('time').value = index;
  $('hour-index').textContent = `${index + 1} / ${metadata.hours.length} hours`;
  $('previous').disabled = index === 0;
  $('next').disabled = index === metadata.hours.length - 1;
  document.querySelector('.map-panel').setAttribute('aria-busy', 'true');
  try {
    const response = await getJSON(`/api/forecast?hour=${encodeURIComponent(metadata.hours[index])}`);
    if (request !== forecastRequest) return;
    current = response; $('error').hidden = true;
    render(); await loadDetail();
  } catch (error) { if (request === forecastRequest) reportError(error); }
  finally { if (request === forecastRequest) document.querySelector('.map-panel').setAttribute('aria-busy', 'false'); }
}
function updateView() { $('map').setAttribute('viewBox', view.join(' ')); }
function zoom(factor) {
  const width = Math.max(200, Math.min(1200, view[2] * factor));
  const height = width * 650 / 820;
  view = [view[0] + (view[2] - width) / 2, view[1] + (view[3] - height) / 2, width, height];
  updateView();
}
async function init() {
  [metadata, geometry, report] = await Promise.all([
    getJSON('/api/metadata'), getJSON('/api/zones'), getJSON('/api/metrics')
  ]);
  $('time').max = metadata.hours.length - 1;
  $('dataset-note').textContent = `${metadata.zone_count} modeled zones · ${metadata.config.observation_delay_hours}h assumed feed delay`;
  buildMap();
  const names = {baseline_week: 'Same local hour last week', baseline_seasonal: 'Training seasonal average',
    model_calendar: 'LightGBM · calendar + zone', model_recent: 'LightGBM · + recent history', model_weekly: 'LightGBM · + weekly history'};
  Object.entries(report.overall.test).forEach(([name, stats]) => {
    const selected = name === `model_${report.selected_model}`;
    const tr = element('tr', selected ? 'selected-row' : '');
    tr.append(element('td', '', names[name] + (selected ? ' · selected' : '')), element('td', '', stats.mae.toFixed(2)),
      element('td', '', stats.wape === null ? 'undefined' : `${(stats.wape * 100).toFixed(1)}%`));
    $('metrics').appendChild(tr);
  });
  metadata.examples.forEach(example => {
    const button = element('button', '', example.title);
    button.addEventListener('click', () => {
      $('example-note').textContent = example.note;
      loadHour(metadata.hours.indexOf(example.hour));
    });
    $('examples').appendChild(button);
  });
  document.querySelectorAll('[data-layer]').forEach(button => button.addEventListener('click', () => {
    layer = button.dataset.layer;
    document.querySelectorAll('[data-layer]').forEach(b => {
      b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', String(b === button));
    });
    if (current) render();
  }));
  $('zone-select').addEventListener('change', event => { if (event.target.value) selectZone(event.target.value); });
  let timer;
  $('time').addEventListener('input', event => {
    clearTimeout(timer); $('example-note').textContent = '';
    timer = setTimeout(() => loadHour(event.target.value), 90);
  });
  $('previous').addEventListener('click', () => { $('example-note').textContent = ''; loadHour(Number($('time').value) - 1); });
  $('next').addEventListener('click', () => { $('example-note').textContent = ''; loadHour(Number($('time').value) + 1); });
  $('zoom-in').addEventListener('click', () => zoom(.8));
  $('zoom-out').addEventListener('click', () => zoom(1.25));
  $('reset-map').addEventListener('click', () => { view = [0, 0, 820, 650]; updateView(); });
  $('map').addEventListener('pointerdown', event => { drag = {x: event.clientX, y: event.clientY, view: [...view], moved: false}; });
  $('map').addEventListener('pointermove', event => {
    if (!drag || !event.buttons) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) drag.moved = true;
    if (!drag.moved) return;
    const ratio = view[2] / $('map').getBoundingClientRect().width;
    view[0] = drag.view[0] - dx * ratio; view[1] = drag.view[1] - dy * ratio; updateView();
  });
  window.addEventListener('pointerup', () => setTimeout(() => { drag = null; }, 0));
  await loadHour(0);
}
let report;
init().catch(reportError);

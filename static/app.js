'use strict';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = value => Number(value || 0).toLocaleString('ko-KR');
const money = value => value == null ? '미입력' : '₩' + Number(value).toLocaleString('ko-KR', {maximumFractionDigits:1});
const percent = value => (100 * (value || 0)).toFixed(1) + '%';
const token = $('meta[name="lens-token"]').content;
const titles = {overview:['사진 속에 담긴 장비의 기록','무엇으로, 얼마나 담았는지. 사진으로 알아보는 나의 장비 생활.'],equipment:['장비의 가치를 기록하세요','신품 가격과 실제 구매 가격, 사용한 만큼 달라지는 사진 한 장의 비용.'],photos:['한 장 한 장, 나의 촬영 기록','사진에 남아 있는 카메라와 렌즈, 그리고 그날의 촬영 설정.'],import:['사진에서 이야기를 가져오세요','사진 폴더를 연결하고, 장비와 함께한 시간을 살펴보세요.'],csv:['CSV에 담아 둔 장비 기록','내보낸 통계를 저장하고, 카메라와 렌즈의 사용 기록을 다시 살펴보세요.']};
const viewLabels = {overview:'개요', equipment:'장비와 가격', photos:'사진 목록', import:'가져오기', csv:'CSV 기록'};
let currentView = 'overview', data = null, demo = false, applied = new URLSearchParams(), page = 1, pages = 1;
let equipmentSort = {key: 'name', direction: 1};
let toastTimer, pollTimer, previousScanStatus = 'idle', reportVersion = 0, photoVersion = 0;
let pickingFolder = false, startingScan = false;
let savedFolders = [], folderHistoryVersion = 0, folderInputEdited = false;
let csvImports = [], selectedCsvImport = null, csvHistoryVersion = 0, csvDetailVersion = 0;
let pickingCsv = false, importingCsv = false, csvPathEdited = false;

function updateImportControls() {
  const busy = pickingFolder || startingScan || pickingCsv || importingCsv || ['scanning','cancelling'].includes(previousScanStatus);
  $('#scan-button').disabled = busy || demo;
  if ($('#choose-folder')) $('#choose-folder').disabled = busy;
  $('#saved-folders').disabled = busy || !savedFolders.length;
  $('#folder').disabled = busy;
  $('#csv-import-button').disabled = busy || demo;
  $('#csv-import-button').textContent = importingCsv ? 'CSV 저장 중…' : 'CSV 기록 저장 →';
  if ($('#choose-csv')) $('#choose-csv').disabled = busy || demo;
  $('#csv-path').disabled = busy || demo;
  $('#csv-history').disabled = importingCsv || pickingCsv || !csvImports.length;
  $('#csv-demo-note').hidden = !demo;
  $$('[data-import]').forEach(button => button.disabled = busy);
}

function syncSavedFolderSelection() {
  const folder = $('#folder').value.trim().replace(/^"|"$/g, '');
  $('#saved-folders').value = savedFolders.some(item => item.path === folder) ? folder : '';
  $('#saved-folders').title = $('#saved-folders').value;
}

async function refreshSavedFolders(restoreLast=false) {
  const version = ++folderHistoryVersion;
  try {
    const result = await api('/api/folders');
    if (version !== folderHistoryVersion) return;
    savedFolders = result.folders;
    $('#saved-folders').innerHTML = `<option value="">${savedFolders.length ? '열었던 폴더를 선택하세요' : '아직 저장된 폴더가 없습니다'}</option>` +
      savedFolders.map(item => `<option value="${esc(item.path)}">${esc(item.path)}</option>`).join('');
    if (restoreLast && !folderInputEdited && !$('#folder').value.trim()) $('#folder').value = result.last_folder || '';
    syncSavedFolderSelection();
    $('#metadata-location').textContent = result.data_directory ? `저장 위치: ${result.data_directory}` : '';
    $('#metadata-location').hidden = !result.data_directory;
    updateImportControls();
  } catch (error) {
    if (version !== folderHistoryVersion) return;
    if (!savedFolders.length) $('#saved-folders').innerHTML = '<option value="">저장된 폴더를 불러오지 못했습니다</option>';
  }
}

async function startScan() {
  if (startingScan || pickingCsv || importingCsv || ['scanning','cancelling'].includes(previousScanStatus)) return;
  if (demo) { toast('예시를 종료한 뒤 실제 폴더를 가져와 주세요.'); return; }
  startingScan = true;
  updateImportControls();
  try {
    await api('/api/scan', {method:'POST', body:JSON.stringify({folder:$('#folder').value.trim().replace(/^"|"$/g,'')})});
    previousScanStatus = 'scanning';
    await refreshSavedFolders();
    await pollStatus();
  } catch (error) { toast(error.message, true); }
  finally { startingScan = false; updateImportControls(); }
}

async function requestPhotoImport() {
  if (pickingFolder || startingScan || pickingCsv || importingCsv) return;
  if (['scanning','cancelling'].includes(previousScanStatus)) {
    showView('import'); toast('현재 가져오기가 끝난 뒤 다른 폴더를 선택해 주세요.'); return;
  }
  pickingFolder = true;
  updateImportControls();
  try {
    if (demo) await toggleDemo();
    showView('import');
    if (!$('#choose-folder')) return; // Development web mode retains the path input.
    const result = await api('/api/desktop/folder', {method:'POST', body:'{}'});
    if (result.folder) {
      $('#folder').value = result.folder;
      folderInputEdited = true;
      await refreshSavedFolders();
      await startScan();
    }
  } catch (error) { toast(error.message, true); }
  finally { pickingFolder = false; updateImportControls(); }
}

function csvImportDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '') : date.toLocaleString('ko-KR', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}

function csvMoney(value) {
  return value == null ? '미입력' : '₩' + Number(value).toLocaleString('ko-KR', {maximumFractionDigits:2});
}

function renderCsvSnapshot(snapshot) {
  selectedCsvImport = snapshot;
  $('#csv-snapshot').hidden = !snapshot;
  $('#csv-empty').hidden = !!snapshot;
  if (!snapshot) return;
  $('#csv-snapshot-title').textContent = snapshot.filename;
  $('#csv-source-path').textContent = snapshot.source_path;
  $('#csv-imported-at').textContent = csvImportDate(snapshot.imported_at);
  $('#csv-row-count').textContent = `${fmt(snapshot.row_count)}개 항목`;
  $('#csv-summary').innerHTML = [
    ['CSV에 기록된 사진', snapshot.total_photos, '장'],
    ['카메라 항목', snapshot.counts.camera, '개'],
    ['렌즈 항목', snapshot.counts.lens, '개'],
    ['카메라 + 렌즈 조합', snapshot.counts.combination, '개']
  ].map(([label,count,unit]) => `<div class="csv-stat"><span>${label}</span><strong>${count == null ? '—' : fmt(count)}<small>${unit}</small></strong></div>`).join('');
  const kinds = {camera:'카메라',lens:'렌즈',combination:'조합'};
  $('#csv-records-body').innerHTML = snapshot.rows.map(row => `<tr data-csv-kind="${esc(row.kind)}"><td><span class="tag">${esc(kinds[row.kind] || row.kind)}</span></td><td><strong>${esc(row.name)}</strong></td><td>${fmt(row.count)}장</td><td>${row.share_percent == null ? '—' : Number(row.share_percent).toLocaleString('ko-KR',{maximumFractionDigits:2})+'%'}</td><td>${csvMoney(row.new_price)}</td><td>${row.cost == null ? '—' : csvMoney(row.cost)}</td></tr>`).join('');
}

async function loadCsvImport(id) {
  const version = ++csvDetailVersion;
  renderCsvSnapshot(null);
  $('#csv-empty-text').textContent = '저장된 CSV 기록을 불러오고 있습니다.';
  if (!id) {
    $('#csv-empty-text').textContent = 'CSV 파일을 가져오면 저장한 통계를 여기에서 확인할 수 있습니다.';
    return;
  }
  try {
    const snapshot = await api('/api/csv-imports/' + encodeURIComponent(id));
    if (version !== csvDetailVersion) return;
    renderCsvSnapshot(snapshot);
  } catch (error) {
    if (version !== csvDetailVersion) return;
    $('#csv-empty-text').textContent = error.message;
    toast(error.message, true);
  }
}

async function refreshCsvHistory(restoreLast=true, selectId=null) {
  const version = ++csvHistoryVersion;
  try {
    const result = await api('/api/csv-imports');
    if (version !== csvHistoryVersion) return;
    csvImports = result.imports;
    const wantedId = selectId ?? $('#csv-history').value ?? selectedCsvImport?.id;
    const selected = csvImports.find(item => String(item.id) === String(wantedId)) || csvImports[0];
    $('#csv-history').innerHTML = csvImports.length ? csvImports.map(item => `<option value="${item.id}">${esc(csvImportDate(item.imported_at))} · ${esc(item.filename)}</option>`).join('') : '<option value="">아직 저장된 CSV 기록이 없습니다</option>';
    $('#csv-history').value = selected ? String(selected.id) : '';
    $('#csv-history').title = selected ? `${csvImportDate(selected.imported_at)} · ${selected.filename}` : '';
    $('#csv-history-help').textContent = '저장한 순서대로 표시합니다. 앱을 다시 열어도 기록이 유지됩니다.';
    if (restoreLast && !csvPathEdited && !$('#csv-path').value.trim()) $('#csv-path').value = csvImports[0]?.source_path || '';
    updateImportControls();
    if (!selected || selectedCsvImport?.id !== selected.id) await loadCsvImport(selected?.id);
  } catch (error) {
    if (version !== csvHistoryVersion) return;
    $('#csv-history-help').textContent = '저장된 CSV 기록을 불러오지 못했습니다. CSV 기록 화면을 다시 열어 주세요.';
  }
}

async function startCsvImport() {
  if (importingCsv || pickingFolder || startingScan || ['scanning','cancelling'].includes(previousScanStatus)) return;
  if (demo) { toast('예시를 종료한 뒤 CSV 기록을 가져와 주세요.'); return; }
  const path = $('#csv-path').value.trim().replace(/^"|"$/g, '');
  if (!path) { toast('가져올 CSV 파일의 경로를 입력해 주세요.', true); return; }
  importingCsv = true;
  csvPathEdited = true;
  updateImportControls();
  try {
    const result = await api('/api/csv-imports', {method:'POST',body:JSON.stringify({path})});
    ++csvDetailVersion;
    renderCsvSnapshot(result.import);
    $('#csv-path').value = result.import.source_path;
    await refreshCsvHistory(false, result.import.id);
    toast(result.duplicate ? '이미 저장된 CSV입니다. 기존 기록을 불러왔습니다.' : `CSV 기록을 저장했습니다 · ${fmt(result.import.row_count)}개 항목`);
  } catch (error) { toast(error.message, true); }
  finally { importingCsv = false; updateImportControls(); }
}

async function requestCsvImport() {
  if (pickingCsv || importingCsv || pickingFolder || startingScan) return;
  if (['scanning','cancelling'].includes(previousScanStatus)) {
    showView('csv'); toast('사진 가져오기가 끝난 뒤 CSV를 선택해 주세요.'); return;
  }
  pickingCsv = true;
  updateImportControls();
  try {
    if (demo) await toggleDemo();
    showView('csv');
    if (!$('#choose-csv')) { pickingCsv = false; updateImportControls(); $('#csv-path').focus(); return; }
    const result = await api('/api/desktop/csv', {method:'POST',body:'{}'});
    if (result.path) {
      $('#csv-path').value = result.path;
      csvPathEdited = true;
      await startCsvImport();
    }
  } catch (error) { toast(error.message, true); }
  finally { pickingCsv = false; updateImportControls(); }
}

function toast(message, error=false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = error ? 'error' : '';
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => $('#toast').hidden = true, error ? 6500 : 3500);
}

async function api(url, options={}) {
  const response = await fetch(url, {...options, headers:{'Content-Type':'application/json','X-LensTracker-Token':token,...options.headers}});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `요청을 처리하지 못했습니다 (${response.status}).`);
  return result;
}

async function refresh() {
  const version = ++reportVersion;
  try {
    const result = demo ? demoReport() : await api('/api/report?' + applied);
    if (version !== reportVersion) return;
    data = result;
    render();
    if (currentView === 'photos') await loadPhotos();
  } catch (error) { toast(error.message, true); }
}

function showView(view) {
  currentView = view;
  $$('.view').forEach(section => section.hidden = section.id !== 'view-' + view);
  $$('.nav-item').forEach(button => {button.classList.toggle('active', button.dataset.view === view); button.setAttribute('aria-current', button.dataset.view === view ? 'page' : 'false');});
  $('#page-title').textContent = titles[view][0];
  $('#page-subtitle').textContent = titles[view][1];
  $('#breadcrumb-view').textContent = viewLabels[view];
  $('#filters').hidden = !['overview','photos'].includes(view);
  $('#demo-banner').hidden = !demo || view === 'csv';
  $('#export-button').hidden = view === 'csv';
  if (view === 'photos') loadPhotos();
  if (view === 'csv') refreshCsvHistory();
}

function filterOptions() {
  for (const kind of ['camera', 'lens']) {
    const select = $('#' + kind + '-filter');
    const value = applied.get(kind) || '';
    select.innerHTML = `<option value="">모든 ${kind === 'camera' ? '카메라' : '렌즈'}</option>` +
      data.equipment.filter(e => e.kind === kind).map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join('') + '<option value="unknown">미상</option>';
    select.value = value;
  }
}

function render() {
  const s = data.summary;
  $('#demo-banner').hidden = !demo || currentView === 'csv';
  $('#demo-button').textContent = demo ? '내 라이브러리로 돌아가기 →' : '예시 데이터 둘러보기 ↗';
  $('#export-button').classList.toggle('disabled', demo || !s.total);
  $('#export-button').setAttribute('aria-disabled', demo || !s.total ? 'true' : 'false');
  $('#export-button').href = '/api/export?' + applied;
  $('#scope-label').textContent = (applied.get('start') || applied.get('end')) ? `${applied.get('start') || '처음'} ~ ${applied.get('end') || '최근'} · 선택 조건` : (applied.size ? '전체 기간 · 선택한 장비' : '전체 기간');
  const cards = [
    ['등록된 사진', fmt(s.total), '장', `전체 라이브러리 ${fmt(s.library_total)}장`, '▧'],
    ['함께한 장비', fmt(s.cameras+s.lenses), '개', `카메라 ${s.cameras}대 · 렌즈 ${s.lenses}개`, '◎'],
    ['등록 신품 가격 합계', s.investment == null ? '—' : money(s.investment), '', `전체 라이브러리 · ${s.priced_equipment}개 가격 입력`, '₩'],
    ['평균 배분 비용 / 장', s.average_cost == null ? '—' : money(s.average_cost), '', `가격 완비 ${fmt(s.covered)}장 · 전체의 ${percent(s.coverage)}`, '↘']
  ];
  $('#summary').innerHTML = cards.map((c,i) => `<article class="summary-card ${i===3?'featured':''}" ${i===3?'title="전체 라이브러리 장비별 단가를 사용해, 가격이 모두 있는 선택 사진의 비용을 평균합니다."':''}><div class="summary-label">${c[0]}<span aria-hidden="true">${c[4]}</span></div><div class="summary-value">${c[1]}<small>${c[2]}</small></div><div class="summary-hint">${c[3]}</div></article>`).join('');
  $('#empty-state').hidden = !!s.total;
  $('#analytics-content').hidden = !s.total;
  if (!s.total && s.library_total) {
    $('#empty-state h2').textContent = '선택한 조건에 맞는 사진이 없습니다';
    $('#empty-state p').textContent = '촬영일 또는 장비 필터를 변경하거나 초기화해 주세요.';
  } else {
    $('#empty-state h2').textContent = '첫 번째 사진 기록을 시작해 보세요';
    $('#empty-state p').textContent = '사진 폴더를 가져오면 카메라와 렌즈를 자동으로 정리합니다. 장비 가격을 입력하면 사진 한 장의 비용도 확인할 수 있어요.';
  }
  renderMonthly(data.monthly);
  renderBars('#camera-chart', data.rankings.camera);
  renderBars('#lens-chart', data.rankings.lens, true);
  renderBars('#focal-chart', data.focal.map(f => ({...f, share:f.count / (s.total || 1)})));
  const leader = data.rankings.camera.find(c => c.id != null);
  $('#insight-title').textContent = leader ? leader.name : '장비 정보가 기다리고 있어요';
  $('#insight-text').textContent = leader ? '가장 많은 순간을 함께 담은 카메라. 사진 속에서 나의 취향이 드러납니다.' : '카메라 모델이 기록된 사진을 가져오면 가장 자주 쓰는 장비를 찾을 수 있습니다.';
  $('#insight-stat').innerHTML = leader ? `${percent(leader.share)}<small>${fmt(leader.count)}장의 기록</small>` : '—';
  $('#combination-chart').innerHTML = data.combinations.slice(0,8).map(c => `<div class="combo-row"><div class="combo-title" title="${esc(c.name)}">${esc(c.camera)}<small>＋ ${esc(c.lens)}</small></div><div class="bar-track"><div class="bar-fill" style="width:${100*c.count/(data.combinations[0]?.count || 1)}%"></div></div><div class="combo-count">${fmt(c.count)}장</div><div class="combo-cost">${c.cost == null ? '가격 미입력' : money(c.cost)}<small>조합 기준 / 장</small></div></div>`).join('');
  $('#quality').innerHTML = [['카메라 정보 미상',s.missing_camera],['렌즈 정보 미상',s.missing_lens],['촬영일 미상',s.missing_date],['카메라·렌즈 가격 완비',s.covered]].map(([name,n],i) => `<div class="quality-row"><span>${name}</span><b class="${i<3&&n?'warning':''}">${fmt(n)}장 <span class="tag">${percent(n/(s.total||1))}</span></b></div>`).join('');
  renderEquipment();
  filterOptions();
}

function renderEquipment() {
  if (!data) return;
  const {key, direction} = equipmentSort;
  const equipment = [...data.equipment].sort((a, b) => {
    const left = a[key], right = b[key];
    // Missing prices/costs stay last in either direction; zero is a real value.
    if (left == null || right == null) {
      if (left == null && right != null) return 1;
      if (right == null && left != null) return -1;
    }
    const difference = key === 'name'
      ? String(left).localeCompare(String(right), 'ko', {numeric: true, sensitivity: 'base'})
      : (left ?? 0) - (right ?? 0);
    return difference * direction || a.name.localeCompare(b.name, 'ko', {numeric: true}) || a.id - b.id;
  });
  $$('[data-equipment-sort]').forEach(button => {
    const active = button.dataset.equipmentSort === key;
    button.closest('th').setAttribute('aria-sort', active ? (direction === 1 ? 'ascending' : 'descending') : 'none');
    button.querySelector('.sort-indicator').textContent = active ? (direction === 1 ? '▲' : '▼') : '↕';
  });
  $('#equipment-body').innerHTML = data.equipment.length ? equipment.map(e => `<tr><td><span class="tag">${e.kind==='camera'?'카메라':'렌즈'}</span><br><strong>${esc(e.name)}</strong>${e.note?`<small>${esc(e.note)}</small>`:''}</td><td>${fmt(e.count)}장</td><td>${money(e.new_price)}</td><td>${e.cost==null?'—':money(e.cost)}</td><td><button class="button secondary small" data-edit="${e.id}" ${demo?'disabled':''}>편집</button></td></tr>`).join('') : '<tr><td colspan="5" class="table-empty">사진 폴더를 가져오면 장비가 자동으로 등록됩니다.</td></tr>';
}

function renderBars(target, items, lens=false) {
  const element = $(target);
  element.classList.toggle('lens-bars', lens);
  const maximum = Math.max(...items.map(i=>i.count), 1);
  element.innerHTML = items.map((item,i) => `<div class="rank-row"><div class="rank-label"><span class="rank-name" title="${esc(item.name)}"><em>${String(i+1).padStart(2,'0')}</em>${esc(item.name)}</span><span class="rank-numbers">${fmt(item.count)}장<small>${percent(item.share)}</small></span></div><div class="bar-track" role="img" aria-label="${esc(item.name)} ${fmt(item.count)}장 ${percent(item.share)}"><div class="bar-fill" style="width:${100*item.count/maximum}%"></div></div></div>`).join('') || '<div class="chart-empty">기록된 정보가 없습니다.</div>';
}

function renderMonthly(months) {
  const target = $('#monthly-chart');
  const visible = months.slice(-12);
  $('#trend-total').textContent = months.length > 12 ? '최근 촬영 월 12개' : `${months.length}개월의 기록`;
  if (!visible.length) {target.innerHTML = '<div class="chart-empty">촬영일이 기록된 사진이 없습니다.</div>';return;}
  const max = Math.max(...visible.map(m => m.count),1), ceiling = Math.ceil(max/4)*4;
  const width = 650, height = 210, left = 44, right = 20, top = 15, bottom = 29;
  const plotHeight = height-top-bottom, plotWidth = width-left-right;
  const points = visible.map((m,i) => [left + (visible.length===1 ? plotWidth/2 : i*plotWidth/(visible.length-1)), top+plotHeight*(1-m.count/ceiling)]);
  const grid = Array.from({length:5},(_,i) => {const y=top+i*plotHeight/4;return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="#eef1e8" stroke-dasharray="3 5"/><text x="${left-12}" y="${y+3}" text-anchor="end" fill="#a3ad98" font-size="9">${fmt(ceiling*(1-i/4))}</text>`;}).join('');
  const polyline = points.map(p=>p.join(',')).join(' ');
  const dots = points.map(([x,y],i) => `<g><circle cx="${x}" cy="${y}" r="4" fill="#fff" stroke="#577d52" stroke-width="2"><title>${esc(visible[i].month)}: ${fmt(visible[i].count)}장</title></circle><text x="${x}" y="${height-7}" text-anchor="middle" fill="#9ba68e" font-size="9">${esc(visible[i].month.slice(2).replace('-','.'))}</text></g>`).join('');
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="월별 촬영량: ${esc(visible.map(m=>`${m.month} ${m.count}장`).join(', '))}"><defs><linearGradient id="trend-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#dce7ca" stop-opacity=".7"/><stop offset="100%" stop-color="#f9fbf5" stop-opacity=".3"/></linearGradient></defs>${grid}<polygon points="${points[0][0]},${top+plotHeight} ${polyline} ${points.at(-1)[0]},${top+plotHeight}" fill="url(#trend-gradient)"/><polyline points="${polyline}" fill="none" stroke="#65885a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${dots}</svg>`;
}

async function loadPhotos() {
  const version = ++photoVersion;
  if (demo) {$('#photos-body').innerHTML='<tr><td colspan="5" class="table-empty">예시는 통계 미리보기입니다. 실제 사진은 내 라이브러리에서 확인하세요.</td></tr>';$('#photo-count').textContent='예시';$('#page-label').textContent='';$('#prev-page').disabled=true;$('#next-page').disabled=true;return;}
  try {
    const query = new URLSearchParams(applied); query.set('page',page);
    const result = await api('/api/photos?' + query);
    if (version !== photoVersion || demo) return;
    page = result.page; pages = result.pages;
    $('#photo-count').textContent = `${fmt(result.total)}장`;
    $('#photos-body').innerHTML = result.photos.map(p => `<tr><td title="${esc(p.path)}"><strong>${esc((p.path||'').split(/[\\/]/).pop())}</strong><small>${esc(p.taken_at?.replace('T',' ') || '촬영일 미상')}</small></td><td>${esc(p.camera||'미상')}</td><td>${esc(p.lens||'미상')}</td><td>${p.focal ? fmt(p.focal)+'mm' : '—'} · ${p.aperture ? 'f/'+fmt(p.aperture):'—'}<small>${p.iso?'ISO '+fmt(p.iso):'ISO 미상'}</small></td><td>${esc(p.format)}</td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">조건에 맞는 사진이 없습니다.</td></tr>';
    $('#page-label').textContent = `${page} / ${pages}`;
    $('#prev-page').disabled=page<=1; $('#next-page').disabled=page>=pages;
  } catch(error) {toast(error.message,true);}
}

function openEquipment(id) {
  const item = data.equipment.find(e=>e.id === Number(id));
  if (!item || demo) return;
  $('#equipment-id').value=item.id; $('#equipment-name').value=item.name;
  $('#original-model').textContent='EXIF 원본 모델: '+item.model;
  $('#new-price').value=item.new_price ?? ''; $('#purchase-price').value=item.purchase_price ?? '';
  $('#equipment-note').value=item.note;
  $('#equipment-dialog').showModal();
}

async function pollStatus() {
  clearTimeout(pollTimer);
  try {
    const result=await api('/api/status'); const s=result.scan;
    const running=['scanning','cancelling'].includes(s.status);
    $('#exiftool-status').textContent=result.exiftool ? '● ExifTool 사용 가능' : '○ 미설치 · JPEG 등 기본 형식 사용 가능';
    $('#cancel-scan').hidden=!running;
    $('#cancel-scan').disabled=s.status==='cancelling';
    $('#scan-progress').hidden=!running;
    const descriptions={idle:'폴더를 선택해 새 사진이나 변경된 사진을 확인하세요.',scanning:'폴더를 확인하고 있습니다. 이미 읽은 사진은 변경이 없으면 건너뜁니다.',cancelling:'현재 파일 처리가 끝나면 중단합니다.',completed:'가져오기가 완료되었습니다. 등록된 사진을 통계에서 확인하세요.',cancelled:'가져오기를 취소했습니다. 완료된 사진은 보존됩니다.',failed:'가져오기를 완료하지 못했습니다. 오류를 확인해 주세요.'};
    $('#scan-description').textContent=descriptions[s.status] || s.status;
    $('#scan-counts').innerHTML=[['처리',s.processed],['새 사진',s.added],['중복',s.duplicates],['변경 없음',s.skipped],['오류',s.failed]].map(([name,count])=>`<div class="scan-stat">${name}<b>${fmt(count)}</b></div>`).join('');
    $('#scan-current').textContent=s.current || s.folder;
    $('#scan-errors').hidden=!s.errors.length;
    $('#error-list').innerHTML=s.errors.map(e=>`<li><b>${esc(e.path)}</b><br>${esc(e.message)}</li>`).join('');
    if (['scanning','cancelling'].includes(previousScanStatus) && !running) {await refresh();toast(s.status==='completed' ? `가져오기 완료 · 새 사진 ${fmt(s.added)}장, 오류 ${fmt(s.failed)}개` : descriptions[s.status], s.status==='failed');}
    previousScanStatus=s.status;
    updateImportControls();
    pollTimer=setTimeout(pollStatus, running?900:5000);
  } catch(error) {
    $('#scan-description').textContent='서버 연결을 확인하고 있습니다. 앱 실행 창이 열려 있는지 확인해 주세요.';
    pollTimer=setTimeout(pollStatus,5000);
  }
}

async function toggleDemo() {
  demo=!demo; applied=new URLSearchParams(); $('#filters').reset();page=1;
  updateImportControls();
  showView('overview'); await refresh();
}

document.addEventListener('click',event=>{
  if(event.target.closest('[data-import]')) requestPhotoImport();
  const nav=event.target.closest('[data-view]'); if(nav) showView(nav.dataset.view);
  const sort = event.target.closest('[data-equipment-sort]');
  if (sort) {
    const key = sort.dataset.equipmentSort;
    equipmentSort = {key, direction: equipmentSort.key === key ? -equipmentSort.direction : 1};
    renderEquipment();
  }
  const edit=event.target.closest('[data-edit]'); if(edit) openEquipment(edit.dataset.edit);
});
$('#filters').addEventListener('submit',async event=>{
  event.preventDefault();
  if ($('#start').value && $('#end').value && $('#start').value>$('#end').value) {toast('시작일은 종료일보다 늦을 수 없습니다.',true);return;}
  applied=new URLSearchParams([...new FormData(event.target)].filter(([,v])=>v));page=1;await refresh();
});
$('#reset-filters').addEventListener('click',()=>{$('#filters').reset();applied=new URLSearchParams();page=1;refresh();});
$('#demo-button').addEventListener('click',toggleDemo);$('#exit-demo').addEventListener('click',toggleDemo);$('#empty-demo').addEventListener('click',toggleDemo);
$('#prev-page').addEventListener('click',()=>{if(page>1){page--;loadPhotos();}});$('#next-page').addEventListener('click',()=>{if(page<pages){page++;loadPhotos();}});
$('#close-dialog').addEventListener('click',()=>$('#equipment-dialog').close());
$('#equipment-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try {await api('/api/equipment/'+$('#equipment-id').value,{method:'PATCH',body:JSON.stringify({name:$('#equipment-name').value,new_price:$('#new-price').value===''?null:Number($('#new-price').value),purchase_price:$('#purchase-price').value===''?null:Number($('#purchase-price').value),note:$('#equipment-note').value})});$('#equipment-dialog').close();await refresh();toast('장비 정보와 가격을 저장했습니다.');}catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
$('#import-form').addEventListener('submit',async event=>{
  event.preventDefault(); await startScan();
});
$('#cancel-scan').addEventListener('click',async()=>{try{await api('/api/scan/cancel',{method:'POST',body:'{}'});await pollStatus();}catch(error){toast(error.message,true);}});
$('#choose-folder')?.addEventListener('click',requestPhotoImport);
$('#saved-folders').addEventListener('change', event => {
  if (event.target.value) {
    $('#folder').value = event.target.value;
    folderInputEdited = true;
  }
  event.target.title = event.target.value;
});
$('#folder').addEventListener('input', () => {
  folderInputEdited = true;
  syncSavedFolderSelection();
});
$('#csv-import-form').addEventListener('submit', async event => {
  event.preventDefault();
  await startCsvImport();
});
$('#choose-csv')?.addEventListener('click', requestCsvImport);
$('#csv-path').addEventListener('input', () => { csvPathEdited = true; });
$('#csv-history').addEventListener('change', event => {
  event.target.title = event.target.selectedOptions[0]?.textContent || '';
  loadCsvImport(event.target.value);
});
$('#csv-exit-demo').addEventListener('click', async () => {
  if (demo) await toggleDemo();
  showView('csv');
});

function demoReport() {
  // Separate synthetic preview. These records are never written into the user's database.
  const equipment=[
    {id:1,kind:'camera',name:'Sony α7 IV',new_price:2890000,purchase_price:2490000},
    {id:2,kind:'camera',name:'Fujifilm X-T5',new_price:2399000,purchase_price:2190000},
    {id:3,kind:'camera',name:'Canon EOS R6 Mark II',new_price:3199000,purchase_price:2890000},
    {id:4,kind:'lens',name:'FE 24-70mm F2.8 GM II',new_price:2790000,purchase_price:2490000},
    {id:5,kind:'lens',name:'FE 35mm F1.4 GM',new_price:1790000,purchase_price:1590000},
    {id:6,kind:'lens',name:'XF 23mm F1.4 R LM WR',new_price:1199000,purchase_price:990000},
    {id:7,kind:'lens',name:'RF 24-105mm F4 L IS USM',new_price:1599000,purchase_price:null}
  ].map(e=>({...e,model:e.name,note:'가상의 예시 가격 · 실제 판매 가격이 아닙니다.'}));
  const monthEnds=[89,210,367,501,707,895,1119,1284];
  const records=Array.from({length:1284},(_,i)=>{
    const camera=i%10<6?1:i%10<9?2:3;
    return {camera_id:camera,lens_id:camera===1?(i%3?4:5):camera===2?6:7,taken_at:`2026-${String(1+monthEnds.findIndex(end=>i<end)).padStart(2,'0')}-${String(1+i%28).padStart(2,'0')}`,focal:[24,35,50,85][i%4]};
  });
  for(const e of equipment){e.count=records.filter(p=>p[e.kind+'_id']===e.id).length;e.cost=e.new_price/e.count;e.purchase_cost=e.purchase_price==null?null:e.purchase_price/e.count;}
  const selected=records.filter(p=>(!applied.get('start')||p.taken_at>=applied.get('start'))&&(!applied.get('end')||p.taken_at<=applied.get('end'))&&['camera','lens'].every(k=>!applied.get(k)||String(p[k+'_id'])===applied.get(k)));
  const lookup=id=>equipment.find(e=>e.id===id);
  const group=key=>{const m=new Map();for(const p of selected){const k=key(p);m.set(k,(m.get(k)||0)+1);}return [...m].map(([key,count])=>({key,count}));};
  const rankings={};for(const kind of ['camera','lens'])rankings[kind]=group(p=>p[kind+'_id']).map(g=>({...lookup(g.key),count:g.count,cost:lookup(g.key).new_price/g.count,share:g.count/(selected.length||1)})).sort((a,b)=>b.count-a.count);
  const combinations=group(p=>p.camera_id+':'+p.lens_id).map(g=>{const [c,l]=g.key.split(':').map(id=>lookup(Number(id)));return {camera_id:c.id,lens_id:l.id,camera:c.name,lens:l.name,name:c.name+' + '+l.name,count:g.count,price:c.new_price+l.new_price,cost:(c.new_price+l.new_price)/g.count,share:g.count/(selected.length||1)};}).sort((a,b)=>b.count-a.count);
  return {equipment,rankings,combinations,summary:{total:selected.length,library_total:records.length,cameras:rankings.camera.length,lenses:rankings.lens.length,investment:equipment.reduce((n,e)=>n+e.new_price,0),priced_equipment:equipment.length,covered:selected.length,coverage:selected.length?1:0,missing_camera:0,missing_lens:0,missing_date:0,average_cost:selected.length?selected.reduce((n,p)=>n+lookup(p.camera_id).cost+lookup(p.lens_id).cost,0)/selected.length:null},monthly:group(p=>p.taken_at.slice(0,7)).map(g=>({month:g.key,count:g.count})).sort((a,b)=>a.month.localeCompare(b.month)),focal:group(p=>p.focal).sort((a,b)=>a.key-b.key).map(g=>({name:g.key+'mm',count:g.count}))};
}

refresh();pollStatus();refreshSavedFolders(true);refreshCsvHistory(true);

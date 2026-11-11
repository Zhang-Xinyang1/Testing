const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function environment() {
  const pending = [], records = [], messages = [];
  const window = { addEventListener() {}, TestingScheduler: {run(input, options) {
    return new Promise((resolve, reject) => pending.push({input, options, resolve, reject}));
  }}};
  const context = vm.createContext({window, document: {addEventListener() {}},
    console: {error() {}}, performance: {now: () => 1}});
  const read = file => fs.readFileSync(path.join(__dirname, '../frontend/assets/js', file), 'utf8');
  const run = window.TestingScheduler.run;
  vm.runInContext(read('../schedule-client.js'), context);
  window.TestingScheduler.run = run;
  vm.runInContext(read('core.js'), context);
  const app = window.TSApp;
  app.state.mode = 'server';
  app.state.data = {meta: {loadedAt: 'before'}, lots: [['L1']], marker: 'first'};
  app.state.options = {algorithm: 'search'};
  app.showLoading = () => { app.loading = true; };
  app.hideLoading = () => { app.loading = false; };
  app.updateRunChip = () => {};
  app.renderView = () => {};
  app.toast = message => messages.push(message);
  app.recordDiagnostics = async (result, error, snapshot) => records.push({result, error, snapshot});
  const result = id => ({id, start: 1, metrics: {completedWafers: 1, totalWafers: 1, completedLots: 1}});
  return {app, context, read, pending, records, result, messages};
}

test('a slower previous request cannot overwrite the current result or hide its loading indicator', async () => {
  const {app, pending, result, records} = environment();
  const first = app.runSchedule();
  app.state.data.marker = 'second';
  const second = app.runSchedule();
  pending[0].resolve(result('old'));
  await first;
  assert.equal(app.state.result, null);
  assert.equal(app.loading, true);
  assert.equal(records.length, 0);
  pending[1].resolve(result('new'));
  await second;
  assert.equal(app.state.result.id, 'new');
  assert.equal(records[0].snapshot.input.marker, 'second');
  assert.equal(pending[0].input.marker, 'first');
});

test('a failed previous request cannot replace a successful new result', async () => {
  const {app, pending, result, records, messages} = environment();
  const first = app.runSchedule();
  const second = app.runSchedule();
  pending[1].resolve(result('new'));
  await second;
  pending[0].reject(new Error('old error'));
  await first;
  assert.equal(app.state.result.id, 'new');
  assert.equal(records.length, 1);
  assert.equal(messages.some(message => message.includes('old error')), false);
});

test('a saved, unchanged preview reuses its result; changed input or options triggers a real run', async () => {
  const {app, pending, result} = environment();
  const cached = {input: JSON.parse(JSON.stringify(app.state.data)), options: {...app.state.options}, result: result('preview')};
  app.state.data.meta.loadedAt = 'after';
  await app.runSchedule(cached);
  assert.equal(pending.length, 0);
  assert.equal(app.state.result.id, 'preview');
  app.state.data.marker = 'changed';
  const changed = app.runSchedule(cached);
  assert.equal(pending.length, 1);
  pending[0].resolve(result('recomputed'));
  await changed;
  assert.equal(app.state.result.id, 'recomputed');
  assert.equal(app.canReuseSchedule(cached, cached.input, {algorithm: 'greedy'}), false);
});

test('configuration remains accessible after a scheduling failure', () => {
  const env = environment();
  // Re-load to restore the actual renderer, then supply only its input view.
  vm.runInContext(env.read('core.js'), env.context);
  const app = env.context.window.TSApp;
  app.state.mode = 'server'; app.state.data = {}; app.state.view = 'inputs';
  app.state.result = null;
  let rendered = false;
  app.renderInputs = () => { rendered = true; };
  app.renderView();
  assert.equal(rendered, true);
});

test('diagnostics store the run snapshot even if the current UI input has changed', async () => {
  const env = environment();
  let posted;
  env.context.fetch = async (url, request) => {
    posted = JSON.parse(request.body);
    return {ok: true, json: async () => ({report: {id: 'run'}})};
  };
  vm.runInContext(env.read('diagnostics.js'), env.context);
  await env.app.recordDiagnostics(null, new Error('run failed'), {
    input: {meta: {loadedAt: 'remove'}, lots: [], marker: 'snapshot'}, options: {algorithm: 'greedy'}
  });
  assert.equal(posted.input.marker, 'snapshot');
  assert.equal(posted.options.algorithm, 'greedy');
  assert.equal(posted.input.meta.loadedAt, undefined);
});

test('an uploaded workbook survives view rerenders and failed saves until committed', async () => {
  const env = environment(), app = env.app, listeners = {};
  const nodes = {
    '#cfg-path': {value: 'old.xlsx'}, '#cfg-file-status': {textContent: 'old.xlsx'},
    '#cfg-result': {}, '#cfg-source': {value: '{"sheetMap":{},"headerRow":1}'},
    '#cfg-fields': {value:'{}'}, '#cfg-month': {value:'2026-09'},
    '#cfg-bake-machines': {value:'7'}, '#cfg-bake-chambers': {value:'4'},
    '#cfg-kind': {value:'borrow'}, '#cfg-machine': {value:''}
  };
  app.$ = selector => nodes[selector]; app.$$ = () => [];
  app.state.config = {dataSource:{path:'old.xlsx', sheetMap:{}, headerRow:1}, fieldMap:{}};
  app.state.data = {eqp:[], borrow:[], pm:[], wip:[], meta:{month:'2026-09'}};
  env.context.document = {addEventListener(type, callback) { listeners[type] = callback; }};
  let failSave = true, posted;
  env.context.fetch = async (url, request) => {
    if (url.endsWith('/api/uploads')) return {ok:true, json:async()=>({path:'uploads/new.xlsx', name:'new.xlsx'})};
    posted = JSON.parse(request.body);
    if (failSave) return {ok:false, json:async()=>({error:'validation failed'})};
    return {ok:true, json:async()=>({data:app.state.data, config:{...app.state.config, dataSource:posted.dataSource}})};
  };
  app.SCHED.run = async () => env.result('saved');
  app.runSchedule = async () => {};
  app.updateSourceCard = () => {};
  vm.runInContext(env.read('configuration.js'), env.context);
  await listeners.change({target:{id:'cfg-file', files:[{name:'new.xlsx', size:10}]}});
  const html = app.configurationHTML(); // navigating away and back rebuilds this markup
  assert.match(html, /value="uploads\/new.xlsx"/);
  assert.match(html, /新文件已上传，等待保存/);
  assert.equal(app.state.config.dataSource.path, 'old.xlsx');
  nodes['#cfg-path'].value = html.match(/id="cfg-path"[^>]*value="([^"]+)"/)[1];
  const event = {target:{id:'cfg-save', closest:()=>null}};
  await listeners.click(event);
  assert.equal(posted.dataSource.path, 'uploads/new.xlsx');
  assert.match(app.configurationHTML(), /value="uploads\/new.xlsx"/);
  failSave = false;
  await listeners.click(event);
  assert.equal(app.state.config.dataSource.path, 'uploads/new.xlsx');
  assert.doesNotMatch(app.configurationHTML(), /新文件已上传，等待保存/);
});

test('equipment pairing and the original two PM rows survive configuration saves', async () => {
  const env = environment(), app = env.app, listeners = {};
  const nodes = {
    '#cfg-path': {value:'paired.xlsx'}, '#cfg-file-status': {textContent:'paired.xlsx'},
    '#cfg-result': {}, '#cfg-source': {value:'{"sheetMap":{},"headerRow":1}'},
    '#cfg-fields': {value:'{}'}, '#cfg-month': {value:'2026-11'},
    '#cfg-bake-machines': {value:'1'}, '#cfg-bake-chambers': {value:'4'},
    '#cfg-kind': {value:'pm'}, '#cfg-machine': {value:'P1'}
  };
  nodes['#cfg-override-eqp'] = {checked:true};
  nodes['#cfg-override-pm'] = {checked:true};
  const equipment = {id:'M1', type:'A', proberId:'P1', card:'C1', recipe:'R', status:'RUN'};
  const plans = ['M1', 'P1'].map((eqp, index) => ({eqp, targetKind:index ? 'PROBER' : 'EQP',
    early:'2026-11-10', due:'2026-11-10', over:'2026-11-10', hours:4,
    start:index ? '2026-11-10T10:00' : '2026-11-10T08:00'}));
  const toRow = object => ({fields:Object.entries(object).map(([field, value]) => ({
    dataset:{field}, value:String(value), type:field === 'hours' ? 'number' : field === 'start' ? 'datetime-local' : 'text'
  }))});
  const rows = {eqp:[toRow(equipment)], pm:plans.map(toRow), borrow:[], wip:[]};
  app.$ = selector => nodes[selector];
  app.$$ = (selector, root) => root ? root.fields : (rows[(selector.match(/^#cfg-table-(\w+)/) || [])[1]] || []);
  app.state.config = {dataSource:{path:'paired.xlsx', sheetMap:{}, headerRow:1}, fieldMap:{}};
  app.state.data = {eqp:[equipment], pm:plans, borrow:[], wip:[], meta:{month:'2026-11'}};
  env.context.document = {addEventListener(type, callback) {listeners[type] = callback;}};
  let posted;
  env.context.fetch = async (url, request) => {
    posted = JSON.parse(request.body);
    return {ok:false, json:async()=>({error:'stop after preview capture'})};
  };
  vm.runInContext(env.read('configuration.js'), env.context);
  const html = app.configurationHTML();
  assert.match(html, /data-field="proberId"[^>]*value="P1"/);
  assert.match(html, /value="PROBER" selected/);
  assert.match(html, /指定时间重叠时合并维护/);
  await listeners.click({target:{id:'cfg-save', closest:()=>null}});
  assert.equal(posted.scheduling.eqp[0].proberId, 'P1');
  assert.equal(posted.scheduling.pm.length, 2);
  assert.deepEqual(posted.scheduling.pm.map(p => p.eqp), ['M1', 'P1']);
  assert.equal(posted.scheduling.pm[1].targetKind, 'PROBER');
  assert.equal(posted.scheduling.pm[1].start, '2026-11-10T10:00:00+08:00');
});

test('the real browser date parser accepts backend formats and rejects invalid calendar values', () => {
  const parse = environment().app.SCHED.parseDate;
  const expected = Date.parse('2026-11-10T08:00:12.123+08:00');
  for (const value of ['2026-11-10 08:00:12.123456', '2026/11/10 08:00:12.123',
    '2026年11月10日 08:00:12.123', '2026-11-10t08:00:12.123+0800', '2026-11-10T00:00:12.123z']) {
    assert.equal(parse(value), expected, value);
  }
  assert.equal(parse('2026-1-2'), Date.parse('2026-01-02T00:00:00+08:00'));
  assert.equal(parse('2024-2-29'), Date.parse('2024-02-29T00:00:00+08:00'));
  for (const value of ['', '2026-2-29', '2026-4-31', '0000-1-1', '2026-13-1',
    '2026-11-10 24:00', '2026-11-10T08:00+2400', '2026-11-10Z', 'not a date']) {
    assert.equal(Number.isNaN(parse(value)), true, value);
  }
});

function inputData() {
  return {meta:{month:'2026-11'}, mps:[], lots:[], eqp:[], pm:[], borrow:[], wip:[],
    recipes:[], flows:{}, weekly:[], sheetSummary:[], bakeResources:{machines:1, chambers:2}};
}
function inputConfig(scheduling = {}) {
  return {dataSource:{path:'input.xlsx', month:'2026-11', sheetMap:{}, headerRow:1}, fieldMap:{}, scheduling};
}

test('opening the page loads configuration and data without scheduling, including bad workbook recovery', async () => {
  for (const invalid of [false, true]) {
    const env = environment(), app = env.app, calls = [], config = inputConfig();
    env.context.location = {protocol:'http:', hash:''};
    env.context.fetch = async url => {
      calls.push(url);
      if (url.endsWith('api/config')) return {ok:true, json:async()=>config};
      return {ok:!invalid, status:500, json:async()=>invalid ? {error:'产品缺少路线：WB49'} : inputData()};
    };
    vm.runInContext(env.read('../data-loader.js'), env.context);
    app.$$ = () => []; app.bindShell = () => {}; app.syncNav = () => {};
    app.updateSourceCard = () => {};
    app.navigate = view => { app.state.view = view; };
    await app.boot();
    assert.deepEqual(calls, ['/api/config', '/api/data']);
    assert.equal(env.pending.length, 0);
    assert.equal(app.state.result, null);
    assert.equal(app.state.mode, 'server');
    assert.equal(app.state.config, config);
    if (invalid) {
      assert.equal(app.state.view, 'inputs');
      assert.equal(app.state.data, null);
      assert.match(app.state.loadError, /WB49/);
    }
  }
});

test('real input and configuration views render before scheduling and after data failure', () => {
  const env = environment(), app = env.app, output = {};
  app.$ = () => output; app.state.config = inputConfig(); app.state.data = inputData();
  vm.runInContext(env.read('configuration.js'), env.context);
  vm.runInContext(env.read('inputs.js'), env.context);
  app.renderInputs();
  assert.match(output.innerHTML, /尚无排产结果/);
  assert.match(output.innerHTML, /id="cfg-file"/);
  app.state.data = null; app.state.loadError = '无效日期/时间：PM 计划';
  app.renderInputs();
  assert.match(output.innerHTML, /数据读取失败/);
  assert.match(output.innerHTML, /id="cfg-save"/);
  assert.match(output.innerHTML, /id="cfg-fields"/);
  assert.match(output.innerHTML, /无效日期\/时间/);
});

test('explicit rerun reads the latest saved data and stops safely if it cannot be read', async () => {
  const env = environment(), app = env.app, latest = inputData();
  latest.marker = 'latest saved input';
  app.updateSourceCard = () => {};
  app.navigate = view => { app.state.view = view; };
  env.context.window.DataLoader = {load:async()=>({data:latest, config:inputConfig(), mode:'server', error:null})};
  const running = app.reloadAndRun();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(env.pending.length, 1);
  assert.equal(env.pending[0].input.marker, 'latest saved input');
  env.pending[0].resolve(env.result('latest'));
  await running;
  env.context.window.DataLoader.load = async()=>({data:null, config:inputConfig(), mode:'server', error:'Excel 无效'});
  await app.reloadAndRun();
  assert.equal(env.pending.length, 1);
  assert.equal(app.state.result, null);
  assert.equal(app.state.view, 'inputs');
  assert.equal(app.loading, false);
});

// Mount the actual editor markup into small DOM fixtures. Original row metadata and
// rendered input values come from production HTML, never from a replacement parser.
function editor(data, config) {
  const env = environment(), app = env.app, listeners = {}, nodes = {}, rows = {}, posted = [];
  const decode = text => text.replace(/&(quot|amp|lt|gt|#39);/g,
    (_, key) => ({quot:'"', amp:'&', lt:'<', gt:'>', '#39':"'"})[key]);
  const attr = (tag, name) => decode((tag.match(new RegExp(' ' + name + '="([^"]*)"')) || [,''])[1]);
  app.state.data = data; app.state.config = config;
  app.$ = selector => nodes[selector];
  app.$$ = (selector, root) => root ? root.fields : (rows[(selector.match(/^#cfg-table-(\w+)/) || [])[1]] || []);
  env.context.document = {addEventListener(type, callback) { listeners[type] = callback; }};
  env.context.fetch = async (url, options) => {
    posted.push(JSON.parse(options.body));
    return {ok:false, json:async()=>({error:'captured preview'})};
  };
  vm.runInContext(env.read('configuration.js'), env.context);
  const html = app.configurationHTML();
  for (const tag of html.matchAll(/<input\b[^>]*>/g)) {
    const id = attr(tag[0], 'id');
    if (id) nodes['#' + id] = {value:attr(tag[0], 'value'), checked:/ checked/.test(tag[0])};
  }
  for (const tag of html.matchAll(/<textarea\b([^>]*)>([\s\S]*?)<\/textarea>/g)) {
    nodes['#' + attr(tag[1], 'id')] = {value:decode(tag[2])};
  }
  nodes['#cfg-file-status'] = {textContent:'input.xlsx'};
  nodes['#cfg-kind'] = {value:'pm'}; nodes['#cfg-machine'] = {value:'M1'};
  nodes['#cfg-target-kind'] = {value:''};
  nodes['#cfg-result'] = {};
  for (const table of html.matchAll(/<table\b([^>]*)>(.*?)<\/table>/g)) {
    const key = attr(table[1], 'id').replace('cfg-table-', '');
    rows[key] = [];
    for (const row of table[2].matchAll(/<tr data-original="([^"]*)">(.*?)<\/tr>/g)) {
      const fields = [];
      for (const el of row[2].matchAll(/<input\b[^>]*>|<select\b[^>]*>.*?<\/select>/g)) {
        const tag = el[0], field = attr(tag, 'data-field');
        if (!field) continue;
        const option = (tag.match(/<option\b[^>]* selected[^>]*>/) || [''])[0];
        fields.push({dataset:{field}, type:tag.startsWith('<select') ? 'select-one' : attr(tag, 'type'),
          value:attr(option || tag, 'value')});
      }
      rows[key].push({dataset:{original:decode(row[1])}, fields});
    }
  }
  return {...env, html, nodes, rows, listeners, posted,
    save:()=>listeners.click({target:{id:'cfg-save', closest:()=>null}})};
}

test('saving other settings keeps Excel sources and unrelated scheduling options', async () => {
  const env = editor(inputData(), inputConfig({rulesVersion:'kept', platformType:{A:'A'}}));
  env.nodes['#cfg-month'].value = '2026-12';
  await env.save();
  const update = env.posted[0];
  assert.equal(update.dataSource.month, '2026-12');
  assert.equal(update.scheduling.rulesVersion, 'kept');
  for (const key of ['eqp', 'pm', 'borrow', 'wip']) assert.equal(key in update.scheduling, false);
  env.nodes['#cfg-override-pm'].checked = true;
  await env.save();
  assert.deepEqual(env.posted[1].scheduling.pm, []); // Explicit empty override is retained.
});

test('editing automatically selects manual source and deselecting returns that table to Excel', async () => {
  const data = inputData(); data.eqp = [{id:'M1', type:'A', proberId:'P1'}];
  const env = editor(data, inputConfig());
  await env.listeners.change({target:{closest:()=>({id:'cfg-table-eqp'})}});
  env.rows.eqp[0].fields.find(f => f.dataset.field === 'proberId').value = 'P2';
  await env.save();
  assert.equal(env.posted[0].scheduling.eqp[0].proberId, 'P2');
  assert.equal('pm' in env.posted[0].scheduling, false);
  env.nodes['#cfg-override-eqp'].checked = false;
  await env.save();
  assert.equal('eqp' in env.posted[1].scheduling, false);
});

test('PM and WIP dates survive real rendering and saving, including seconds and timezone', async () => {
  const data = inputData();
  data.eqp = [{id:'M1', type:'A', proberId:'P1'}];
  data.pm = ['M1','P1'].map((eqp, i) => ({eqp, targetKind:i ? 'PROBER' : 'EQP',
    early:'2026/11/10', due:'2026年11月10日', over:'2026-11-10', hours:4,
    start:i ? '2026/11/10 10:00:12.123456' : '2026-11-10 08:00:12.123456', note:'preserve'}));
  data.wip = [{lotId:'L1', eqp:'M1', start:'2026-11-10T00:00:12Z', end:'2026年11月10日 09:30:35'}];
  const env = editor(data, inputConfig({eqp:data.eqp, pm:data.pm, wip:data.wip}));
  assert.match(env.html, /value="2026-11-10T08:00:12.123"/);
  assert.match(env.html, /type="date" value="2026-11-10"/);
  await env.save();
  const saved = env.posted[0].scheduling;
  assert.deepEqual(saved.pm, data.pm);
  assert.equal(saved.wip[0].start, data.wip[0].start);
  assert.equal(saved.wip[0].end, data.wip[0].end);
  env.rows.pm[1].fields.find(f => f.dataset.field === 'start').value = '2026-11-10T10:15:32.125';
  await env.save();
  assert.equal(env.posted[1].scheduling.pm[1].start, '2026-11-10T10:15:32.125+08:00');
});

test('invalid saved dates remain visible and cannot silently turn into empty fixed PM starts', async () => {
  const data = inputData(); data.pm = [{eqp:'M1', start:'2026-02-30 08:00', hours:2}];
  const env = editor(data, inputConfig({pm:data.pm}));
  assert.match(env.html, /data-field="start"[^>]*type="text" value="2026-02-30 08:00"/);
  await env.save();
  assert.equal(env.posted[0].scheduling.pm[0].start, '2026-02-30 08:00');
});

test('restoring Excel sources preserves configured oven capacity and other settings', async () => {
  const env = editor(inputData(), inputConfig({pm:[], eqp:[], borrow:[], wip:[],
    bakeResources:{machines:1, chambers:2}, rulesVersion:'keep'}));
  await env.listeners.click({target:{id:'cfg-excel', closest:()=>null}});
  assert.deepEqual(env.posted[0].scheduling, {bakeResources:{machines:1, chambers:2}, rulesVersion:'keep'});
});

test('plan recommendation reaches preview and reports the new PM without shadowing the request helper', async () => {
  const data = inputData(); data.eqp = [{id:'M1', type:'A'}];
  const env = editor(data, inputConfig()), calls = [];
  env.context.fetch = async (url, options) => {
    calls.push(url);
    return {ok:true, json:async()=>({data:JSON.parse(JSON.stringify(data))})};
  };
  env.app.SCHED.run = async input => {
    assert.equal(input.pm.length, 1);
    return {pmRecords:[{id:'PM-01', eqp:'M1', start:1, end:2}]};
  };
  await env.listeners.click({target:{id:'cfg-recommend', closest:()=>null}});
  assert.deepEqual(calls, ['/api/preview']);
  assert.match(env.nodes['#cfg-result'].textContent, /推荐维护时间/);
});

test('a lost server during rerun cannot replace saved input with embedded demo data', async () => {
  const env = environment(), app = env.app, config = inputConfig();
  app.state.config = config; app.updateSourceCard = () => {};
  app.navigate = view => { app.state.view = view; };
  env.context.window.DataLoader = {load:async()=>({data:inputData(), config:null, mode:'embedded', error:'HTTP 404'})};
  await app.reloadAndRun();
  assert.equal(app.state.config, config);
  assert.equal(app.state.data, null);
  assert.equal(app.state.result, null);
  assert.equal(env.pending.length, 0);
  assert.equal(app.state.view, 'inputs');
});

test('export buttons before the first schedule explain the required action instead of throwing', () => {
  const env = environment(), listeners = {};
  env.context.document.addEventListener = (name, callback) => { listeners[name] = callback; };
  vm.runInContext(env.read('ui.js'), env.context);
  for (const kind of ['mps', 'lots']) {
    listeners.click({target:{closest:selector => selector === '[data-export]' ? {getAttribute:()=>kind} : null}});
  }
  assert.equal(env.messages.filter(message => message.includes('请先排产再导出')).length, 2);
});

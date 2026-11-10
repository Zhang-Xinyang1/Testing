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
  app.SCHED.parseDate = Date.parse;
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

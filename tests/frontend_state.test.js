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

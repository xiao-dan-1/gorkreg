import test from 'node:test';
import assert from 'node:assert/strict';
import * as betaController from '../../client/static/prototypes/focus-inspector-beta-controller.mjs';

import {
  BETA_CONTROLLER_VERSION,
  loadDashboardSnapshot,
  createTaskTransport,
  createActionGate,
  shouldAcceptTaskUpdate,
  readStatus,
  executeRefresh,
  executeStop,
} from '../../client/static/prototypes/focus-inspector-beta-controller.mjs';

test('loads independent dashboard regions without blanking fulfilled results', async () => {
  const calls = [];
  const api = (path) => {
    calls.push(path);
    if (path === '/api/env-check') return Promise.reject(new Error('unavailable'));
    return Promise.resolve({ path });
  };

  const snapshot = await loadDashboardSnapshot(api);

  assert.equal(BETA_CONTROLLER_VERSION, 1);
  assert.deepEqual(calls, ['/api/overview', '/api/env-check', '/api/refresh-bot']);
  assert.deepEqual(snapshot.overview, { ok: true, value: { path: '/api/overview' }, error: null });
  assert.equal(snapshot.environment.ok, false);
  assert.equal(snapshot.environment.value, null);
  assert.equal(snapshot.environment.error.message, 'unavailable');
  assert.deepEqual(snapshot.bot, { ok: true, value: { path: '/api/refresh-bot' }, error: null });
});

test('task transport consumes only named task SSE events', () => {
  const listeners = new Map();
  const tasks = [];
  const connections = [];
  const errors = [];
  let url = '';
  const source = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    close() {},
  };
  const transport = createTaskTransport({
    eventSourceFactory(nextUrl) { url = nextUrl; return source; },
    getStatus: async () => ({}),
    setIntervalFn() { throw new Error('polling not expected'); },
    clearIntervalFn() {},
    onTask(task) { tasks.push(task); },
    onConnection(status) { connections.push(status); },
    onError(error) { errors.push(error); },
  });

  transport.start();
  source.onopen();
  listeners.get('task')({ data: '{"id":1}' });
  listeners.get('task')({ data: 'not json' });

  assert.equal(url, '/api/logs/stream?after=0');
  assert.equal(listeners.has('task'), true);
  assert.equal(listeners.has('log'), false);
  assert.deepEqual(connections, ['reconnecting', 'realtime']);
  assert.deepEqual(tasks, [{ id: 1 }]);
  assert.equal(errors.length, 1);
});

test('task transport marks only the first snapshot of a connection as sequence-reset eligible', () => {
  const listeners = new Map();
  const deliveries = [];
  const source = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    close() {},
  };
  const transport = createTaskTransport({
    eventSourceFactory() { return source; },
    getStatus: async () => ({}),
    setIntervalFn() { throw new Error('polling not expected'); },
    clearIntervalFn() {},
    onTask(task, context) { deliveries.push({ task, context }); },
    onConnection() {},
    onError() {},
  });

  transport.start();
  listeners.get('task')({ data: '{"log_seq":1,"run_id":"new"}' });
  listeners.get('task')({ data: '{"log_seq":0,"run_id":"stale"}' });

  assert.deepEqual(deliveries, [
    { task: { log_seq: 1, run_id: 'new' }, context: { allowSequenceReset: true } },
    { task: { log_seq: 0, run_id: 'stale' }, context: { allowSequenceReset: false } },
  ]);
});

test('task transport falls back from SSE to 1500ms polling', async () => {
  const connections = [];
  const tasks = [];
  const intervals = [];
  let closed = 0;
  const source = {
    addEventListener() {},
    close() { closed += 1; },
  };
  const transport = createTaskTransport({
    eventSourceFactory() { return source; },
    getStatus: async () => ({ running: true }),
    setIntervalFn(callback, delay) { intervals.push({ callback, delay }); return 'timer-1'; },
    clearIntervalFn() {},
    onTask(task) { tasks.push(task); },
    onConnection(status) { connections.push(status); },
    onError() {},
  });

  transport.start();
  source.onopen();
  source.onerror();
  await Promise.resolve();

  assert.equal(closed, 1);
  assert.deepEqual(connections, ['reconnecting', 'realtime', 'reconnecting', 'polling']);
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0].delay, 1500);
  assert.deepEqual(tasks, [{ running: true }]);
});

test('task transport stop closes active SSE and clears active poll timer once', () => {
  let closed = 0;
  const cleared = [];
  const source = { addEventListener() {}, close() { closed += 1; } };
  const transport = createTaskTransport({
    eventSourceFactory() { return source; },
    getStatus: async () => ({}),
    setIntervalFn() { return 'timer-1'; },
    clearIntervalFn(timer) { cleared.push(timer); },
    onTask() {}, onConnection() {}, onError() {},
  });

  transport.startPolling();
  transport.start();
  transport.stop();
  transport.stop();

  assert.equal(closed, 1);
  assert.deepEqual(cleared, ['timer-1']);
});

test('stale SSE errors after stop or restart do not poll or close a replacement source', () => {
  const connections = [];
  const intervals = [];
  const stale = { addEventListener() {}, close() { this.closed = (this.closed || 0) + 1; } };
  const replacement = { addEventListener() {}, close() { this.closed = (this.closed || 0) + 1; } };
  const sources = [stale, replacement];
  const transport = createTaskTransport({
    eventSourceFactory() { return sources.shift(); },
    getStatus: async () => ({}),
    setIntervalFn(callback, delay) { intervals.push({ callback, delay }); return 'timer'; },
    clearIntervalFn() {},
    onTask() {}, onConnection(status) { connections.push(status); }, onError() {},
  });

  transport.start();
  transport.stop();
  assert.doesNotThrow(() => stale.onerror());
  transport.start();
  stale.onerror();

  assert.equal(replacement.closed || 0, 0);
  assert.equal(intervals.length, 0);
  assert.deepEqual(connections, ['reconnecting', 'reconnecting']);
});

test('task events queued after stop do not deliver tasks or parse errors', () => {
  const listeners = new Map();
  const tasks = [];
  const errors = [];
  const source = { addEventListener(name, handler) { listeners.set(name, handler); }, close() {} };
  const transport = createTaskTransport({
    eventSourceFactory() { return source; }, getStatus: async () => ({}),
    setIntervalFn() { return 'timer'; }, clearIntervalFn() {},
    onTask(task) { tasks.push(task); }, onConnection() {}, onError(error) { errors.push(error); },
  });

  transport.start();
  transport.stop();
  listeners.get('task')({ data: '{"stale":true}' });
  listeners.get('task')({ data: 'not json' });

  assert.deepEqual(tasks, []);
  assert.deepEqual(errors, []);
});

test('polling serializes interval ticks until the active status request resolves', async () => {
  const intervals = [];
  const tasks = [];
  let calls = 0;
  let release;
  const slowStatus = new Promise((resolve) => { release = resolve; });
  const transport = createTaskTransport({
    eventSourceFactory() { throw new Error('unused'); },
    getStatus() { calls += 1; return calls === 1 ? slowStatus : Promise.resolve({ attempt: calls }); },
    setIntervalFn(callback, delay) { intervals.push({ callback, delay }); return 'timer'; }, clearIntervalFn() {},
    onTask(task) { tasks.push(task); }, onConnection() {}, onError() {},
  });

  transport.startPolling();
  intervals[0].callback();
  assert.equal(calls, 1);
  release({ attempt: 1 });
  await Promise.resolve();
  await Promise.resolve();
  intervals[0].callback();
  await Promise.resolve();

  assert.equal(calls, 2);
  assert.deepEqual(tasks, [{ attempt: 1 }, { attempt: 2 }]);
});

test('stopping suppresses an in-flight poll and lets a restart poll immediately', async () => {
  const intervals = [];
  const tasks = [];
  const resolvers = [];
  let calls = 0;
  const transport = createTaskTransport({
    eventSourceFactory() { throw new Error('unused'); },
    getStatus() {
      calls += 1;
      return new Promise((resolve) => { resolvers.push(resolve); });
    },
    setIntervalFn(callback, delay) { intervals.push({ callback, delay }); return `timer-${intervals.length}`; },
    clearIntervalFn() {}, onTask(task) { tasks.push(task); }, onConnection() {}, onError() {},
  });

  transport.startPolling();
  transport.stop();
  transport.startPolling();
  assert.equal(calls, 2);
  resolvers[0]({ generation: 'stale' });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(tasks, []);
  resolvers[1]({ generation: 'current' });
  await Promise.resolve();
  await Promise.resolve();

  assert.deepEqual(tasks, [{ generation: 'current' }]);
});

test('action gate ignores duplicate actions while its first action is pending', async () => {
  const busy = [];
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const gate = createActionGate((value) => busy.push(value));

  const first = gate.run(async () => { calls += 1; return pending; });
  const duplicate = await gate.run(async () => { calls += 1; return 'duplicate'; });
  release({ saved: true });

  assert.deepEqual(duplicate, { ignored: true });
  assert.equal(calls, 1);
  assert.equal(gate.busy, true);
  assert.deepEqual(await first, { saved: true });
  assert.equal(gate.busy, false);
  assert.deepEqual(busy, [true, false]);
});

test('refresh uses the approved request and status contracts with a stable started result', async () => {
  const calls = [];
  const result = await executeRefresh({ request: async (path, options) => {
    calls.push([path, options]);
    return { run_id: 'refresh-1' };
  }, getStatus: async () => ({ running: true, kind: 'refresh', run_id: 'refresh-1' }), body: { limit: 0, jobs: 4, needs_only: false, remint_on_revoke: true } });
  assert.deepEqual(calls, [['/api/task/refresh', { method: 'POST', body: { limit: 0, jobs: 4, needs_only: false, remint_on_revoke: true } }]]);
  assert.deepEqual(result, { outcome: 'started', response: { run_id: 'refresh-1' }, error: null, status: { running: true, kind: 'refresh', run_id: 'refresh-1' }, statusError: null });
});

test('readStatus and refresh preserve a status read error in the stable result', async () => {
  const unavailable = new TypeError('offline');
  const read = await readStatus(async () => { throw unavailable; });
  assert.deepEqual(read, { status: null, statusError: unavailable });
  const result = await executeRefresh({ request: async () => ({ run_id: 'refresh-2' }), getStatus: async () => { throw unavailable; }, body: { limit: 1 } });
  assert.deepEqual(result, { outcome: 'started', response: { run_id: 'refresh-2' }, error: null, status: null, statusError: unavailable });
});

test('refresh maps 409 to conflict whether status is readable or not', async () => {
  const conflict = Object.assign(new Error('busy'), { status: 409 });
  const withStatus = await executeRefresh({ request: async () => { throw conflict; }, getStatus: async () => ({ running: true }), body: {} });
  assert.deepEqual(withStatus, { outcome: 'conflict', response: null, error: conflict, status: { running: true }, statusError: null });
  const withoutStatus = await executeRefresh({ request: async () => { throw conflict; }, getStatus: async () => { throw conflict; }, body: {} });
  assert.deepEqual(withoutStatus, { outcome: 'conflict', response: null, error: conflict, status: null, statusError: conflict });
});

test('refresh returns errors without status for validation and server failures', async () => {
  for (const status of [422, 500]) {
    const error = Object.assign(new Error(String(status)), { status });
    const result = await executeRefresh({ request: async () => { throw error; }, getStatus: async () => ({ running: true }), body: {} });
    assert.deepEqual(result, { outcome: 'error', response: null, error, status: null, statusError: null });
  }
});

test('stop uses the approved request and status contracts with a stable stopping result', async () => {
  const calls = [];
  const result = await executeStop({ request: async (path, options) => {
    calls.push([path, options]);
    return { message: '停止请求已发送' };
  }, getStatus: async () => ({ running: true, stopping: true }) });
  assert.deepEqual(calls, [['/api/task/stop', { method: 'POST' }]]);
  assert.deepEqual(result, { outcome: 'stopping', response: { message: '停止请求已发送' }, error: null, status: { running: true, stopping: true }, statusError: null });
});

test('stop keeps a successful outcome when status is unavailable and recognizes no_task', async () => {
  const unavailable = new Error('status unavailable');
  const result = await executeStop({ request: async () => ({ message: '停止请求已发送' }), getStatus: async () => { throw unavailable; } });
  assert.deepEqual(result, { outcome: 'stopping', response: { message: '停止请求已发送' }, error: null, status: null, statusError: unavailable });
  const noTask = await executeStop({ request: async () => ({ message: '无运行中任务' }), getStatus: async () => ({ running: false }) });
  assert.deepEqual(noTask, { outcome: 'no_task', response: { message: '无运行中任务' }, error: null, status: { running: false }, statusError: null });
});

test('stop returns error for a server failure', async () => {
  const error = Object.assign(new Error('unavailable'), { status: 500 });
  const result = await executeStop({ request: async () => { throw error; }, getStatus: async () => ({}) });
  assert.deepEqual(result, { outcome: 'error', response: null, error, status: null, statusError: null });
});

test('bounded status reads settle refresh and stop actions so their gates release', async () => {
  const timers = [];
  const timeoutOptions = {
    statusTimeoutMs: 1,
    setTimeoutFn(callback) { timers.push(callback); callback(); return 'timer'; },
    clearTimeoutFn(timer) { assert.equal(timer, 'timer'); },
  };
  const refreshGate = createActionGate();
  const refresh = await refreshGate.run(() => executeRefresh({ request: async () => ({ run_id: 'r1' }), getStatus: () => new Promise(() => {}), body: {}, ...timeoutOptions }));
  assert.equal(refresh.outcome, 'started');
  assert.equal(refresh.status, null);
  assert.ok(refresh.statusError instanceof TypeError);
  assert.equal(refreshGate.busy, false);
  const stopGate = createActionGate();
  const stop = await stopGate.run(() => executeStop({ request: async () => ({ message: 'stopping' }), getStatus: () => new Promise(() => {}), ...timeoutOptions }));
  assert.equal(stop.outcome, 'stopping');
  assert.equal(stop.status, null);
  assert.ok(stop.statusError instanceof TypeError);
  assert.equal(stopGate.busy, false);
  assert.equal(timers.length, 2);
});

test('causal task comparator rejects lower sequences even for an expected matching run', () => {
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'new', running: true, completed: 8 }, incoming: { run_id: 'new', running: true, completed: 5 }, currentSequence: 8, incomingSequence: 5, expectedRunId: 'new' }), false);
});

test('causal task comparator accepts a lower sequence for an expected new run after backend restart', () => {
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'old', running: false, completed: 100 },
    incoming: { run_id: 'new', running: true, completed: 0 },
    currentSequence: 100,
    incomingSequence: 1,
    expectedRunId: 'new',
  }), true);
});

test('confirmed expected run releases identity pinning without weakening stale update guards', () => {
  assert.equal(typeof betaController.remainingExpectedRunId, 'function');
  const expectedAfterR1 = betaController.remainingExpectedRunId('r1', { run_id: 'r1', running: true, log_seq: 10 });
  assert.equal(expectedAfterR1, '');
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'r1', running: false, completed: 10 },
    incoming: { run_id: 'r2', running: true, completed: 0 },
    currentSequence: 10,
    incomingSequence: 11,
    expectedRunId: expectedAfterR1,
  }), true);
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'r2', running: true, completed: 1 },
    incoming: { run_id: '', running: false, completed: 0 },
    currentSequence: 11,
    incomingSequence: 0,
    expectedRunId: expectedAfterR1,
    allowSequenceReset: true,
  }), true);
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'r2', running: true, completed: 1 },
    incoming: { run_id: 'r1', running: true, completed: 0 },
    currentSequence: 11,
    incomingSequence: 10,
    expectedRunId: expectedAfterR1,
  }), false);
});

test('expected identity is retained for empty and non-matching confirmed statuses', () => {
  assert.equal(typeof betaController.remainingExpectedRunId, 'function');
  assert.equal(betaController.remainingExpectedRunId('r1', { running: false }), 'r1');
  assert.equal(betaController.remainingExpectedRunId('r1', { run_id: 'r2', running: true }), 'r1');
});

test('authoritative lower-sequence restart frame releases an unmatched obsolete expectation', () => {
  const idleAfterRestart = { running: false, log_seq: 0 };
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'old', running: false, completed: 100 },
    incoming: idleAfterRestart,
    currentSequence: 100,
    incomingSequence: 0,
    expectedRunId: 'r1',
    allowSequenceReset: true,
  }), true);
  assert.equal(betaController.remainingExpectedRunId('r1', idleAfterRestart, {
    allowSequenceReset: true,
    currentSequence: 100,
    incomingSequence: 0,
  }), '');
});

test('authoritative equal-sequence generation frame releases an unmatched obsolete expectation', () => {
  const idleAfterRestart = { running: false, log_seq: 0 };
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'old', running: true, completed: 1 },
    incoming: idleAfterRestart,
    currentSequence: 0,
    incomingSequence: 0,
    expectedRunId: 'r1',
    allowSequenceReset: true,
  }), true);
  assert.equal(betaController.remainingExpectedRunId('r1', idleAfterRestart, {
    allowSequenceReset: true,
    currentSequence: 0,
    incomingSequence: 0,
  }), '');
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'old', running: true, completed: 1 },
    incoming: idleAfterRestart,
    currentSequence: 0,
    incomingSequence: 0,
    expectedRunId: 'r1',
    allowSequenceReset: false,
  }), false);
});

test('causal task comparator limits connection reset permission to the authoritative first snapshot', () => {
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'old', running: false, completed: 100 },
    incoming: { run_id: '', running: false, completed: 0 },
    currentSequence: 100,
    incomingSequence: 0,
    allowSequenceReset: true,
  }), true);
  assert.equal(shouldAcceptTaskUpdate({
    current: { runId: 'new', running: true, completed: 1 },
    incoming: { run_id: 'stale', running: true, completed: 0 },
    currentSequence: 1,
    incomingSequence: 0,
    allowSequenceReset: false,
  }), false);
});

test('causal task comparator is monotonic for equal-sequence updates of one run', () => {
  const stopping = { runId: 'r1', running: true, stopping: true, completed: 8 };
  const running = { run_id: 'r1', running: true, completed: 8 };
  assert.equal(shouldAcceptTaskUpdate({ current: stopping, incoming: running, currentSequence: 8, incomingSequence: 8 }), false);
  assert.equal(shouldAcceptTaskUpdate({ current: running, incoming: stopping, currentSequence: 8, incomingSequence: 8 }), true);
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'r1', success: 8, completed: 8 }, incoming: running, currentSequence: 8, incomingSequence: 8 }), false);
  assert.equal(shouldAcceptTaskUpdate({ current: running, incoming: { run_id: 'r1', success: 8, completed: 8 }, currentSequence: 8, incomingSequence: 8 }), true);
});

test('causal task comparator accepts the new expected run then rejects stale SSE snapshots', () => {
  const initial = { runId: 'old', running: true, completed: 2 };
  const direct = { run_id: 'new', running: true, completed: 1 };
  assert.equal(shouldAcceptTaskUpdate({ current: initial, incoming: direct, currentSequence: 5, incomingSequence: 6, expectedRunId: 'new' }), true);
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'new', running: true, completed: 1 }, incoming: { run_id: 'old', running: true, completed: 2 }, currentSequence: 6, incomingSequence: 5, expectedRunId: 'new' }), false);
});

test('causal task comparator uses expected identity only to resolve ambiguous run changes', () => {
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'expected', running: true }, incoming: { run_id: 'other', running: true }, currentSequence: 8, incomingSequence: 8, expectedRunId: 'expected' }), false);
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'old', running: true }, incoming: { run_id: 'expected', running: true }, currentSequence: 8, incomingSequence: 8, expectedRunId: 'expected' }), true);
  assert.equal(shouldAcceptTaskUpdate({ current: { runId: 'old', running: false }, incoming: { run_id: 'new', running: true }, currentSequence: null, incomingSequence: null }), true);
});

test('causal task comparator compares normalized elapsed and total monotonically', () => {
  const current = { runId: 'r1', running: true, total: 100, completed: 10, elapsed: 12 };
  assert.equal(shouldAcceptTaskUpdate({ current, incoming: { run_id: 'r1', running: true, total: 100, completed: 10, elapsed_s: 24 }, currentSequence: 8, incomingSequence: 8 }), true);
  assert.equal(shouldAcceptTaskUpdate({ current, incoming: { run_id: 'r1', running: true, total: 100, completed: 10, elapsed_s: 6 }, currentSequence: 8, incomingSequence: 8 }), false);
  assert.equal(shouldAcceptTaskUpdate({ current, incoming: { run_id: 'r1', running: true, total: 0, completed: 10, elapsed_s: 12 }, currentSequence: 8, incomingSequence: 8 }), false);
  assert.equal(shouldAcceptTaskUpdate({ current, incoming: { run_id: 'r1', running: true, total: 120, completed: 10, elapsed_s: 12 }, currentSequence: 8, incomingSequence: 8 }), true);
});

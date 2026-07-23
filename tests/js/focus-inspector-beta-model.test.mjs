import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BETA_MODEL_VERSION,
  normalizeOverview, normalizeEnvironment, normalizeRefreshBot, normalizeTask,
  classifyTask, taskPresentation, refreshDefaults, validateRefreshInput,
  buildRefreshRequest, mapRefreshValidationDetail, controlAvailability,
  deriveActionQueue, classifyRequestError,
} from '../../client/static/prototypes/focus-inspector-beta-model.mjs';

test('normalizes an overview without retaining paths or task secrets', () => {
  const overview = normalizeOverview({
    auth: { total: 6006, fresh: 3443, needs_refresh: 2563 }, mint_gap: 0,
    recent_batches: [{ name: '/srv/private/batch_0716_j12n94.json', ok: 92, fail: 2, n: 94, jobs: 12, wall_s: 133, thr: .692, mtime: 100 }],
    task: { running: true, kind: 'refresh', total: 6006, completed: 3460, success: 3444, failed: 16, percent: 57.6, elapsed_s: 776, message: 'secret@example.test last_line' }, ts: 1000,
  });
  assert.equal(BETA_MODEL_VERSION, 1);
  assert.deepEqual(overview.kpis, { auth: 6006, fresh: 3443, needs: 2563, gap: 0 });
  assert.deepEqual(overview.batches, [{ name: 'batch_0716_j12n94.json', ok: 92, fail: 2, total: 94, jobs: 12, elapsed: 133, throughput: .692, mtime: 100 }]);
  assert.equal(overview.updatedAt, 1000);
  assert.equal(overview.task.running, true);
  const encoded = JSON.stringify(overview);
  assert.equal(encoded.includes('/srv/private'), false);
  assert.equal(encoded.includes('secret@example.test'), false);
});

test('normalizes task state and classifies precedence safely', () => {
  assert.equal(normalizeTask({ running: false, message: 'stopping' }).stopping, false);
  assert.equal(normalizeTask({ running: true, message: ' STOPPING ' }).stopping, true);
  assert.equal(normalizeTask({ running: true, message: 'stopped' }).stopped, false);
  assert.equal(normalizeTask({ running: false, message: ' STOPPED ' }).stopped, true);
  assert.equal(normalizeTask({ message: ' EXIT=-1 ' }).abnormal, true);
  assert.equal(normalizeTask({ message: 'some error secret@example.test' }).abnormal, true);
  assert.equal(classifyTask({ running: true }), 'running');
  assert.equal(classifyTask({ running: true, stopping: true }), 'stopping');
  assert.equal(classifyTask({ stopped: true }), 'stopped');
  assert.equal(classifyTask({ success: 5, failed: 2 }), 'partial');
  assert.equal(classifyTask({ success: 0, failed: 2 }), 'failed');
  assert.equal(classifyTask({ abnormal: true }), 'failed');
  assert.equal(classifyTask({ abnormal: true, success: 2 }), 'partial');
  assert.equal(classifyTask({ message: 'exit=1' }), 'failed');
  assert.equal(classifyTask({ message: 'exit=1', success: 2 }), 'partial');
  assert.equal(classifyTask({ completed: 5 }), 'success');
  assert.equal(classifyTask({}), 'idle');
});

test('presents task labels, progress, throughput, and controls', () => {
  const running = taskPresentation({ running: true, kind: 'refresh', total: 6006, completed: 3460, elapsed: 776 });
  assert.equal(running.title, 'Refresh · 正在保活');
  assert.equal(running.percent, 57.6);
  assert.equal(running.throughput, 4.46);
  assert.equal(running.canStart, false);
  assert.equal(running.canStop, true);
  assert.equal(running.completed, 3460);
  assert.equal(running.total, 6006);
  assert.equal(running.status, '正在保活');
  assert.equal(taskPresentation({ running: true, kind: 'mint' }).title, 'Mint · 运行中');
  assert.equal(taskPresentation({ running: true, stopping: true, kind: 'refresh' }).title, 'Refresh · 正在停止');
  assert.equal(taskPresentation({ kind: 'custom_job', completed: 1 }).title, 'custom_job · 已完成');
  assert.equal(taskPresentation({ total: 10, completed: 5, percent: 0 }).percent, 50);
  assert.equal(taskPresentation({}).title, '暂无运行任务');
  assert.equal(taskPresentation({ kind: 'bad kind!?', total: 3, completed: 1 }).kind, 'bad kind!?');
  assert.deepEqual(
    ['refresh', 'register', 'mint', 'export', 'upload', 'cpa_upload', 'probe', 'check_chain'].map((kind) => taskPresentation({ kind, success: 1 }).title),
    ['Refresh · 已完成', '注册 · 已完成', 'Mint · 已完成', 'Export · 已完成', 'Upload · 已完成', 'Upload · 已完成', '探活 · 已完成', '代理链检查 · 已完成'],
  );
  assert.equal(taskPresentation({ kind: 'refresh', stopping: true, running: true }).status, '正在停止');
  assert.equal(taskPresentation({ kind: 'refresh', stopped: true }).status, '已停止');
  assert.equal(taskPresentation({ kind: 'refresh', success: 1, failed: 1 }).status, '部分成功');
  assert.equal(taskPresentation({ kind: 'refresh', failed: 1 }).status, '失败');
  assert.equal(taskPresentation({ kind: 'refresh', success: 1 }).status, '已完成');
  assert.equal(taskPresentation({}).status, '暂无运行任务');
});

test('normalizes environment to readiness flags with no secret configuration', () => {
  const env = normalizeEnvironment({
    cloudmail: { url: 'https://mail.example', domains: ['example.test'] },
    captcha: { ready: true, backend: 'mystery', api_key: 'secret-key' },
    proxy: { local_proxy: 'http://u:p@private.proxy' }, ready_for_mint: true, ready_for_cpa_upload: false,
  });
  assert.deepEqual(env, { cloudmail: true, captcha: true, captchaBackend: 'Custom', proxy: true, mint: true, upload: false });
  assert.equal(JSON.stringify(env).includes('private.proxy'), false);
  assert.equal(normalizeEnvironment({ captcha: {} }).captchaBackend, 'Auto');
  assert.equal(normalizeEnvironment({ captcha: { backend: ' CapSolver ' } }).captchaBackend, 'CapSolver');
  assert.equal(normalizeEnvironment({ proxy: { local_proxy: '', default: 'http://fallback.proxy' } }).proxy, true);
  assert.equal(normalizeEnvironment({ captcha: { ready: 'ready' }, ready_for_mint: 1, ready_for_cpa_upload: 'yes' }).captcha, true);
  assert.equal(normalizeEnvironment({ captcha: { ready: 'ready' }, ready_for_mint: 1, ready_for_cpa_upload: 'yes' }).mint, true);
  assert.equal(normalizeEnvironment({ captcha: { ready: 'ready' }, ready_for_mint: 1, ready_for_cpa_upload: 'yes' }).upload, true);
  assert.equal(normalizeEnvironment({ captcha: { backend: 'capsolver' } }).captchaBackend, 'CapSolver');
  assert.equal(normalizeEnvironment({ captcha: { backend: 'yescaptcha' } }).captchaBackend, 'YesCaptcha');
  assert.equal(normalizeEnvironment({ captcha: { backend: '2captcha' } }).captchaBackend, 'Custom');
  assert.equal(normalizeEnvironment({ captcha: { backend: 'auto' } }).captchaBackend, 'Auto');
  assert.equal(normalizeEnvironment({ captcha: { backend: 'twocaptcha' } }).captchaBackend, '2Captcha');
  assert.equal(normalizeEnvironment({ cloudmail: { url: true, domains: true } }).cloudmail, true);
});

test('normalizes refresh bot public state only', () => {
  const bot = normalizeRefreshBot({ enabled: true, jobs: 8, limit: 200, skip_if_busy: true, next_in_s: 1234, task_busy: true, last_run: { action: 'ran', ts: '2026-07-23T06:00:00Z', message: 'secret@example.test' } });
  assert.deepEqual(bot, { enabled: true, jobs: 8, limit: 200, skipIfBusy: true, nextIn: 1234, taskBusy: true, lastRunAction: '自动保活已触发', lastRunAt: 1784786400 });
  assert.equal(JSON.stringify(bot).includes('secret@example.test'), false);
  assert.equal(normalizeRefreshBot({ jobs: 99, limit: -1, skip_if_busy: false, next_in_s: -2 }).jobs, 16);
  assert.equal(normalizeRefreshBot({ jobs: 99, limit: -1, skip_if_busy: false, next_in_s: -2 }).limit, 0);
  assert.equal(normalizeRefreshBot({ skip_if_busy: false }).skipIfBusy, false);
  assert.equal(normalizeRefreshBot({ jobs: 0, next_in_s: -2 }).jobs, 1);
  assert.equal(normalizeRefreshBot({ next_in_s: -2 }).nextIn, 0);
  assert.equal(normalizeRefreshBot({}).nextIn, null);
  assert.equal(normalizeRefreshBot({ last_run: { action: 'skipped_busy' } }).lastRunAction, '任务忙，自动保活已跳过');
  assert.equal(normalizeRefreshBot({ enabled: 'yes', task_busy: 1, last_run: { ts: 'July 23, 2026 06:00:00 UTC' } }).enabled, true);
  assert.equal(normalizeRefreshBot({ enabled: 'yes', task_busy: 1, last_run: { ts: 'July 23, 2026 06:00:00 UTC' } }).taskBusy, true);
  assert.equal(normalizeRefreshBot({ last_run: { ts: 'July 23, 2026 06:00:00 UTC' } }).lastRunAt, 1784786400);
});

test('builds refresh defaults and validates bounded form input', () => {
  assert.deepEqual(refreshDefaults({ needs: 20001 }, { jobs: 0 }), { limit: 10000, jobs: 1, needsOnly: true, remintOnRevoke: true });
  assert.deepEqual(refreshDefaults({ needs: 0 }, { jobs: 4 }), { limit: 0, jobs: 4, needsOnly: true, remintOnRevoke: true });
  assert.deepEqual(validateRefreshInput({ limit: 1.5, jobs: 1, needsOnly: false }, { needs: 1 }), { limit: '数量上限需在 0–10000 之间' });
  assert.deepEqual(validateRefreshInput({ limit: 1, jobs: 17, needsOnly: false }, { needs: 1 }), { jobs: '并发需在 1–16 之间' });
  assert.deepEqual(validateRefreshInput({ limit: 1, jobs: 1, needsOnly: true }, { needs: 0 }), { scope: '当前没有需保活项；如需全量处理，请取消“仅处理需保活”' });
  assert.deepEqual(validateRefreshInput({ limit: 1, jobs: 1, needsOnly: true }, {}), {});
  assert.deepEqual(validateRefreshInput({ limit: 1, jobs: 1, needsOnly: true }, { needs: -1 }), {});
  assert.deepEqual(validateRefreshInput({ limit: 0, jobs: 1, needsOnly: false }, { needs: 0 }), {});
  assert.deepEqual(buildRefreshRequest({ limit: '12', jobs: '3', needsOnly: 0, remintOnRevoke: 1 }), { limit: 12, jobs: 3, needs_only: false, remint_on_revoke: true });
  assert.deepEqual(buildRefreshRequest({ limit: 0, jobs: 4, needsOnly: false, remintOnRevoke: true }), { limit: 0, jobs: 4, needs_only: false, remint_on_revoke: true });
});

test('maps API validation safely and derives availability and queue', () => {
  assert.deepEqual(mapRefreshValidationDetail([{ loc: ['body', 'limit'], msg: 'private message', input: 'secret' }]), { limit: '数量上限无效' });
  assert.deepEqual(mapRefreshValidationDetail([{ loc: ['body', 'jobs'] }]), { jobs: '并发无效' });
  assert.deepEqual(mapRefreshValidationDetail([{ loc: ['body', 'other'], msg: 'secret' }]), { form: '请检查参数' });
  assert.deepEqual(controlAvailability({ task: {}, overviewReady: true }), { canStart: true, canStop: false, locked: false });
  assert.deepEqual(controlAvailability({ task: {}, overviewReady: true, pendingAction: 'starting' }), { canStart: false, canStop: false, locked: true });
  assert.deepEqual(controlAvailability({ task: {}, overviewReady: true, actionBusy: 'busy' }), { canStart: false, canStop: false, locked: true });
  assert.deepEqual(controlAvailability({ task: { running: true }, overviewReady: true, pendingAction: 'stopping' }), { canStart: false, canStop: false, locked: true });
  const queue = deriveActionQueue({ kpis: { needs: 24 }, batches: [{ fail: 2 }], task: { success: 10, failed: 1 } });
  assert.equal(queue.length, 3);
  assert.deepEqual(queue.map((item) => item.action), ['refresh', 'refresh', 'batches']);
  assert.equal(queue.filter((item) => item.recommended).length, 1);
  assert.deepEqual(queue, [
    { tone: 'danger', title: '任务异常', impact: '影响 1 项', count: 1, action: 'refresh', label: '重新保活', recommended: true },
    { tone: 'warning', title: '待保活', impact: '24 个账号需要 Refresh', count: 24, action: 'refresh', label: '配置保活', recommended: false },
    { tone: 'neutral', title: '最近批次有失败', impact: '失败 2 项', count: 2, action: 'batches', label: '查看最近批次', recommended: false },
  ]);
  assert.deepEqual(deriveActionQueue({ kpis: { needs: 0 }, batches: [], task: {} }), [{ tone: 'success', title: '当前无待处理异常', impact: '库存与最近任务状态正常', count: 0, action: 'none', label: '', recommended: false }]);
  assert.equal(normalizeOverview({ recent_batches: [{ name: '/private/' }] }).batches[0].name, '-');
  assert.equal(normalizeOverview({ recent_batches: [{ name: '/private/a secret@!.json' }] }).batches[0].name, 'a-secret-.json');
  assert.equal(normalizeOverview({ recent_batches: [{ name: `/private/${'a'.repeat(100)}.json` }] }).batches[0].name.length, 80);
  assert.equal(normalizeOverview({ recent_batches: [{ name: false }, { name: 0 }, { name: '' }] }).batches.every((batch) => batch.name === '-'), true);
});

test('classifies request errors as safe objects', () => {
  assert.deepEqual(classifyRequestError({ status: 409 }), { kind: 'conflict', message: '已有任务运行中' });
  assert.deepEqual(classifyRequestError({ status: 422 }), { kind: 'validation', message: '请检查参数' });
  assert.deepEqual(classifyRequestError({ status: 500 }), { kind: 'server', message: '服务暂时不可用' });
  assert.deepEqual(classifyRequestError(new TypeError('network')), { kind: 'network', message: '无法连接 GrokX' });
  assert.deepEqual(classifyRequestError({}), { kind: 'network', message: '无法连接 GrokX' });
  assert.deepEqual(classifyRequestError({ status: 400 }), { kind: 'request', message: '请求失败' });
  assert.deepEqual(classifyRequestError({ status: 0 }), { kind: 'network', message: '无法连接 GrokX' });
  assert.deepEqual(classifyRequestError({ status: 'bad' }), { kind: 'network', message: '无法连接 GrokX' });
});

test('keeps task percent explicit until presentation fallback', () => {
  const task = normalizeTask({ running: 'yes', total: 10, completed: 5, percent: 0, stopping: 'now', stopped: 'later' });
  assert.equal(task.running, true);
  assert.equal(task.stopping, true);
  assert.equal(task.stopped, true);
  assert.equal(task.percent, 0);
  assert.equal(taskPresentation(task).percent, 50);
});

test('normalizeTask uses only reference fields and preserves kind and runId strings', () => {
  const task = normalizeTask({
    type: 'refresh', id: 'alias-id', ok: 8, fail: 2,
    kind: 'custom kind!?', runId: 'run id!?', abnormal: 'truthy',
  });
  assert.equal(task.kind, 'custom kind!?');
  assert.equal(task.runId, 'run id!?');
  assert.equal(task.success, 0);
  assert.equal(task.failed, 0);
  assert.equal(task.abnormal, true);
  assert.deepEqual(normalizeTask({ type: 'refresh', id: 'alias-id' }).kind, '');
  assert.deepEqual(normalizeTask({ type: 'refresh', id: 'alias-id' }).runId, '');
});

test('normalizeOverview accepts only reference payload keys', () => {
  const overview = normalizeOverview({
    auth: { total: 1, fresh: 2, needs: 3 }, gap: 4,
    recent_batches: [{ path: '/private/secret.json' }], updated_at: 5,
  });
  assert.deepEqual(overview.kpis, { auth: 1, fresh: 2, needs: 0, gap: 0 });
  assert.equal(overview.batches[0].name, '-');
  assert.equal(overview.updatedAt, 0);
});

test('safe batch all-unsafe leaf is exactly a dash', () => {
  assert.equal(normalizeOverview({ recent_batches: [{ name: '@' }] }).batches[0].name, '-');
});

test('controlAvailability accepts truthy overview readiness', () => {
  assert.deepEqual(controlAvailability({ task: {}, overviewReady: 'ready' }), { locked: false, canStart: true, canStop: false });
});

test('refresh bot ignores top-level last-run action aliases', () => {
  assert.equal(normalizeRefreshBot({ last_run_action: 'ran' }).lastRunAction, '');
});

test('abnormal task without a failed counter remains an executable refresh action', () => {
  const queue = deriveActionQueue({ task: { message: 'exit=1' } });
  assert.deepEqual(queue, [{
    tone: 'danger', title: '任务异常', impact: '影响 1 项', count: 1,
    action: 'refresh', label: '重新保活', recommended: true,
  }]);
});

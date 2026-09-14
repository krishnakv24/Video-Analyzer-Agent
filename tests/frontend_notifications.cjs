// node tests/frontend_notifications.cjs; existing localhost app serves static assets only.
// Optional: PLAYWRIGHT_MODULE, EDGE_EXECUTABLE, FRONTEND_TEST_URL. All API calls are mocked.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { randomUUID } = require('node:crypto');
function loadPlaywright() {
  if (process.env.PLAYWRIGHT_MODULE) return require(path.resolve(process.env.PLAYWRIGHT_MODULE));
  try { return require('playwright'); }
  catch (error) {
    const driver = path.resolve(__dirname, '../.venv/Lib/site-packages/~laywright/driver/package');
    if (error.code !== 'MODULE_NOT_FOUND' || !fs.existsSync(driver)) throw error;
    return require(driver);
  }
}
const { chromium } = loadPlaywright();
const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:8000';
const user = { id: 'notification-test-user', username: 'Notification test', csrf_token: 'test-csrf' };
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function gate() {
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  return { wait, release, entered: false };
}
async function until(predicate, label) {
  for (let attempt = 0; attempt < 250; attempt++) {
    if (await predicate()) return;
    await pause(20);
  }
  throw new Error(`Timed out: ${label}`);
}
function backend() {
  const state = { jobs: [], uploads: new Map(), errors: [], deletes: [], nextUploadGate: null, held: [] };
  state.seed = (name, status = 'ready') => {
    const id = randomUUID();
    const job = { id, session_id: id, upload_id: randomUUID(), filename: name,
      status, entities: ['People'], instructions: '', metadata: {}, error: null };
    state.jobs.push(job);
    return job;
  };
  state.handle = async route => {
    const request = route.request(), url = new URL(request.url()).pathname, method = request.method();
    const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    try {
      if (method !== 'GET') assert.equal(request.headers()['x-csrf-token'], user.csrf_token);
      if (url === '/api/me' && method === 'GET') return send(user);
      if (url === '/api/jobs' && method === 'GET') return send({ jobs: state.jobs.map(({ hold, ...job }) => job) });
      if (url === '/api/uploads' && method === 'POST') {
        const upload = { ...request.postDataJSON(), id: randomUUID(), offset: 0, status: 'uploading', chunk_size: 1024 };
        upload.hold = state.nextUploadGate;
        state.nextUploadGate = null;
        state.uploads.set(upload.id, upload);
        return send(upload, 201);
      }
      const uploadPath = url.match(/^\/api\/uploads\/([^/]+)(\/complete)?$/);
      if (uploadPath) {
        const upload = state.uploads.get(uploadPath[1]);
        assert.ok(upload, 'Known upload ID');
        if (method === 'DELETE') {
          state.deletes.push(upload.id);
          return send({ id: upload.id, status: 'deleted', cleanup_pending: false });
        }
        if (method === 'GET') return send(upload);
        if (method === 'PATCH') {
          if (upload.hold) { upload.hold.entered = true; await upload.hold.wait; }
          assert.equal(Number(request.headers()['upload-offset']), upload.offset);
          upload.offset += request.postDataBuffer().length;
          return send(upload);
        }
        if (method === 'POST' && uploadPath[2]) {
          assert.equal(upload.offset, upload.size);
          upload.status = 'complete';
          return send(upload);
        }
      }
      if (url === '/api/jobs' && method === 'POST') {
        const body = request.postDataJSON(), upload = state.uploads.get(body.upload_id);
        assert.equal(upload.status, 'complete');
        const job = state.seed(upload.filename, 'preprocessing');
        Object.assign(job, body);
        return send(job, 201);
      }
      const jobPath = url.match(/^\/api\/jobs\/([^/]+)(\/messages)?$/);
      if (jobPath && method === 'GET') {
        const job = state.jobs.find(item => item.id === jobPath[1]);
        assert.ok(job, 'Known job ID');
        if (jobPath[2]) return send({ messages: [] });
        const { hold, ...snapshot } = job;
        if (hold) { job.hold = null; hold.entered = true; await hold.wait; }
        return send(snapshot);
      }
      throw new Error(`Unexpected API: ${method} ${url}`);
    } catch (error) {
      state.errors.push(error.message);
      return send({ detail: error.message }, 500).catch(() => {});
    }
  };
  return state;
}
const notices = page => page.evaluate(() => window.notificationCalls);
const selected = (page, job) => page.locator(`.conversation[data-job-id="${job.id}"]`);
async function upload(page, filename) {
  await page.locator('#new-conversation').click();
  await page.locator('#video-file').setInputFiles({ name: filename, mimeType: 'video/mp4', buffer: Buffer.from('testvideo') });
  await page.locator('.analyze-button').click();
}
async function terminalButton(page) {
  assert.equal(await page.locator('.analyze-button').isDisabled(), true, 'A completed video cannot be submitted twice');
  assert.doesNotMatch(await page.locator('.analyze-button').textContent(), /uploading/i, 'Completed upload must lose its busy label');
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.EDGE_EXECUTABLE ? { executablePath: process.env.EDGE_EXECUTABLE } : { channel: 'msedge' }) });
  const failures = [];
  async function scenario(name, seed, check) {
    const state = backend();
    const context = await browser.newContext({ viewport: { width: 1400, height: 1000 }, serviceWorkers: 'block' });
    const errors = [];
    try {
      seed(state);
      await context.route('**/api/**', state.handle);
      await context.addInitScript(() => {
        window.notificationCalls = [];
        window.throwNotifications = false;
        Object.defineProperty(window, 'Notification', { configurable: true, value: class {
          static permission = 'granted';
          static requestPermission() { return Promise.resolve('granted'); }
          constructor(title, options) {
            window.notificationCalls.push({ title, body: options?.body, tag: options?.tag });
            if (window.throwNotifications) throw new Error('Synthetic notification failure');
          }
        } });
        const originalInterval = window.setInterval;
        window.setInterval = (callback, delay, ...args) => originalInterval(callback, delay === 3000 ? 75 : delay, ...args);
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.setDefaultTimeout(5000);
      await page.goto(baseURL);
      await page.locator('#workspace-screen').waitFor({ state: 'visible' });
      await check(page, state);
      assert.deepEqual(state.errors, [], 'All requests honored the mock API contract');
      assert.deepEqual(errors, [], 'No uncaught JavaScript exceptions');
      console.log(`PASS: ${name}`);
    } catch (error) { failures.push(`${name}: ${error.message}`); console.error(`FAIL: ${name}: ${error.message}`); }
    finally { state.held.forEach(item => item.release()); await context.close(); }
  }
  try {
    await scenario('existing ready sessions stay silent on open/reopen/reload', state => {
      state.seed('old-a.mp4'); state.seed('old-b.mp4');
    }, async (page, state) => {
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'initial ready view');
      assert.equal((await notices(page)).length, 0, 'Loading existing ready data is not a completion event');
      await selected(page, state.jobs[1]).click();
      await selected(page, state.jobs[0]).click();
      await pause(250);
      assert.equal((await notices(page)).length, 0);
      await page.reload();
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'ready after reload');
      assert.equal((await notices(page)).length, 0);
    });
    await scenario('new upload and ready transitions notify once; completed control resets', () => {}, async (page, state) => {
      await upload(page, 'new-camera.mp4');
      await until(async () => state.jobs.length === 1 && (await notices(page)).length >= 1, 'new upload notification');
      await terminalButton(page);
      assert.equal((await notices(page)).length, 1);
      state.jobs[0].status = 'ready';
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'new ready event');
      assert.equal((await notices(page)).length, 2);
      await selected(page, state.jobs[0]).click();
      await pause(250);
      assert.equal((await notices(page)).length, 2, 'Reopening must not replay ready');
      await terminalButton(page);
      await page.reload();
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'completed session after reload');
      assert.equal((await notices(page)).length, 0, 'Reload must not replay either completion event');
      await terminalButton(page);
    });
    await scenario('parallel jobs report background completion independently', () => {}, async (page, state) => {
      const held = gate(); state.held.push(held); state.nextUploadGate = held;
      await upload(page, 'camera-a.mp4');
      await until(() => held.entered, 'A paused in upload');
      await upload(page, 'camera-b.mp4');
      await until(() => state.jobs.length === 1, 'B uploaded while A paused');
      const b = state.jobs[0]; held.release();
      await until(() => state.jobs.length === 2, 'A uploaded');
      const a = state.jobs[1];
      await until(async () => (await notices(page)).length === 2, 'separate upload events');
      a.status = 'ready';
      await until(async () => (await notices(page)).length === 3, 'background A ready notification');
      assert.equal(await selected(page, b).getAttribute('class').then(value => value.includes('active')), true);
      assert.equal(await page.locator('#job-state').textContent(), 'Preparing', 'A readiness must not enable B chat');
      b.status = 'ready';
      await until(async () => (await notices(page)).length === 4, 'B ready notification');
      await selected(page, a).click(); await selected(page, b).click(); await pause(250);
      assert.equal((await notices(page)).length, 4);
    });
    await scenario('Notification errors cannot fail uploads or repeat ready events', () => {}, async (page, state) => {
      await page.evaluate(() => { window.throwNotifications = true; });
      await upload(page, 'notification-error.mp4');
      await until(() => state.jobs.length === 1, 'upload committed despite notification failure');
      state.jobs[0].status = 'ready';
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'ready despite notification failure');
      await pause(350); await terminalButton(page);
      assert.equal((await notices(page)).length, 2, 'Only one attempt per event even when Notification throws');
      assert.doesNotMatch(await page.locator('#chat-subtitle').textContent(), /could not check|failed/i);
      assert.equal(await page.locator('.conversation[data-draft-id]').count(), 0, 'Successful upload must not become a failed draft');
      assert.deepEqual(state.deletes, [], 'Notification errors must never delete a successful video');
    });
    await scenario('a stale response after navigation cannot regress the ready view', state => {
      const job = state.seed('stale-status.mp4', 'preprocessing'); job.hold = gate(); state.held.push(job.hold);
      state.seed('already-ready.mp4');
    }, async (page, state) => {
      const readyJob = state.jobs[1], held = state.held[0];
      await until(() => held.entered, 'old status request held');
      await selected(page, readyJob).click();
      await until(async () => await page.locator('#job-state').textContent() === 'Ready', 'ready conversation displayed');
      held.release(); await pause(250);
      assert.equal(await page.locator('#job-state').textContent(), 'Ready');
      assert.equal(await page.locator('#chat-input').isDisabled(), false);
      assert.equal((await notices(page)).length, 0, 'Opening baseline ready data stays silent');
    });
  } finally { await browser.close(); }
  assert.deepEqual(failures, [], 'Notification regression failures');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });

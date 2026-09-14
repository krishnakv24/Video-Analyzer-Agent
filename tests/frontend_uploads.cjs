// Start the app on localhost:8000, then run: node tests/frontend_uploads.cjs
// Optional: PLAYWRIGHT_MODULE, EDGE_EXECUTABLE, FRONTEND_TEST_URL.
// Uses existing Playwright/Edge installations. Every /api/** request is mocked;
// the running application supplies static frontend assets only.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { randomUUID } = require('node:crypto');
function loadPlaywright() {
  if (process.env.PLAYWRIGHT_MODULE) return require(path.resolve(process.env.PLAYWRIGHT_MODULE));
  try { return require('playwright'); }
  catch (error) {
    const localDriver = path.resolve(__dirname, '../.venv/Lib/site-packages/~laywright/driver/package');
    if (error.code !== 'MODULE_NOT_FOUND' || !fs.existsSync(localDriver)) throw error;
    return require(localDriver);
  }
}
const { chromium } = loadPlaywright();

const baseURL = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:8000';
const user = { id: 'browser-upload-test-user', username: 'Upload test', csrf_token: 'test-csrf' };
const fileInfo = { name: 'camera.mp4', lastModified: 1700000000000 };
const payloadA = 'abcdefghijkl';
const payloadB = 'ZYXWVUTSRQPO';
const payloadD = '123456789012';
const storagePrefix = `frameUploadDraft:${user.id}:`;

function gate() {
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  return { wait, release, entered: false };
}

async function until(predicate, label, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out: ${label}`);
}

function mockBackend() {
  const state = { uploads: new Map(), jobs: [], errors: [], patches: [], deletes: [], nextPlan: null };
  const uploadResponse = item => ({ id: item.id, filename: item.filename, size: item.size,
    offset: item.bytes.length, status: item.complete ? 'complete' : 'uploading', chunk_size: 4 });

  state.handle = async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const method = request.method();
    const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    try {
      if (method !== 'GET') assert.equal(request.headers()['x-csrf-token'], user.csrf_token);
      if (method === 'GET' && pathname === '/api/me') return respond(user);
      if (method === 'GET' && pathname === '/api/jobs') return respond({ jobs: [...state.jobs].reverse() });
      if (method === 'POST' && pathname === '/api/uploads') {
        const body = request.postDataJSON();
        const item = { id: randomUUID(), filename: body.filename, size: body.size,
          bytes: Buffer.alloc(0), complete: false, ...state.nextPlan };
        state.nextPlan = null;
        state.uploads.set(item.id, item);
        return respond(uploadResponse(item), 201);
      }
      const uploadMatch = pathname.match(/^\/api\/uploads\/([^/]+)(\/complete)?$/);
      if (uploadMatch) {
        const item = state.uploads.get(uploadMatch[1]);
        if (!item) return respond({ detail: 'Upload not found' }, 404);
        if (method === 'DELETE' && !uploadMatch[2]) {
          state.deletes.push(item.id);
          if (state.jobs.some(job => job.upload_id === item.id)) return respond({ detail: 'Upload belongs to a saved conversation' }, 409);
          if (item.deleteFailure) return respond({ detail: 'Simulated cleanup connection failure' }, 503);
          state.uploads.delete(item.id);
          return respond({ id: item.id, deleted: true, cleanup_pending: false });
        }
        if (method === 'GET' && !uploadMatch[2]) return respond(uploadResponse(item));
        if (method === 'PATCH' && !uploadMatch[2]) {
          const offset = Number(request.headers()['upload-offset']);
          const bytes = request.postDataBuffer();
          state.patches.push({ id: item.id, offset, bytes });
          assert.equal(offset, item.bytes.length, 'The browser uses this upload’s committed offset');
          assert.ok(bytes && bytes.length > 0 && bytes.length <= 4);
          if (item.gate && offset === 0) {
            item.gate.entered = true;
            await item.gate.wait;
          }
          if (item.failAt === offset) return respond({ detail: item.failureReason || 'Simulated interrupted transfer' }, 503);
          item.bytes = Buffer.concat([item.bytes, bytes]);
          assert.ok(item.bytes.length <= item.size);
          return respond(uploadResponse(item));
        }
        if (method === 'POST' && uploadMatch[2]) {
          assert.equal(item.bytes.length, item.size, 'Only complete bytes can be finalized');
          item.complete = true;
          return respond(uploadResponse(item));
        }
      }
      if (method === 'POST' && pathname === '/api/jobs') {
        const body = request.postDataJSON();
        const upload = state.uploads.get(body.upload_id);
        assert.ok(upload && upload.complete);
        if (state.jobs.some(job => job.upload_id === upload.id)) return respond({ detail: 'Session already exists' }, 409);
        const id = randomUUID();
        const job = { ...body, id, session_id: id, filename: upload.filename,
          status: 'ready', metadata: {}, error: null };
        state.jobs.push(job);
        if (upload.loseJobResponse) return route.abort('failed');
        return respond(job, 201);
      }
      const jobMatch = pathname.match(/^\/api\/jobs\/([^/]+)(\/messages)?$/);
      if (method === 'GET' && jobMatch) {
        const job = state.jobs.find(item => item.id === jobMatch[1]);
        assert.ok(job, `Known session: ${pathname}`);
        return respond(jobMatch[2] ? { messages: [] } : job);
      }
      throw new Error(`Unexpected API request: ${method} ${pathname}`);
    } catch (error) {
      state.errors.push(error.message);
      await respond({ detail: error.message }, 500).catch(() => {});
    }
  };
  return state;
}

async function selectFile(page, text, overrides = {}) {
  await page.locator('#video-file').evaluate((input, value) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([value.text], value.name,
      { type: 'video/mp4', lastModified: value.lastModified }));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, { ...fileInfo, text, ...overrides });
}

async function newUpload(page, text, instructions, entities = ['People', 'Cars']) {
  await page.locator('#new-conversation').click();
  await selectFile(page, text);
  await page.locator('#instructions').fill(instructions);
  for (const entity of ['People', 'Cars', 'Motorcycles', 'Bicycles', 'Animals']) {
    await page.locator(`.entity-options input[value="${entity}"]`).setChecked(entities.includes(entity));
  }
  await page.locator('.analyze-button').click();
}

async function storedDrafts(page) {
  return page.evaluate(prefix => Object.keys(localStorage)
    .filter(key => key.startsWith(prefix))
    .map(key => ({ key, ...JSON.parse(localStorage.getItem(key)) })), storagePrefix);
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.EDGE_EXECUTABLE ? { executablePath: process.env.EDGE_EXECUTABLE } : { channel: 'msedge' }) });
  const state = mockBackend();
  const heldGates = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: 'block' });
    await context.route('**/api/**', state.handle);
    await context.addInitScript(() => {
      Object.defineProperty(window, 'Notification', { configurable: true, value: class {
        static permission = 'denied';
        static requestPermission() { return Promise.resolve('denied'); }
      } });
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    page.setDefaultTimeout(8000);
    await page.goto(baseURL);
    await page.locator('#workspace-screen').waitFor({ state: 'visible' });

    // A and B have identical browser metadata but completely different bytes.
    // Hold A's first PATCH; B must independently finish before A is released.
    const holdA = gate();
    heldGates.push(holdA);
    state.nextPlan = { gate: holdA };
    await newUpload(page, payloadA, 'Question for camera A', ['People']);
    await until(() => holdA.entered, 'A begins its held PATCH');
    const uploadA = [...state.uploads.values()][0];
    await newUpload(page, payloadB, 'Question for camera B', ['Cars']);
    await until(() => state.jobs.length === 1, 'B finishes while A remains active');
    const jobB = state.jobs[0];
    assert.notEqual(jobB.upload_id, uploadA.id);
    assert.equal(jobB.instructions, 'Question for camera B');
    assert.deepEqual(jobB.entities, ['Cars']);
    assert.equal(state.uploads.get(jobB.upload_id).bytes.toString(), payloadB);
    assert.equal(uploadA.bytes.length, 0);
    await until(async () => (await storedDrafts(page)).length === 1, 'B removes only its own stored draft');
    assert.equal((await storedDrafts(page))[0].uploadId, uploadA.id);
    await until(() => page.locator(`.conversation[data-job-id="${jobB.id}"].active`).count(), 'B keeps the active view');
    holdA.release();
    await until(() => state.jobs.length === 2, 'A completes after its gate opens');
    const jobA = state.jobs.find(job => job.upload_id === uploadA.id);
    assert.equal(jobA.instructions, 'Question for camera A');
    assert.deepEqual(jobA.entities, ['People']);
    assert.equal(uploadA.bytes.toString(), payloadA);
    await until(async () => (await storedDrafts(page)).length === 0, 'Both successful draft records clear');
    assert.equal(await page.locator(`.conversation[data-job-id="${jobB.id}"].active`).count(), 1,
      'A finishing in the background must not steal focus from B');

    // A page interruption retains a resumable draft, unlike a handled failure.
    // Seed exactly the state saved after its first acknowledged four-byte chunk.
    const uploadC = { id: randomUUID(), filename: fileInfo.name, size: payloadA.length,
      bytes: Buffer.from(payloadA.slice(0, 4)), complete: false };
    state.uploads.set(uploadC.id, uploadC);
    const savedC = { id: `draft-${randomUUID()}`, userId: user.id, uploadId: uploadC.id,
      fileInfo: { ...fileInfo, size: payloadA.length }, entities: ['Animals'], instructions: 'Resume camera C',
      progress: 33, createdAt: Date.now() };
    await page.evaluate(({ key, record }) => localStorage.setItem(key, JSON.stringify(record)),
      { key: `${storagePrefix}${savedC.id}`, record: savedC });
    assert.equal(uploadC.bytes.toString(), payloadA.slice(0, 4));
    assert.notEqual(uploadC.id, uploadA.id, 'Uploading the same video again gets an independent ID');
    await page.reload();
    await page.locator('#workspace-screen').waitFor({ state: 'visible' });
    const pausedC = page.locator(`.conversation[data-draft-id="${savedC.id}"]`);
    await pausedC.waitFor({ state: 'visible' });
    assert.match(await pausedC.textContent(), /Resume upload/);
    await pausedC.click();
    assert.equal(await page.locator('#instructions').inputValue(), 'Resume camera C');
    assert.equal(await page.locator('.entity-options input[value="Animals"]').isChecked(), true);

    // A paused transfer rejects a different file selection before any API write.
    const patchCountBeforeMismatch = state.patches.length;
    await selectFile(page, payloadA, { name: 'wrong-camera.mp4' });
    assert.match(await page.locator('#form-message').textContent(), /original|match|different/i);
    await page.locator('.analyze-button').click();
    assert.equal(state.patches.length, patchCountBeforeMismatch);
    assert.equal(state.uploads.size, 3);

    // New conversation must stay new even with a matching paused C in storage.
    const holdD = gate();
    heldGates.push(holdD);
    state.nextPlan = { gate: holdD };
    await newUpload(page, payloadD, 'Fresh camera D', ['Bicycles']);
    await until(() => holdD.entered, 'D starts independently of paused C');
    assert.equal(state.uploads.size, 4);
    const uploadD = [...state.uploads.values()].find(item => item.gate === holdD);
    assert.notEqual(uploadD.id, uploadC.id);
    assert.equal((await storedDrafts(page)).length, 2);

    // Explicitly resuming D in another tab must respect its existing Web Lock,
    // while a different new conversation in that tab remains independent.
    const savedD = (await storedDrafts(page)).find(item => item.uploadId === uploadD.id);
    const secondPage = await context.newPage();
    secondPage.on('pageerror', error => pageErrors.push(error.message));
    secondPage.setDefaultTimeout(8000);
    await secondPage.goto(baseURL);
    await secondPage.locator('#workspace-screen').waitFor({ state: 'visible' });
    assert.equal(await secondPage.evaluate(() => !!navigator.locks), true, 'This regression requires Web Locks support');
    await secondPage.locator(`.conversation[data-draft-id="${savedD.id}"]`).click();
    await selectFile(secondPage, payloadD);
    const patchesBeforeLockedResume = state.patches.length;
    await secondPage.locator('.analyze-button').click();
    await until(async () => /another tab/.test(await secondPage.locator('#form-message').textContent()), 'An active draft cannot resume in a second tab');
    assert.equal(state.patches.length, patchesBeforeLockedResume);
    assert.equal(await secondPage.locator('#upload-error-dialog').isVisible(), false, 'A lock conflict is not a genuine transfer failure');
    assert.deepEqual(state.deletes, [], 'A lock conflict must not delete another tab’s video');
    assert.ok((await storedDrafts(secondPage)).some(item => item.id === savedD.id && item.uploadId === uploadD.id));
    await newUpload(secondPage, payloadB, 'Independent camera E', ['Motorcycles']);
    await until(() => state.jobs.length === 3, 'A different draft in the second tab completes');
    const jobE = state.jobs.find(job => job.instructions === 'Independent camera E');
    assert.ok(jobE);
    assert.notEqual(jobE.upload_id, uploadD.id);
    assert.equal(state.uploads.get(jobE.upload_id).bytes.toString(), payloadB);

    await pausedC.click();
    await selectFile(page, payloadA);
    const patchesBeforeResume = state.patches.length;
    await page.locator('.analyze-button').click();
    await until(() => state.jobs.length === 4, 'Selected C resumes its own upload');
    const resumed = state.patches.slice(patchesBeforeResume).filter(item => item.id === uploadC.id);
    assert.deepEqual(resumed.map(item => item.offset), [4, 8]);
    assert.equal(uploadC.bytes.toString(), payloadA);
    const jobC = state.jobs.find(job => job.upload_id === uploadC.id);
    assert.equal(jobC.instructions, 'Resume camera C');
    assert.deepEqual(jobC.entities, ['Animals']);
    await until(async () => (await storedDrafts(page)).length === 1, 'Resuming C removes only C’s draft record');
    assert.equal((await storedDrafts(page))[0].uploadId, uploadD.id);
    holdD.release();
    await until(() => state.jobs.length === 5, 'D completes independently');
    assert.equal(uploadD.bytes.toString(), payloadD);
    assert.equal(state.jobs.find(job => job.upload_id === uploadD.id).instructions, 'Fresh camera D');
    assert.equal(new Set(state.jobs.map(job => job.id)).size, 5);
    assert.equal(new Set(state.jobs.map(job => job.upload_id)).size, 5);
    await until(async () => (await storedDrafts(page)).length === 0, 'All completed resume records clear');

    // Old filename-based storage is migrated to an explicitly selectable draft;
    // choosing that draft resumes its legacy upload without creating a new one.
    const legacyUpload = { id: randomUUID(), filename: fileInfo.name, size: payloadA.length,
      bytes: Buffer.from(payloadA.slice(0, 4)), complete: false };
    state.uploads.set(legacyUpload.id, legacyUpload);
    const legacyKey = `frameUpload:${user.id}:${fileInfo.name}:${payloadA.length}:${fileInfo.lastModified}`;
    await page.evaluate(({ key, id }) => localStorage.setItem(key, id), { key: legacyKey, id: legacyUpload.id });
    await page.reload();
    await page.locator('#workspace-screen').waitFor({ state: 'visible' });
    const legacyDraft = (await storedDrafts(page))[0];
    assert.equal(legacyDraft.uploadId, legacyUpload.id);
    assert.equal(await page.evaluate(key => localStorage.getItem(key), legacyKey), null);
    await page.locator(`.conversation[data-draft-id="${legacyDraft.id}"]`).click();
    await selectFile(page, payloadA);
    await page.locator('#instructions').fill('Resume legacy camera');
    const patchesBeforeLegacy = state.patches.length;
    await page.locator('.analyze-button').click();
    await until(() => state.jobs.length === 6, 'Legacy upload resumes explicitly');
    assert.equal(state.uploads.size, 6, 'Legacy resume must not allocate another upload');
    assert.deepEqual(state.patches.slice(patchesBeforeLegacy).map(item => item.offset), [4, 8]);
    assert.equal(legacyUpload.bytes.toString(), payloadA);
    await until(async () => (await storedDrafts(page)).length === 0, 'Legacy completion clears its migrated record');

    // A genuine transfer failure is discarded, explains its reason, and resets
    // only that conversation. An unrelated video must remain live and resumable.
    const holdF = gate();
    heldGates.push(holdF);
    state.nextPlan = { gate: holdF };
    await newUpload(page, payloadA, 'Keep camera F uploading', ['People']);
    await until(() => holdF.entered, 'F stays active while G fails');
    const uploadF = [...state.uploads.values()].find(item => item.gate === holdF);
    state.nextPlan = { failAt: 4 };
    await newUpload(page, payloadB, 'Retry camera G after failure', ['Motorcycles']);
    await page.locator('#upload-error-dialog').waitFor({ state: 'visible' });
    assert.match(await page.locator('#upload-error-file').textContent(), /camera\.mp4/);
    assert.match(await page.locator('#upload-error-reason').textContent(), /Simulated interrupted transfer/);
    await until(() => state.deletes.length === 1, 'Failed G requests deletion of its partial upload');
    const failedGId = state.deletes[0];
    assert.notEqual(failedGId, uploadF.id);
    assert.equal(state.uploads.has(failedGId), false);
    assert.equal(state.uploads.has(uploadF.id), true);
    assert.equal(uploadF.bytes.length, 0);
    assert.equal(await page.locator('#video-file').inputValue(), '');
    assert.equal(await page.locator('#file-row').isVisible(), false);
    assert.equal(await page.locator('#upload-progress-wrap').isVisible(), false);
    assert.equal(await page.locator('.analyze-button').isDisabled(), false);
    assert.equal(await page.locator('.conversation[data-draft-id]').count(), 1);
    const remainingDrafts = await storedDrafts(page);
    assert.equal(remainingDrafts.length, 1);
    assert.equal(remainingDrafts[0].uploadId, uploadF.id);
    assert.equal(state.jobs.length, 6, 'A failed transfer cannot create a saved session');

    // Retry asks for a fresh file selection and preserves the failed question.
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#retry-upload').click();
    const fileChooser = await fileChooserPromise;
    assert.equal(await page.locator('#upload-error-dialog').isVisible(), false);
    assert.equal(await page.locator('#instructions').inputValue(), 'Retry camera G after failure');
    assert.equal(await page.locator('.entity-options input[value="Motorcycles"]').isChecked(), true);
    await fileChooser.setFiles({ name: fileInfo.name, mimeType: 'video/mp4', buffer: Buffer.from(payloadB) });
    await page.locator('.analyze-button').click();
    await until(() => state.jobs.length === 7, 'Manual retry starts a fresh upload and completes');
    const retryJob = state.jobs.find(job => job.instructions === 'Retry camera G after failure');
    assert.ok(retryJob);
    assert.notEqual(retryJob.upload_id, failedGId);
    assert.notEqual(retryJob.upload_id, uploadF.id);
    assert.equal(state.uploads.get(retryJob.upload_id).bytes.toString(), payloadB);
    assert.deepEqual(retryJob.entities, ['Motorcycles']);
    await until(async () => (await storedDrafts(page)).length === 1, 'Retry completion leaves F’s resume record');
    assert.equal((await storedDrafts(page))[0].uploadId, uploadF.id);
    holdF.release();
    await until(() => state.jobs.length === 8, 'Unrelated F still completes successfully');
    assert.equal(uploadF.bytes.toString(), payloadA);
    await until(async () => (await storedDrafts(page)).length === 0, 'Successful uploads clear all active draft records');

    // Failed cleanup remains queued until connectivity returns. The discarded
    // video must never be restored as a resumable conversation in the meantime.
    await page.setViewportSize({ width: 390, height: 844 });
    state.nextPlan = { failAt: 0, deleteFailure: true,
      failureReason: `Simulated transfer failure: ${'unbrokenConnectionDetail'.repeat(14)}` };
    await newUpload(page, payloadA, 'Cleanup after connection returns');
    await page.locator('#upload-error-dialog').waitFor({ state: 'visible' });
    const offlineUpload = [...state.uploads.values()].find(item => item.deleteFailure);
    assert.ok(offlineUpload);
    const cleanupPrefix = `frameUploadCleanup:${user.id}:`;
    await until(() => page.evaluate(({ prefix, uploadId }) => Object.keys(localStorage)
      .some(key => key.startsWith(prefix) && JSON.parse(localStorage.getItem(key)).uploadId === uploadId),
    { prefix: cleanupPrefix, uploadId: offlineUpload.id }), 'Failed cleanup is retained in browser storage');
    assert.equal((await storedDrafts(page)).length, 0, 'Discarded failures are not resumable drafts');
    assert.equal(state.uploads.has(offlineUpload.id), true, 'Mock backend retains the file while DELETE fails');
    assert.ok((await page.locator('#upload-error-cleanup').textContent()).trim());
    const dialogBox = await page.locator('#upload-error-dialog').boundingBox();
    assert.ok(dialogBox && dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= 391,
      'The failure popup stays inside the mobile viewport');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await page.locator('#dismiss-upload-error').click();
    assert.equal(await page.locator('#upload-error-dialog').isVisible(), false);
    offlineUpload.deleteFailure = false;
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await until(() => !state.uploads.has(offlineUpload.id), 'Online retry deletes the abandoned upload');
    await until(() => page.evaluate(prefix => !Object.keys(localStorage).some(key => key.startsWith(prefix)), cleanupPrefix),
      'Successful cleanup removes its queued storage record');
    assert.ok(state.deletes.filter(id => id === offlineUpload.id).length >= 2);
    assert.equal(await page.locator('#upload-error-dialog').isVisible(), false,
      'Asynchronous cleanup must not reopen a dismissed failure popup');

    // A saved session survives a lost POST response. Recovery finds that job;
    // it must not discard the completed video or prompt for a replacement file.
    const deletesBeforeLostResponse = state.deletes.length;
    state.nextPlan = { loseJobResponse: true };
    await newUpload(page, payloadD, 'Recover a saved session after a lost response', ['Cars']);
    await until(() => state.jobs.length === 9, 'The backend saves the session before losing its response');
    const recoveredJob = state.jobs.find(job => job.instructions === 'Recover a saved session after a lost response');
    await until(() => page.locator(`.conversation[data-job-id="${recoveredJob.id}"].active`).count(),
      'The browser recovers and selects the existing saved session');
    assert.equal(state.deletes.length, deletesBeforeLostResponse, 'Recovery must not delete a saved conversation’s video');
    assert.equal(state.uploads.get(recoveredJob.upload_id).bytes.toString(), payloadD);
    assert.equal(await page.locator('.analyze-button').textContent(), 'Video uploaded');
    assert.equal(await page.locator('#upload-error-dialog').isVisible(), false);
    await until(async () => (await storedDrafts(page)).length === 0, 'Recovered session clears its draft record');
    assert.deepEqual(state.errors, [], 'All API traffic stays within the mocked contract');
    assert.deepEqual(pageErrors, [], 'No uncaught browser JavaScript errors');
    console.log('PASS: parallel isolation, exact bytes, prompts, interrupted-page resume, cross-tab locking, legacy migration, failure popup/reset/retry, reconnect cleanup, and lost-response recovery.');
  } finally {
    heldGates.forEach(item => item.release());
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});

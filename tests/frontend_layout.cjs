// Start the app on localhost:8000, then run: node tests/frontend_layout.cjs
// Optional: PLAYWRIGHT_MODULE, EDGE_EXECUTABLE, FRONTEND_TEST_URL.
// Every /api/** request is mocked. Only static assets are read from the server.
// Writes three screenshots containing sample data to docs/design/screenshots/.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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
const screenshotDir = path.resolve(__dirname, '../docs/design/screenshots');
const fixturePath = path.resolve(__dirname, '../frontend/assets/video-preview.jpg');
const fixtureImage = fs.readFileSync(fixturePath);
const user = { id: 'layout-test-user', username: `SampleUser_${'CameraOperator'.repeat(4)}`, csrf_token: 'layout-csrf' };
const longFilename = `ReceptionCamera_${'LongRecordingName'.repeat(14)}.mp4`;
const jobs = [0, 1].map((index) => {
  const id = randomUUID();
  return { id, session_id: id, upload_id: randomUUID(), filename: `${index + 1}_${longFilename}`,
    status: 'ready', entities: ['People', 'Cars'], instructions: 'Sample preparation instructions', metadata: {}, error: null };
});
const viewports = [
  { width: 1280, height: 900 },
  { width: 1280, height: 400 },
  { width: 320, height: 700 }, { width: 360, height: 800 }, { width: 390, height: 844 },
  { width: 700, height: 900 },
  { width: 768, height: 1024 }, { width: 900, height: 1000 },
  { width: 844, height: 390 }
];

async function until(predicate, label, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out: ${label}`);
}

async function noHorizontalOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  if (dimensions.document > dimensions.viewport + 1 || dimensions.body > dimensions.viewport + 1) {
    console.error(await page.evaluate(() => [...document.querySelectorAll('body *')].map(node => {
      const box = node.getBoundingClientRect();
      return { tag: node.tagName, id: node.id, class: node.className, left: box.left, right: box.right, width: box.width };
    }).filter(box => box.width && (box.left < -1 || box.right > document.documentElement.clientWidth + 1)).slice(0, 15)));
  }
  assert.ok(dimensions.document <= dimensions.viewport + 1 && dimensions.body <= dimensions.viewport + 1,
    `${label}: horizontal overflow ${JSON.stringify(dimensions)}`);
}

async function reachable(page, selector, label) {
  const control = page.locator(selector);
  await control.scrollIntoViewIfNeeded();
  const box = await control.boundingBox();
  const viewport = page.viewportSize();
  assert.ok(box && box.width > 0 && box.height > 0, `${label}: ${selector} is visible`);
  assert.ok(box.x >= -1 && box.x + box.width <= viewport.width + 1,
    `${label}: ${selector} extends outside viewport: ${JSON.stringify(box)}`);
  assert.ok(box.y + box.height > 0 && box.y < viewport.height, `${label}: ${selector} can be scrolled into view`);
  await control.click({ trial: true });
}

async function openNavigation(page) {
  if (page.viewportSize().width <= 900 && await page.locator('#toggle-navigation').getAttribute('aria-expanded') !== 'true') {
    await page.locator('#toggle-navigation').click();
  }
}

async function selectVideo(page, filename = longFilename) {
  await page.locator('#video-file').evaluate((input, name) => {
    const files = new DataTransfer();
    files.items.add(new File(['abcdefghijkl'], name, { type: 'video/mp4', lastModified: 1700000000000 }));
    input.files = files.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, filename);
}

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.EDGE_EXECUTABLE ? { executablePath: process.env.EDGE_EXECUTABLE } : { channel: 'msedge' }) });
  let releaseUpload;
  const heldUpload = new Promise(resolve => { releaseUpload = resolve; });
  const state = { authenticated: false, jobs: [], apiErrors: [], upload: null, held: false };
  try {
    const context = await browser.newContext({ viewport: viewports[0], serviceWorkers: 'block' });
    await context.addInitScript(() => {
      Object.defineProperty(window, 'Notification', { configurable: true, value: class {
        static permission = 'denied';
        static requestPermission() { return Promise.resolve('denied'); }
      } });
    });
    await context.route('**/api/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      const method = request.method();
      const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      try {
        if (pathname === '/api/me') return respond(state.authenticated ? user : { detail: 'Sign in required' }, state.authenticated ? 200 : 401);
        if (method === 'GET' && pathname === '/api/jobs') return respond({ jobs: state.jobs });
        if (method === 'GET' && pathname.includes('/images/')) return route.fulfill({ contentType: 'image/jpeg', body: fixtureImage });
        const session = pathname.match(/^\/api\/jobs\/([^/]+)(\/messages)?$/);
        if (method === 'GET' && session) {
          const job = state.jobs.find(item => item.id === session[1]);
          assert.ok(job, `Known mocked session: ${pathname}`);
          if (!session[2]) return respond(job);
          return respond({ messages: [
            { id: 1, role: 'user', content: `Sample question about this recording. ${'unbrokenCameraReference'.repeat(22)}`,
              images: [{ id: 'sample-image', filename: `${'reference'.repeat(20)}.jpg`, url: `/api/sessions/${job.id}/images/sample-image` }] },
            { id: 2, role: 'assistant', content: 'Sample backend response: this workspace stores your video and the images attached to each question.', images: [] }
          ] });
        }
        if (method === 'POST' && pathname === '/api/uploads') {
          const body = request.postDataJSON();
          state.upload = { id: randomUUID(), filename: body.filename, size: body.size, offset: 0, chunk_size: 4, status: 'uploading' };
          return respond(state.upload, 201);
        }
        if (state.upload && pathname === `/api/uploads/${state.upload.id}` && method === 'PATCH') {
          const offset = Number(request.headers()['upload-offset']);
          assert.equal(offset, state.upload.offset);
          if (offset === 4) { state.held = true; await heldUpload; }
          state.upload.offset += request.postDataBuffer().length;
          return respond(state.upload);
        }
        if (state.upload && pathname === `/api/uploads/${state.upload.id}` && method === 'GET') return respond(state.upload);
        if (state.upload && pathname === `/api/uploads/${state.upload.id}/complete` && method === 'POST') {
          assert.equal(state.upload.offset, state.upload.size);
          state.upload.status = 'complete';
          return respond(state.upload);
        }
        if (method === 'POST' && pathname === '/api/jobs') {
          const body = request.postDataJSON();
          const id = randomUUID();
          const job = { ...body, id, session_id: id, filename: state.upload.filename, status: 'ready', metadata: {} };
          state.jobs.push(job);
          return respond(job, 201);
        }
        throw new Error(`Unexpected API request: ${method} ${pathname}`);
      } catch (error) {
        state.apiErrors.push(error.message);
        await respond({ detail: error.message }, 500).catch(() => {});
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const pageErrors = [];
    let navigations = 0;
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
    fs.mkdirSync(screenshotDir, { recursive: true });

    for (const viewport of viewports) {
      const label = `${viewport.width}x${viewport.height}`;
      await page.setViewportSize(viewport);
      state.authenticated = false;
      state.jobs = [];
      await page.goto(baseURL);
      await page.locator('#login-screen').waitFor({ state: 'visible' });
      if (viewport.width === 320) await page.screenshot({ path: path.join(screenshotDir, 'responsive-login-mobile.png'), fullPage: true });
      await noHorizontalOverflow(page, `${label} login`);
      for (const selector of ['#username', '#password', '#toggle-password', '.sign-in']) await reachable(page, selector, label);
      if (viewport.width === 390) {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: path.join(screenshotDir, 'responsive-login-mobile.png'), fullPage: true });
      }

      state.authenticated = true;
      await page.reload();
      await page.locator('#workspace-screen').waitFor({ state: 'visible' });
      await noHorizontalOverflow(page, `${label} empty workspace`);
      await reachable(page, '#new-conversation', label);
      assert.equal(await page.locator('#toggle-navigation').isVisible(), viewport.width <= 900);
      assert.equal(await page.locator('#workspace-navigation').isVisible(), viewport.width > 900);
      await selectVideo(page);
      await page.locator('#instructions').fill('Keep this sample video prompt visible and editable on every viewport.');
      await noHorizontalOverflow(page, `${label} selected video`);
      for (const selector of ['.add-video', '#remove-file', '#instructions', '.analyze-button']) await reachable(page, selector, label);

      state.jobs = [...jobs];
      await page.reload();
      await page.locator('#chat-send').waitFor({ state: 'visible' });
      await until(() => page.locator('.message-attachments img').count(), 'Sample message image renders');
      await noHorizontalOverflow(page, `${label} populated conversation`);
      for (const selector of ['#open-chat', '.chat-attach', '#chat-input', '#chat-send']) await reachable(page, selector, label);

      await openNavigation(page);
      await noHorizontalOverflow(page, `${label} long conversation names and account`);
      await reachable(page, '#sign-out', label);
      if (viewport.width <= 900) {
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#workspace-navigation').isVisible(), false);
        assert.equal(await page.evaluate(() => document.activeElement.id), 'toggle-navigation');
        await openNavigation(page);
      }
      await page.locator(`.conversation[data-job-id="${jobs[1].id}"]`).click();
      if (viewport.width <= 900) {
        assert.equal(await page.locator('#workspace-navigation').isVisible(), false, 'Selecting a session closes mobile navigation');
        await until(() => page.evaluate(() => document.activeElement.id === 'intro-title'), 'Focus returns to selected session content');
      }

      await openNavigation(page);
      await page.locator('.conversation-options').first().click();
      await reachable(page, '#delete-conversation', label);
      await page.locator('#delete-conversation').click();
      await noHorizontalOverflow(page, `${label} delete dialog`);
      for (const selector of ['#confirm-delete', '#cancel-delete']) await reachable(page, selector, label);
      await page.locator('#cancel-delete').click();
      if (viewport.width <= 900) await page.keyboard.press('Escape');

      if (viewport.width === 390 || (viewport.width === 1280 && viewport.height === 900)) {
        await page.locator('#toast').waitFor({ state: 'hidden', timeout: 9000 });
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: path.join(screenshotDir,
          viewport.width === 390 ? 'responsive-workspace-mobile.png' : 'responsive-workspace-desktop.png'), fullPage: true });
      }
      await openNavigation(page);
      await page.locator('#new-conversation').click();
      if (viewport.width <= 900) assert.equal(await page.locator('#workspace-navigation').isVisible(), false,
        'New conversation closes mobile navigation');
      console.log(`PASS layout: ${label} login, video form, conversation, navigation, and delete dialog`);
    }

    // Resize a live conversation: no navigation, lost text, or dropped image draft.
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.reload();
    await page.locator('#chat-send').waitFor({ state: 'visible' });
    await page.locator('#chat-input').fill('Preserve this unsent question through resizing.');
    await page.locator('#image-file').setInputFiles(fixturePath);
    const selectedSession = await page.locator('.conversation.active').getAttribute('data-job-id');
    const beforeResize = navigations;
    for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }, { width: 1280, height: 900 }]) {
      await page.setViewportSize(viewport);
      await noHorizontalOverflow(page, 'Live conversation resize');
      assert.equal(await page.locator('.conversation.active').getAttribute('data-job-id'), selectedSession);
      assert.equal(await page.locator('#chat-input').inputValue(), 'Preserve this unsent question through resizing.');
      assert.equal(await page.locator('.pending-image-card').count(), 1);
    }
    assert.equal(navigations, beforeResize, 'Responsive layout changes must not refresh the page');

    // A live transfer retains its progress, selected file, and prompt on resize.
    await page.locator('#new-conversation').click();
    await selectVideo(page);
    await page.locator('#instructions').fill('Preserve the uploading conversation prompt.');
    await page.locator('.analyze-button').click();
    await until(() => state.held, 'Upload pauses after one acknowledged chunk');
    const draftId = await page.locator('.conversation.active').getAttribute('data-draft-id');
    const beforeUploadResize = navigations;
    for (const viewport of [{ width: 320, height: 700 }, { width: 768, height: 1024 }, { width: 1280, height: 900 }]) {
      await page.setViewportSize(viewport);
      await noHorizontalOverflow(page, 'Live upload resize');
      if (viewport.width <= 900) {
        await openNavigation(page);
        await page.locator(`.conversation[data-draft-id="${draftId}"]`).click();
        assert.equal(await page.locator('#workspace-navigation').isVisible(), false,
          'Selecting an uploading draft closes mobile navigation');
      }
      assert.equal(await page.locator('.conversation.active').getAttribute('data-draft-id'), draftId);
      assert.equal(await page.locator('#upload-progress').evaluate(progress => progress.value), 33);
      assert.equal(await page.locator('#instructions').inputValue(), 'Preserve the uploading conversation prompt.');
      assert.equal(await page.locator('#file-name').textContent(), longFilename);
    }
    assert.equal(navigations, beforeUploadResize);
    releaseUpload();
    await until(() => state.upload.status === 'complete', 'Transfer still completes after resizing');
    assert.deepEqual(state.apiErrors, []);
    assert.deepEqual(pageErrors, []);
    console.log('PASS: live resize preserves session, unsent question, attachments, and upload progress without refresh. Three sample screenshots saved.');
  } finally {
    releaseUpload();
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});

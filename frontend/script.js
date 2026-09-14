const loginScreen = document.querySelector('#login-screen');
const workspaceScreen = document.querySelector('#workspace-screen');
const loginForm = document.querySelector('#login-form');
const fileInput = document.querySelector('#video-file');
const fileRow = document.querySelector('#file-row');
const formMessage = document.querySelector('#form-message');
const result = document.querySelector('#analysis-result');
let currentFile = null;
const uploadDrafts = new Map();
let activeDraftId = null;
let activeJobId = null;
let jobTimer = null;
let messageTimer = null;
let renderedMessageIds = new Set();
const trackedJobs = new Map();
const jobRequests = new Map();
const notifiedEvents = new Set();
let jobStatusLoading = false;
let workspaceGeneration = 0;
const uploadFailures = [];
let uploadFailureClosing = false;
const pendingUploadCleanup = new Map();
let cleanupTimer = null;
let toastTimer = null;
let currentUser = null;
let csrfToken = null;
const chatPanel = document.querySelector('#chat-panel');
const chatMessages = document.querySelector('#chat-messages');
const chatInput = document.querySelector('#chat-input');
const chatSend = document.querySelector('#chat-send');
const toast = document.querySelector('#toast');
const analysisForm = document.querySelector('#analysis-form');
const analyzeButton = document.querySelector('.analyze-button');
const openChatButton = document.querySelector('#open-chat');
const imageInput = document.querySelector('#image-file');
const imageList = document.querySelector('#image-list');
const imageMessage = document.querySelector('#image-message');
let pendingImages = [];
const progressWrap = document.querySelector('#upload-progress-wrap');
const progressBar = document.querySelector('#upload-progress');
const progressLabel = document.querySelector('#upload-progress-label');
const signInButton = loginForm.querySelector('button[type="submit"]');
const MAX_IMAGES_PER_MESSAGE = 10;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const POLL_INTERVAL_MS = 3000;
const conversationMenu = document.querySelector('#conversation-menu');
const deleteDialog = document.querySelector('#delete-dialog');
const deleteError = document.querySelector('#delete-dialog-error');
const confirmDelete = document.querySelector('#confirm-delete');
const cancelDelete = document.querySelector('#cancel-delete');
let menuTarget = null;
let deleteTarget = null;
let deleteInProgress = false;
const navigationToggle = document.querySelector('#toggle-navigation');
const workspaceNavigation = document.querySelector('#workspace-navigation');
const mobileLayout = window.matchMedia('(max-width: 900px)');
const uploadErrorDialog = document.querySelector('#upload-error-dialog');

function setNavigationOpen(open) {
  document.querySelector('.sidebar').classList.toggle('navigation-open', open);
  navigationToggle.setAttribute('aria-expanded', String(open));
  navigationToggle.setAttribute('aria-label', open ? 'Hide conversations' : 'Show conversations');
  if (!open) closeConversationMenu();
}

function closeMobileNavigation() {
  if (!mobileLayout.matches) return;
  const focusWasInside = workspaceNavigation.contains(document.activeElement);
  setNavigationOpen(false);
  if (focusWasInside) requestAnimationFrame(() => document.querySelector('#intro-title').focus({ preventScroll: true }));
}

navigationToggle.addEventListener('click', () => {
  setNavigationOpen(navigationToggle.getAttribute('aria-expanded') !== 'true');
});
mobileLayout.addEventListener('change', () => {
  const focusWasInside = workspaceNavigation.contains(document.activeElement);
  setNavigationOpen(false);
  if (mobileLayout.matches && focusWasInside) navigationToggle.focus();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && mobileLayout.matches && navigationToggle.getAttribute('aria-expanded') === 'true'
    && !deleteDialog.open && conversationMenu.hidden) {
    setNavigationOpen(false);
    navigationToggle.focus();
  }
});

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (csrfToken && options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function draftStorageKey(draft) {
  return `frameUploadDraft:${draft.userId}:${draft.id}`;
}

function newDraftId() {
  const id = crypto.randomUUID?.()
    || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  return `draft-${id}`;
}

function fileDetails(file) {
  return { name: file.name, size: file.size, lastModified: file.lastModified };
}

function matchesDraftFile(draft, file) {
  const info = draft.fileInfo;
  return info.name === file.name && info.size === file.size
    && (info.lastModified === null || info.lastModified === file.lastModified);
}

function persistDraft(draft) {
  const { id, userId, uploadId, fileInfo, entities, instructions, progress, createdAt } = draft;
  try {
    localStorage.setItem(draftStorageKey(draft), JSON.stringify({ id, userId, uploadId, fileInfo, entities, instructions, progress, createdAt }));
    return true;
  } catch {
    if (!draft.storageWarning) notifyUser('Browser storage is unavailable. Keep this tab open until your upload finishes.');
    draft.storageWarning = true;
    return false;
  }
}

function forgetDraft(draft) {
  try { localStorage.removeItem(draftStorageKey(draft)); }
  catch { notifyUser('Could not remove the saved upload entry from this browser. It may appear again after a refresh.'); }
}

function restoreUploadDrafts(jobs) {
  const prefix = `frameUploadDraft:${currentUser.id}:`;
  const legacyPrefix = `frameUpload:${currentUser.id}:`;
  let keys;
  try { keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)); }
  catch { return; }
  for (const key of keys) {
    if (!key?.startsWith(prefix) && !key?.startsWith(legacyPrefix)) continue;
    try {
      let saved;
      if (key.startsWith(prefix)) {
        saved = JSON.parse(localStorage.getItem(key));
        if (!saved || saved.userId !== currentUser.id || typeof saved.id !== 'string'
          || !saved.fileInfo || typeof saved.fileInfo.name !== 'string' || !(saved.fileInfo.size > 0)
          || !Array.isArray(saved.entities) || typeof saved.instructions !== 'string') continue;
        if (draftStorageKey(saved) !== key) continue;
      } else {
        // Convert old filename-based entries into explicitly selectable paused drafts.
        const parts = key.slice(legacyPrefix.length).split(':');
        const lastModified = Number(parts.pop());
        const size = Number(parts.pop());
        const name = parts.join(':');
        const uploadId = localStorage.getItem(key);
        if (!name || !(size > 0) || !Number.isFinite(lastModified) || !/^[a-f0-9-]{36}$/i.test(uploadId || '')) continue;
        saved = { id: `draft-${uploadId}`, userId: currentUser.id, uploadId,
          fileInfo: { name, size, lastModified }, entities: ['People', 'Cars'], instructions: '', progress: 0, createdAt: Date.now() };
      }
      if (jobs.some(job => job.upload_id === saved.uploadId)
        || pendingUploadCleanup.has(`frameUploadCleanup:${currentUser.id}:${saved.id}`)) {
        localStorage.removeItem(key);
        continue;
      }
      if (uploadDrafts.has(saved.id)) continue;
      const draft = createDraft(null, saved.entities, saved.instructions, saved, false);
      if (key.startsWith(legacyPrefix) && persistDraft(draft)) localStorage.removeItem(key);
    } catch {
      // An invalid saved entry must not prevent the user's workspace from opening.
    }
  }
}

function showProgress(offset, size) {
  const percent = Math.round(offset / size * 100);
  progressWrap.hidden = false;
  progressBar.value = percent;
  progressLabel.textContent = `${percent}% uploaded`;
}

async function uploadVideo(draft, onProgress) {
  const file = draft.file;
  let upload;
  if (draft.uploadId) {
    try {
      upload = await api(`/api/uploads/${draft.uploadId}`);
      if (upload.size !== file.size) throw new Error('Upload size changed');
    } catch (error) {
      if (error.status !== 404) throw error;
      draft.uploadId = null;
      persistDraft(draft);
    }
  }
  if (!upload) {
    upload = await api('/api/uploads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, size: file.size })
    });
    draft.uploadId = upload.id;
    persistDraft(draft);
  }
  let offset = upload.offset;
  onProgress(offset, file.size);
  while (offset < file.size) {
    const chunk = file.slice(offset, offset + upload.chunk_size);
    let sent = false;
    for (let attempt = 0; attempt < 3 && !sent; attempt++) {
      try {
        const updated = await api(`/api/uploads/${upload.id}`, {
          method: 'PATCH', headers: { 'Upload-Offset': String(offset), 'Content-Type': 'application/octet-stream' },
          body: chunk
        });
        offset = updated.offset;
        sent = true;
      } catch (error) {
        const status = await api(`/api/uploads/${upload.id}`);
        if (status.offset > offset) { offset = status.offset; sent = true; }
        else if (attempt === 2) throw error;
      }
    }
    onProgress(offset, file.size);
  }
  if (upload.status !== 'complete') await api(`/api/uploads/${upload.id}/complete`, { method: 'POST' });
  return { id: upload.id };
}

function renderDraft(draft) {
  const started = new Date(draft.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  draft.button.querySelector('small').textContent = draft.status === 'paused'
    ? `Resume upload · ${started}` : draft.status === 'failed'
      ? 'Upload failed · select to retry' : `Uploading ${draft.progress}%`;
  draft.button.classList.toggle('active', activeDraftId === draft.id);
}

function createDraft(file, entities, instructions, saved = null, activate = true) {
  const draft = {
    id: newDraftId(), userId: currentUser.id, uploadId: null,
    fileInfo: file ? fileDetails(file) : null, entities, instructions,
    progress: 0, createdAt: Date.now(), ...saved, file,
    status: file ? 'uploading' : 'paused', error: ''
  };
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `conversation${activate ? ' active' : ''}`;
  button.dataset.draftId = draft.id;
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg><span class="conversation-copy"><strong></strong><small></small></span>';
  button.querySelector('strong').textContent = draft.fileInfo.name.replace(/\.[^.]+$/, '');
  button.title = `${draft.fileInfo.name} · ${new Date(draft.createdAt).toLocaleString()}`;
  button.addEventListener('click', () => showDraft(draft.id));
  draft.button = button;
  uploadDrafts.set(draft.id, draft);
  if (activate) {
    activeDraftId = draft.id;
    document.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  }
  document.querySelector('#conversation-list').prepend(button);
  renderDraft(draft);
  if (!saved) persistDraft(draft);
  return draft;
}

function showDraft(draftId) {
  const draft = uploadDrafts.get(draftId);
  if (!draft) return;
  clearVideo();
  activeDraftId = draftId;
  currentFile = draft.file;
  document.querySelector('#file-name').textContent = draft.fileInfo.name;
  document.querySelector('#file-meta').textContent = `${(draft.fileInfo.size / 1024 / 1024).toFixed(1)} MB · Video`;
  fileRow.hidden = false;
  document.querySelector('#instructions').value = draft.instructions;
  document.querySelectorAll('.entity-options input').forEach(input => {
    input.checked = draft.entities.includes(input.value);
  });
  showProgress(draft.progress, 100);
  formMessage.textContent = draft.error || (draft.file ? '' : 'Select the original video file below, then resume this upload.');
  const busy = draft.status === 'uploading';
  analyzeButton.disabled = busy;
  analyzeButton.textContent = busy ? 'Uploading…' : 'Resume upload ↑';
  fileInput.disabled = busy;
  document.querySelector('#remove-file').disabled = busy;
  document.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  renderDraft(draft);
}

async function runUpload(draft) {
  try {
    if (navigator.locks) {
      await navigator.locks.request(`frame-upload:${draft.userId}:${draft.id}`, { ifAvailable: true }, async lock => {
        if (!lock) {
          draft.status = 'paused';
          draft.error = 'This conversation is uploading in another tab. Return to that tab, or start a New conversation for another video.';
          renderDraft(draft);
          if (activeDraftId === draft.id) showDraft(draft.id);
          return;
        }
        await transferDraft(draft);
      });
    } else {
      await transferDraft(draft);
    }
  } catch (error) {
    if (draft.status === 'complete') {
      notifyUser('Your video was saved. Refresh the workspace to open its conversation.');
      return;
    }
    handleUploadFailure(draft, error);
  }
}

async function transferDraft(draft) {
  const { file, entities, instructions } = draft;
  // Another tab may have advanced this draft since this page restored it.
  try {
    const saved = JSON.parse(localStorage.getItem(draftStorageKey(draft)));
    if (saved?.uploadId) draft.uploadId = saved.uploadId;
  } catch { /* Browser storage is optional while the tab remains open. */ }
  persistDraft(draft);
  const upload = await uploadVideo(draft, (offset, size) => {
    draft.progress = Math.round(offset / size * 100);
    persistDraft(draft);
    renderDraft(draft);
    if (activeDraftId === draft.id) showProgress(offset, size);
  });
  let job;
  try {
    job = await api('/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_id: upload.id, entities, instructions })
    });
  } catch (error) {
    // The session may have been saved even if its response was lost.
    try { job = (await api('/api/jobs')).jobs.find(item => item.upload_id === upload.id); }
    catch { /* Discard refuses to remove any upload already linked to a session. */ }
    if (!job) throw error;
  }
  draft.status = 'complete';
  forgetDraft(draft);
  const selected = activeDraftId === draft.id;
  uploadDrafts.delete(draft.id);
  draft.button.remove();
  addConversation(file.name.replace(/\.[^.]+$/, ''), job.id, selected);
  trackedJobs.set(job.id, { id: job.id, filename: file.name, status: 'preprocessing' });
  notifyJobOnce(job.id, 'uploaded', `${file.name} uploaded. Background preprocessing has started.`);
  updateJob({ ...job, filename: file.name });
  if (selected) openJob(job.id);
}

function uploadFailureReason(error) {
  if (error.status === 401) return 'Your sign-in expired. Sign in again, then choose the video to retry.';
  if (error.status === 413) return 'The file or upload chunk exceeds the server limit.';
  if (error instanceof TypeError || error.name === 'AbortError') return 'The connection to the server was interrupted. Check your network and that the server is running.';
  if (error.status >= 500 && /^Request failed/.test(error.message)) return `The server could not save this upload (HTTP ${error.status}). Check available disk space and the server logs before retrying.`;
  return error.message || 'The upload could not be completed. Please choose the video again.';
}

function renderUploadFailure() {
  const failure = uploadFailures[0];
  if (!failure || uploadFailureClosing) return;
  document.querySelector('#upload-error-file').textContent = failure.filename;
  document.querySelector('#upload-error-reason').textContent = failure.reason;
  document.querySelector('#upload-error-cleanup').textContent = failure.cleanup;
  if (!uploadErrorDialog.open) uploadErrorDialog.showModal();
}

function handleUploadFailure(draft, error) {
  draft.status = 'failed';
  const failure = { filename: draft.fileInfo.name, entities: [...draft.entities], instructions: draft.instructions,
    reason: uploadFailureReason(error), cleanup: draft.uploadId
      ? 'Clearing the incomplete upload from the server…'
      : 'The selected video has been cleared. Choose it again to retry.' };
  uploadFailures.push(failure);
  if (draft.uploadId) queueUploadCleanup(draft, failure);
  forgetDraft(draft);
  uploadDrafts.delete(draft.id);
  draft.button.remove();
  if (activeDraftId === draft.id) clearVideo();
  draft.file = null;
  renderUploadFailure();
}

function queueUploadCleanup(draft, failure) {
  const key = `frameUploadCleanup:${draft.userId}:${draft.id}`;
  const record = { key, userId: draft.userId, uploadId: draft.uploadId };
  try { localStorage.setItem(key, JSON.stringify(record)); } catch { /* Retry while this tab is open. */ }
  pendingUploadCleanup.set(key, { ...record, failure, inFlight: false });
  retryUploadCleanup();
}

function restoreUploadCleanup() {
  pendingUploadCleanup.clear();
  clearInterval(cleanupTimer);
  cleanupTimer = null;
  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(`frameUploadCleanup:${currentUser.id}:`)) continue;
      try {
        const record = JSON.parse(localStorage.getItem(key));
        if (record?.key === key && record.userId === currentUser.id && typeof record.uploadId === 'string') {
          pendingUploadCleanup.set(key, { ...record, inFlight: false });
        }
      } catch { /* Ignore invalid saved cleanup entries. */ }
    }
  } catch { /* Browser storage may be unavailable. */ }
  retryUploadCleanup();
}

function retryUploadCleanup() {
  if (!currentUser) return;
  if (pendingUploadCleanup.size && !cleanupTimer) cleanupTimer = setInterval(retryUploadCleanup, 15000);
  for (const record of pendingUploadCleanup.values()) {
    if (record.userId !== currentUser.id || record.inFlight) continue;
    record.inFlight = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    api(`/api/uploads/${record.uploadId}`, { method: 'DELETE', signal: controller.signal }).then(response => {
      finishUploadCleanup(record, response.cleanup_pending
        ? 'The failed upload was removed, but some server files need administrator cleanup.'
        : 'The incomplete upload was removed. Choose the video again to retry.');
    }).catch(error => {
      if (error.status === 404) finishUploadCleanup(record, 'The failed upload is no longer on the server. Choose the video again to retry.');
      else if (error.status === 409) finishUploadCleanup(record, 'The video already has a saved conversation. Refresh the workspace to open it.');
      else if (record.failure) {
        record.failure.cleanup = 'The selection was cleared. Server cleanup is pending and will retry when the connection and sign-in are available.';
        if (uploadFailures[0] === record.failure) renderUploadFailure();
      }
    }).finally(() => {
      clearTimeout(timeout);
      record.inFlight = false;
    });
  }
}

function finishUploadCleanup(record, message) {
  pendingUploadCleanup.delete(record.key);
  try { localStorage.removeItem(record.key); } catch { /* A later 404 is safe to repeat. */ }
  if (record.failure) {
    record.failure.cleanup = message;
    if (uploadFailures[0] === record.failure) renderUploadFailure();
  }
  if (!pendingUploadCleanup.size) { clearInterval(cleanupTimer); cleanupTimer = null; }
}

function closeUploadFailure() {
  uploadFailures.shift();
  uploadFailureClosing = true;
  uploadErrorDialog.close();
}

document.querySelector('#dismiss-upload-error').addEventListener('click', closeUploadFailure);
uploadErrorDialog.addEventListener('cancel', event => {
  event.preventDefault();
  closeUploadFailure();
});
document.querySelector('#retry-upload').addEventListener('click', () => {
  const failure = uploadFailures[0];
  closeUploadFailure();
  if (!failure) return;
  startNewConversation();
  document.querySelector('#instructions').value = failure.instructions;
  document.querySelectorAll('.entity-options input').forEach(input => { input.checked = failure.entities.includes(input.value); });
  fileInput.click();
});
uploadErrorDialog.addEventListener('close', () => {
  uploadFailureClosing = false;
  if (uploadFailures.length) renderUploadFailure();
});
window.addEventListener('online', retryUploadCleanup);

async function showWorkspace(user) {
  const response = await api('/api/jobs');
  currentUser = user;
  csrfToken = user.csrf_token;
  workspaceGeneration++;
  trackedJobs.clear();
  jobRequests.clear();
  clearVideo();
  uploadDrafts.clear();
  activeDraftId = null;
  document.querySelector('#account-name').textContent = user.username;
  document.querySelector('#avatar').textContent = user.username.charAt(0).toUpperCase();
  const list = document.querySelector('#conversation-list');
  list.replaceChildren();
  [...response.jobs].reverse().forEach(job => {
    trackedJobs.set(job.id, job);
    addConversation(job.filename.replace(/\.[^.]+$/, ''), job.id);
    renderJobStatus(job);
  });
  restoreUploadCleanup();
  restoreUploadDrafts(response.jobs);
  let remembered;
  try { remembered = localStorage.getItem(`frameLastJob:${user.id}`); } catch { /* Optional preference. */ }
  const selected = response.jobs.find(job => job.id === remembered) || response.jobs[0];
  loginScreen.hidden = true;
  workspaceScreen.hidden = false;
  if (selected) openJob(selected.id);
  ensureJobPolling();
}

function notifyUser(message, browserNotification = false, tag) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 6500);
  try {
    if (browserNotification && 'Notification' in window && Notification.permission === 'granted') {
      new Notification('VideoLens', { body: message, tag, renotify: false });
    }
  } catch { /* Browser alerts are optional; saved uploads and UI state remain valid. */ }
}

function notifyJobOnce(jobId, event, message) {
  if (!currentUser) return;
  const generation = workspaceGeneration;
  const key = `frameNotification:${currentUser.id}:${jobId}:${event}`;
  if (notifiedEvents.has(key)) return;
  notifiedEvents.add(key);
  const deliver = () => {
    if (generation !== workspaceGeneration) return;
    try {
      if (localStorage.getItem(key)) return;
      localStorage.setItem(key, '1');
    } catch { /* In-memory deduplication still works when storage is unavailable. */ }
    notifyUser(message, true, key);
  };
  if (navigator.locks) navigator.locks.request(key, deliver).catch(() => {});
  else deliver();
}

function isPreparing(status) {
  return status === 'queued' || status === 'preprocessing';
}

function renderJobStatus(job) {
  const button = [...document.querySelectorAll('.conversation[data-job-id]')].find(item => item.dataset.jobId === job.id);
  if (button) button.querySelector('small').textContent = job.status === 'ready' ? 'Ready to chat'
    : job.status === 'failed' ? 'Preparation failed' : 'Preparing video';
}

function updateJob(job) {
  const previous = trackedJobs.get(job.id);
  if (!previous) return;
  // A delayed preparing response must never undo a terminal status.
  if (['ready', 'failed'].includes(previous.status) && job.status !== previous.status) return;
  const current = { ...previous, ...job };
  trackedJobs.set(job.id, current);
  if (job.id === activeJobId) jobStatusLoading = false;
  renderJobStatus(current);
  ensureJobPolling();
  if (job.id === activeJobId) displayJob(current);
  if (isPreparing(previous.status) && current.status === 'ready') {
    notifyJobOnce(job.id, 'ready', `${current.filename || 'Your video'} is ready. Continue the conversation.`);
  } else if (isPreparing(previous.status) && current.status === 'failed') {
    notifyJobOnce(job.id, 'preprocessing-failed', `Could not prepare ${current.filename || 'your video'}: ${current.error || 'Unknown error'}`);
  }
}

function ensureJobPolling() {
  const needed = (activeJobId && jobStatusLoading) || [...trackedJobs.values()].some(job => isPreparing(job.status));
  if (needed && !jobTimer) {
    jobTimer = setInterval(() => {
      const ids = new Set([...trackedJobs.values()].filter(job => isPreparing(job.status)).map(job => job.id));
      if (activeJobId && jobStatusLoading) ids.add(activeJobId);
      ids.forEach(id => refreshJob(id));
    }, POLL_INTERVAL_MS);
  } else if (!needed) {
    clearInterval(jobTimer);
    jobTimer = null;
  }
}

function addBubble(role, content, images = []) {
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  if (content) {
    const text = document.createElement('div');
    text.textContent = content;
    bubble.append(text);
  }
  if (images.length) {
    const attachments = document.createElement('div');
    attachments.className = 'message-attachments';
    images.forEach(item => {
      const picture = document.createElement('img');
      picture.src = item.url;
      picture.alt = item.filename;
      picture.loading = 'lazy';
      attachments.append(picture);
    });
    bubble.append(attachments);
  }
  chatMessages.append(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function loadMessages(jobId) {
  const history = await api(`/api/jobs/${jobId}/messages`);
  if (activeJobId !== jobId) return;
  history.messages.forEach(message => {
    if (renderedMessageIds.has(message.id)) return;
    renderedMessageIds.add(message.id);
    addBubble(message.role, message.content, message.images);
  });
}

function displayJob(job) {
  if (job.id !== activeJobId) return;
  const isReady = job.status === 'ready';
  const isFailed = job.status === 'failed';
  chatPanel.hidden = false;
  document.querySelector('#job-state').textContent = isReady ? 'Ready' : isFailed ? 'Failed' : 'Preparing';
  document.querySelector('#chat-subtitle').textContent = isReady
    ? 'Preprocessing complete. Continue the conversation.'
    : isFailed ? `Preprocessing failed: ${job.error || 'Unknown error'}`
      : 'Background initial preprocessing is in progress. We will notify you when it finishes.';
  chatInput.disabled = !isReady;
  chatSend.disabled = !isReady;
  openChatButton.disabled = !isReady;
  result.hidden = false;
  result.replaceChildren();
  const heading = document.createElement('h2');
  heading.textContent = isReady ? 'Your video is ready' : isFailed ? 'Preprocessing failed' : 'Video uploaded';
  const message = document.createElement('p');
  message.textContent = isReady
    ? 'Initial preprocessing is complete. You can continue in the conversation below.'
    : isFailed ? (job.error || 'Please try uploading again.')
      : 'Background initial preprocessing is in progress. You can leave this page and return later.';
  const session = document.createElement('p');
  session.textContent = `Session ID: ${job.session_id}`;
  result.append(heading, message, session);
  if (isReady && !messageTimer) {
    loadMessages(job.id).catch(() => {});
    messageTimer = setInterval(() => loadMessages(job.id).catch(() => {}), POLL_INTERVAL_MS);
  }
}

function refreshJob(jobId = activeJobId) {
  if (!jobId || jobRequests.has(jobId)) return;
  const generation = workspaceGeneration;
  const request = api(`/api/jobs/${jobId}`).then(job => {
    if (generation === workspaceGeneration) updateJob(job);
  }).catch(error => {
    if (generation !== workspaceGeneration) return;
    if (activeJobId === jobId) document.querySelector('#chat-subtitle').textContent = `Could not check status: ${error.message}`;
    if (error.status === 404) {
      trackedJobs.delete(jobId);
      if (activeJobId === jobId) jobStatusLoading = false;
      ensureJobPolling();
    }
  }).finally(() => {
    if (jobRequests.get(jobId) === request) jobRequests.delete(jobId);
  });
  jobRequests.set(jobId, request);
  return request;
}

function openJob(jobId) {
  closeMobileNavigation();
  closeConversationMenu();
  clearPendingImages();
  activeDraftId = null;
  currentFile = null;
  fileInput.value = '';
  fileInput.disabled = false;
  imageInput.disabled = false;
  activeJobId = jobId;
  try { if (currentUser) localStorage.setItem(`frameLastJob:${currentUser.id}`, jobId); }
  catch { /* Opening a saved conversation does not require browser storage. */ }
  document.querySelectorAll('.conversation').forEach(item => {
    item.classList.toggle('active', item.dataset.jobId === jobId);
  });
  analysisForm.classList.add('session-locked');
  analyzeButton.disabled = true;
  analyzeButton.textContent = 'Video uploaded';
  progressWrap.hidden = true;
  fileRow.hidden = true;
  formMessage.textContent = '';
  result.hidden = true;
  result.replaceChildren();
  openChatButton.hidden = false;
  openChatButton.disabled = true;
  document.querySelector('#intro-title').textContent = 'Your video session';
  document.querySelector('#intro-subtitle').textContent = 'Ask follow-up questions and add reference images to explore your video.';
  jobStatusLoading = true;
  if (!trackedJobs.has(jobId)) trackedJobs.set(jobId, { id: jobId, status: 'loading' });
  chatPanel.hidden = false;
  chatInput.disabled = true;
  chatSend.disabled = true;
  chatMessages.replaceChildren();
  renderedMessageIds = new Set();
  clearInterval(messageTimer);
  messageTimer = null;
  refreshJob(jobId);
  ensureJobPolling();
  loadMessages(jobId).catch(() => {});
}

function renderPendingImages() {
  imageList.replaceChildren();
  pendingImages.forEach((item, index) => {
    const card = document.createElement('div');
    card.className = 'pending-image-card';
    const picture = document.createElement('img');
    picture.src = item.url;
    picture.alt = item.file.name;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${item.file.name}`);
    remove.addEventListener('click', () => {
      URL.revokeObjectURL(item.url);
      pendingImages.splice(index, 1);
      renderPendingImages();
    });
    card.append(picture, remove);
    imageList.append(card);
  });
}

function clearPendingImages() {
  pendingImages.forEach(item => URL.revokeObjectURL(item.url));
  pendingImages = [];
  imageInput.value = '';
  renderPendingImages();
}

function clearVideo() {
  closeMobileNavigation();
  closeConversationMenu();
  activeDraftId = null;
  currentFile = null;
  fileInput.value = '';
  fileInput.disabled = false;
  document.querySelector('#remove-file').disabled = false;
  fileRow.hidden = true;
  result.hidden = true;
  result.replaceChildren();
  formMessage.textContent = '';
  progressWrap.hidden = true;
  analysisForm.classList.remove('session-locked');
  analyzeButton.disabled = false;
  analyzeButton.innerHTML = 'Analyze video <span aria-hidden="true">↑</span>';
  openChatButton.hidden = true;
  openChatButton.disabled = true;
  document.querySelector('#intro-title').textContent = 'A closer look at your video.';
  document.querySelector('#intro-subtitle').textContent = 'Add a video and choose what you want to find.';
  clearPendingImages();
  imageMessage.textContent = '';
  chatPanel.hidden = true;
  clearInterval(messageTimer);
  messageTimer = null;
  activeJobId = null;
  jobStatusLoading = false;
  ensureJobPolling();
}

function startNewConversation() {
  clearVideo();
  chatInput.value = '';
  imageInput.disabled = false;
  document.querySelector('#instructions').value = '';
  document.querySelectorAll('.entity-options input').forEach(input => { input.checked = ['People', 'Cars'].includes(input.value); });
  document.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  fileInput.focus();
}

function closeConversationMenu(restoreFocus = false) {
  const trigger = menuTarget?.trigger;
  conversationMenu.hidden = true;
  menuTarget?.options.setAttribute('aria-expanded', 'false');
  menuTarget = null;
  if (restoreFocus && trigger?.isConnected) trigger.focus({ preventScroll: true });
}

function showConversationMenu(target, x, y) {
  closeConversationMenu();
  menuTarget = target;
  target.options.setAttribute('aria-expanded', 'true');
  conversationMenu.hidden = false;
  const margin = 8;
  const left = Math.max(margin, Math.min(x, window.innerWidth - conversationMenu.offsetWidth - margin));
  const top = Math.max(margin, Math.min(y, window.innerHeight - conversationMenu.offsetHeight - margin));
  conversationMenu.style.left = `${left}px`;
  conversationMenu.style.top = `${top}px`;
  document.querySelector('#delete-conversation').focus({ preventScroll: true });
}

document.addEventListener('pointerdown', event => {
  if (!conversationMenu.hidden && !conversationMenu.contains(event.target)) closeConversationMenu();
});
document.addEventListener('keydown', event => {
  if (conversationMenu.hidden) return;
  if (event.key === 'Escape' || event.key === 'Tab') {
    if (event.key === 'Escape') event.preventDefault();
    closeConversationMenu(true);
  }
});
window.addEventListener('resize', () => closeConversationMenu());
// Close on user scrolling, not delayed scroll events from focus restoration.
document.addEventListener('wheel', () => closeConversationMenu(), { passive: true });
document.addEventListener('touchmove', () => closeConversationMenu(), { passive: true });

document.querySelector('#delete-conversation').addEventListener('click', () => {
  if (!menuTarget) return;
  deleteTarget = menuTarget;
  closeConversationMenu();
  document.querySelector('#delete-dialog-description').textContent = `“${deleteTarget.title}” and its video, images, and messages will be permanently deleted. This cannot be undone.`;
  deleteError.hidden = true;
  deleteError.textContent = '';
  deleteDialog.showModal();
  cancelDelete.focus();
});

cancelDelete.addEventListener('click', () => deleteDialog.close());
deleteDialog.addEventListener('cancel', event => {
  if (deleteInProgress) event.preventDefault();
});
deleteDialog.addEventListener('close', () => {
  const trigger = deleteTarget?.trigger;
  deleteTarget = null;
  if (trigger?.isConnected) trigger.focus({ preventScroll: true });
});

confirmDelete.addEventListener('click', async () => {
  if (!deleteTarget || deleteInProgress) return;
  const target = deleteTarget;
  deleteInProgress = true;
  confirmDelete.disabled = true;
  cancelDelete.disabled = true;
  confirmDelete.textContent = 'Deleting…';
  deleteDialog.setAttribute('aria-busy', 'true');
  deleteError.hidden = true;
  try {
    let response;
    try {
      response = await api(`/api/jobs/${target.jobId}`, { method: 'DELETE' });
    } catch (error) {
      if (error.status !== 404) throw error;
      response = { already_removed: true };
    }
    target.row.remove();
    trackedJobs.delete(target.jobId);
    ensureJobPolling();
    const preference = `frameLastJob:${currentUser.id}`;
    if (localStorage.getItem(preference) === target.jobId) localStorage.removeItem(preference);
    const wasActive = activeJobId === target.jobId;
    deleteDialog.close();
    if (wasActive) {
      startNewConversation();
      const next = document.querySelector('.conversation[data-job-id]');
      if (next) {
        openJob(next.dataset.jobId);
        next.focus({ preventScroll: true });
      }
    } else if (!target.trigger.isConnected) {
      (document.querySelector('.conversation.active') || document.querySelector('#new-conversation')).focus({ preventScroll: true });
    }
    notifyUser(response.cleanup_pending ? 'Conversation deleted. Some stored files could not be removed.'
      : response.already_removed ? 'This conversation is no longer available.' : 'Conversation deleted.');
  } catch (error) {
    deleteError.textContent = `Could not delete: ${error.message}`;
    deleteError.hidden = false;
  } finally {
    deleteInProgress = false;
    confirmDelete.disabled = false;
    cancelDelete.disabled = false;
    confirmDelete.textContent = 'Delete conversation';
    deleteDialog.removeAttribute('aria-busy');
  }
});

function addConversation(title, jobId, activate = true) {
  const list = document.querySelector('#conversation-list');
  if (activate) list.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  const button = document.createElement('button');
  const row = document.createElement('div');
  row.className = 'conversation-item';
  button.type = 'button';
  button.dataset.jobId = jobId;
  button.className = `conversation${activate ? ' active' : ''}`;
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg><span class="conversation-copy"><strong></strong><small>Video · just now</small></span>';
  button.querySelector('strong').textContent = title;
  button.title = title;
  button.addEventListener('click', () => {
    list.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    openJob(jobId);
  });
  const options = document.createElement('button');
  options.type = 'button';
  options.className = 'conversation-options';
  options.setAttribute('aria-label', `Options for ${title}`);
  options.setAttribute('aria-haspopup', 'menu');
  options.setAttribute('aria-expanded', 'false');
  options.setAttribute('aria-controls', 'conversation-menu');
  options.title = 'Conversation options';
  options.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';
  options.addEventListener('click', () => {
    const rect = options.getBoundingClientRect();
    showConversationMenu({ jobId, title, row, options, trigger: options }, rect.left, rect.bottom + 4);
  });
  row.addEventListener('contextmenu', event => {
    event.preventDefault();
    const rect = button.getBoundingClientRect();
    showConversationMenu({ jobId, title, row, options, trigger: button }, event.clientX || rect.left, event.clientY || rect.bottom);
  });
  button.addEventListener('keydown', event => {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      event.preventDefault();
      const rect = button.getBoundingClientRect();
      showConversationMenu({ jobId, title, row, options, trigger: button }, rect.left, rect.bottom);
    }
  });
  row.append(button, options);
  list.prepend(row);
}

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const message = document.querySelector('#login-error');
  message.textContent = '';
  signInButton.disabled = true;
  try {
    const user = await api('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: document.querySelector('#username').value.trim(),
        password: document.querySelector('#password').value,
        remember: document.querySelector('#remember').checked })
    });
    loginForm.reset();
    await showWorkspace(user);
  } catch (error) { message.textContent = error.message; }
  finally { signInButton.disabled = false; }
});

document.querySelector('#toggle-password').addEventListener('click', event => {
  const input = document.querySelector('#password');
  const visible = input.type === 'password';
  input.type = visible ? 'text' : 'password';
  event.currentTarget.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
});

document.querySelector('#sign-out').addEventListener('click', async () => {
  if ([...uploadDrafts.values()].some(draft => draft.status === 'uploading')) {
    notifyUser('Wait for active uploads to finish before signing out.');
    return;
  }
  try { await api('/api/auth/logout', { method: 'POST' }); }
  catch (error) { notifyUser(`Could not sign out: ${error.message}`); return; }
  workspaceGeneration++;
  trackedJobs.clear();
  jobRequests.clear();
  clearVideo();
  uploadDrafts.clear();
  pendingUploadCleanup.clear();
  clearInterval(cleanupTimer);
  cleanupTimer = null;
  uploadFailures.length = 0;
  uploadErrorDialog.close();
  currentUser = null;
  csrfToken = null;
  loginForm.reset();
  workspaceScreen.hidden = true;
  loginScreen.hidden = false;
});

document.querySelector('#new-conversation').addEventListener('click', startNewConversation);

imageInput.addEventListener('change', () => {
  const files = [...imageInput.files];
  imageInput.value = '';
  if (!activeJobId || !files.length) return;
  if (pendingImages.length + files.length > MAX_IMAGES_PER_MESSAGE) {
    imageMessage.textContent = 'Attach up to 10 images to one question.';
    return;
  }
  for (const file of files) {
    if (file.size > MAX_IMAGE_BYTES) { imageMessage.textContent = `${file.name} exceeds the 20 MiB limit.`; continue; }
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) { imageMessage.textContent = `${file.name} is not a supported image.`; continue; }
    pendingImages.push({ file, url: URL.createObjectURL(file), uploadedId: null });
  }
  renderPendingImages();
});

fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!file.type.startsWith('video/')) { formMessage.textContent = 'Choose a video file.'; fileInput.value = ''; return; }
  const draft = uploadDrafts.get(activeDraftId);
  if (draft && !matchesDraftFile(draft, file)) {
    formMessage.textContent = 'Select the original video to resume this upload. Use New conversation to upload a different video.';
    fileInput.value = '';
    return;
  }
  if (draft) draft.file = file;
  currentFile = file;
  document.querySelector('#file-name').textContent = file.name;
  document.querySelector('#file-meta').textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB · Video`;
  fileRow.hidden = false;
  result.hidden = true;
  formMessage.textContent = '';
});

document.querySelector('#remove-file').addEventListener('click', () => {
  if (activeDraftId) {
    const draft = uploadDrafts.get(activeDraftId);
    if (draft?.status === 'uploading') return;
    if (draft) forgetDraft(draft);
    draft?.button.remove();
    uploadDrafts.delete(activeDraftId);
  }
  clearVideo();
});

openChatButton.addEventListener('click', () => {
  if (openChatButton.disabled) return;
  chatPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  chatInput.focus({ preventScroll: true });
});

document.querySelector('#analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (activeJobId || (activeDraftId && uploadDrafts.get(activeDraftId)?.status === 'uploading')) return;
  if (!currentFile) { formMessage.textContent = 'Add a video before starting analysis.'; fileInput.focus(); return; }
  const entities = [...document.querySelectorAll('.entity-options input:checked')].map(input => input.value);
  if (!entities.length) { formMessage.textContent = 'Select at least one entity.'; return; }
  const existingDraft = uploadDrafts.get(activeDraftId);
  if (existingDraft && !matchesDraftFile(existingDraft, currentFile)) {
    formMessage.textContent = 'Select the original video to resume this upload. Use New conversation to upload a different video.';
    return;
  }
  formMessage.textContent = '';
  try {
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
  } catch { /* Unsupported browser alerts must not prevent an upload. */ }
  const draft = existingDraft || createDraft(currentFile, entities, document.querySelector('#instructions').value);
  draft.file = currentFile;
  draft.entities = entities;
  draft.instructions = document.querySelector('#instructions').value;
  draft.status = 'uploading';
  draft.error = '';
  renderDraft(draft);
  analyzeButton.disabled = true;
  analyzeButton.textContent = 'Uploading…';
  fileInput.disabled = true;
  document.querySelector('#remove-file').disabled = true;
  await runUpload(draft);
});

document.querySelector('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const content = chatInput.value.trim();
  if ((!content && !pendingImages.length) || !activeJobId || chatInput.disabled) return;
  const jobId = activeJobId;
  const attachments = [...pendingImages];
  chatInput.value = '';
  chatInput.disabled = true;
  chatSend.disabled = true;
  imageInput.disabled = true;
  imageMessage.textContent = attachments.length ? 'Uploading images with your question…' : 'Sending question…';
  try {
    for (const item of attachments) {
      if (!item.uploadedId) {
        const saved = await api(`/api/sessions/${jobId}/images`, {
          method: 'POST',
          headers: { 'Content-Type': item.file.type, 'X-Filename': encodeURIComponent(item.file.name) },
          body: item.file
        });
        item.uploadedId = saved.id;
      }
    }
    const reply = await api(`/api/jobs/${jobId}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, image_ids: attachments.map(item => item.uploadedId) })
    });
    if (activeJobId === jobId) {
      if (reply.user_message?.id && !renderedMessageIds.has(reply.user_message.id)) {
        renderedMessageIds.add(reply.user_message.id);
        addBubble('user', reply.user_message.content, reply.user_message.images);
      }
      if (reply.id && !renderedMessageIds.has(reply.id)) {
        renderedMessageIds.add(reply.id);
        addBubble('assistant', reply.content, reply.images || []);
      }
      clearPendingImages();
      imageMessage.textContent = '';
    }
  } catch (error) {
    if (activeJobId === jobId) { imageMessage.textContent = `Could not send: ${error.message}`; chatInput.value = content; }
  } finally {
    if (activeJobId === jobId) { chatInput.disabled = false; chatSend.disabled = false; imageInput.disabled = false; chatInput.focus(); }
  }
});

api('/api/me').then(showWorkspace).catch(error => {
  if (error.status !== 401) document.querySelector('#login-error').textContent = error.message;
});

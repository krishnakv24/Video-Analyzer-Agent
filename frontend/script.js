const loginScreen = document.querySelector('#login-screen');
const workspaceScreen = document.querySelector('#workspace-screen');
const loginForm = document.querySelector('#login-form');
const fileInput = document.querySelector('#video-file');
const fileRow = document.querySelector('#file-row');
const formMessage = document.querySelector('#form-message');
const result = document.querySelector('#analysis-result');
let currentFile = null;
let uploading = false;
let activeJobId = null;
let jobTimer = null;
let messageTimer = null;
let renderedMessageIds = new Set();
let lastJobStatus = null;
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

function uploadKey(file) {
  return `frameUpload:${currentUser.id}:${file.name}:${file.size}:${file.lastModified}`;
}

function showProgress(offset, size) {
  const percent = Math.round(offset / size * 100);
  progressWrap.hidden = false;
  progressBar.value = percent;
  progressLabel.textContent = `${percent}% uploaded`;
}

async function uploadVideo(file) {
  const key = uploadKey(file);
  const legacyKey = `frameUpload:${file.name}:${file.size}:${file.lastModified}`;
  let upload;
  const oldId = localStorage.getItem(key) || localStorage.getItem(legacyKey);
  if (oldId) {
    try {
      upload = await api(`/api/uploads/${oldId}`);
      if (upload.size !== file.size) throw new Error('Upload size changed');
      localStorage.setItem(key, oldId);
      localStorage.removeItem(legacyKey);
    } catch (error) {
      if (error.status !== 404) throw error;
      localStorage.removeItem(key);
      localStorage.removeItem(legacyKey);
    }
  }
  if (!upload) {
    upload = await api('/api/uploads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, size: file.size })
    });
    localStorage.setItem(key, upload.id);
  }
  let offset = upload.offset;
  showProgress(offset, file.size);
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
    showProgress(offset, file.size);
  }
  if (upload.status !== 'complete') await api(`/api/uploads/${upload.id}/complete`, { method: 'POST' });
  return { id: upload.id, storageKey: key };
}

async function showWorkspace(user) {
  const response = await api('/api/jobs');
  currentUser = user;
  csrfToken = user.csrf_token;
  document.querySelector('#account-name').textContent = user.username;
  document.querySelector('#avatar').textContent = user.username.charAt(0).toUpperCase();
  const list = document.querySelector('#conversation-list');
  list.replaceChildren();
  [...response.jobs].reverse().forEach(job => addConversation(job.filename.replace(/\.[^.]+$/, ''), job.id));
  const remembered = localStorage.getItem(`frameLastJob:${user.id}`);
  const selected = response.jobs.find(job => job.id === remembered) || response.jobs[0];
  loginScreen.hidden = true;
  workspaceScreen.hidden = false;
  if (selected) openJob(selected.id);
}

function notifyUser(message, browserNotification = false) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 6500);
  if (browserNotification && 'Notification' in window && Notification.permission === 'granted') {
    new Notification('Frame', { body: message });
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
  if (isReady && lastJobStatus !== 'ready') {
    notifyUser('Video preprocessing is complete. You can continue the conversation.', true);
    loadMessages(job.id).catch(() => {});
    clearInterval(messageTimer);
    messageTimer = setInterval(() => loadMessages(job.id).catch(() => {}), POLL_INTERVAL_MS);
  }
  if (isFailed && lastJobStatus !== 'failed') notifyUser('Video preprocessing failed. Please try again.');
  lastJobStatus = job.status;
  if (isReady || isFailed) { clearInterval(jobTimer); jobTimer = null; }
}

async function refreshJob() {
  if (!activeJobId) return;
  try { displayJob(await api(`/api/jobs/${activeJobId}`)); }
  catch (error) { document.querySelector('#chat-subtitle').textContent = `Could not check status: ${error.message}`; }
}

function openJob(jobId) {
  clearPendingImages();
  activeJobId = jobId;
  if (currentUser) localStorage.setItem(`frameLastJob:${currentUser.id}`, jobId);
  document.querySelectorAll('.conversation').forEach(item => {
    item.classList.toggle('active', item.dataset.jobId === jobId);
  });
  analysisForm.classList.add('session-locked');
  analyzeButton.disabled = true;
  openChatButton.hidden = false;
  openChatButton.disabled = true;
  document.querySelector('#intro-title').textContent = 'Your video session';
  document.querySelector('#intro-subtitle').textContent = 'One video per session. Add images or continue in chat.';
  lastJobStatus = null;
  chatPanel.hidden = false;
  chatInput.disabled = true;
  chatSend.disabled = true;
  chatMessages.replaceChildren();
  renderedMessageIds = new Set();
  clearInterval(jobTimer);
  clearInterval(messageTimer);
  messageTimer = null;
  refreshJob();
  jobTimer = setInterval(refreshJob, POLL_INTERVAL_MS);
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
  if (uploading) return;
  currentFile = null;
  fileInput.value = '';
  fileRow.hidden = true;
  result.hidden = true;
  result.replaceChildren();
  formMessage.textContent = '';
  progressWrap.hidden = true;
  analysisForm.classList.remove('session-locked');
  analyzeButton.disabled = false;
  openChatButton.hidden = true;
  openChatButton.disabled = true;
  document.querySelector('#intro-title').textContent = 'A closer look at your video.';
  document.querySelector('#intro-subtitle').textContent = 'Add a video and choose what you want to find.';
  clearPendingImages();
  imageMessage.textContent = '';
  chatPanel.hidden = true;
  clearInterval(jobTimer);
  jobTimer = null;
  clearInterval(messageTimer);
  messageTimer = null;
  activeJobId = null;
  lastJobStatus = null;
}

function addConversation(title, jobId) {
  const list = document.querySelector('#conversation-list');
  list.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.jobId = jobId;
  button.className = 'conversation active';
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg><span class="conversation-copy"><strong></strong><small>Video · just now</small></span>';
  button.querySelector('strong').textContent = title;
  button.addEventListener('click', () => {
    list.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    openJob(jobId);
  });
  list.prepend(button);

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
  if (uploading) { notifyUser('Wait for the upload to finish before signing out.'); return; }
  try { await api('/api/auth/logout', { method: 'POST' }); }
  catch (error) { notifyUser(`Could not sign out: ${error.message}`); return; }
  clearVideo();
  currentUser = null;
  csrfToken = null;
  loginForm.reset();
  workspaceScreen.hidden = true;
  loginScreen.hidden = false;
});

document.querySelector('#new-conversation').addEventListener('click', () => {
  if (uploading) return;
  clearVideo();
  document.querySelector('#instructions').value = '';
  document.querySelectorAll('.entity-options input').forEach(input => { input.checked = ['People', 'Cars'].includes(input.value); });
  document.querySelectorAll('.conversation').forEach(item => item.classList.remove('active'));
  fileInput.focus();
});

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
  currentFile = file;
  document.querySelector('#file-name').textContent = file.name;
  document.querySelector('#file-meta').textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB · Video`;
  fileRow.hidden = false;
  result.hidden = true;
  formMessage.textContent = '';
});

document.querySelector('#remove-file').addEventListener('click', clearVideo);

openChatButton.addEventListener('click', () => {
  if (openChatButton.disabled) return;
  chatPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  chatInput.focus({ preventScroll: true });
});

document.querySelector('#analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (uploading || activeJobId) return;
  if (!currentFile) { formMessage.textContent = 'Add a video before starting analysis.'; fileInput.focus(); return; }
  const entities = [...document.querySelectorAll('.entity-options input:checked')].map(input => input.value);
  if (!entities.length) { formMessage.textContent = 'Select at least one entity.'; return; }
  formMessage.textContent = '';
  uploading = true;
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
  analyzeButton.disabled = true;
  analyzeButton.textContent = 'Uploading…';
  try {
    const upload = await uploadVideo(currentFile);
    let job;
    try {
      job = await api('/api/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: upload.id, entities, instructions: document.querySelector('#instructions').value })
      });
    } catch (error) {
      if (error.status !== 409) throw error;
      job = (await api('/api/jobs')).jobs.find(item => item.upload_id === upload.id);
      if (!job) throw error;
    }
    localStorage.removeItem(upload.storageKey);
    addConversation(currentFile.name.replace(/\.[^.]+$/, ''), job.id);
    openJob(job.id);
    notifyUser('Video upload complete. Background preprocessing has started.', true);
  } catch (error) {
    formMessage.textContent = `${error.message}. Re-select the same file and try again to resume.`;
  } finally {
    uploading = false;
    analyzeButton.disabled = Boolean(activeJobId);
    analyzeButton.innerHTML = 'Analyze video <span aria-hidden="true">↑</span>';
  }
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

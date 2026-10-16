(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const views = { login: $('#login-view'), key: $('#key-view'), app: $('#app-view') };
  const els = {
    messages: $('#messages'), question: $('#question'), askButton: $('#ask-button'), mic: $('#mic-button'),
    voiceStatus: $('#voice-status'), connection: $('#connection-state'), hud: $('#hud'), hudPanel: $('#hud-panel')
  };
  const Voice = window.CampVoice || null;
  const HUD = window.CampHUD || null;
  const GENERIC_ERROR = 'CampSage is temporarily unavailable. Please try again.';
  const NO_ANSWER = 'I do not have an answer from the available sources.';

  // voice: idle | starting | recording | transcribing | thinking | speaking
  // Counters invalidate stale async work: epoch (sign-out), seq (asks), recSeq (recording starts), playSeq (playback).
  const state = { profile: null, mode: 'chat', voice: 'idle', busy: false, greeted: false, hudMounted: false, epoch: 0, seq: 0, recSeq: 0, playSeq: 0 };

  const LABELS = { idle: 'Ready', starting: 'Starting mic...', recording: 'Listening...', transcribing: 'Transcribing...', thinking: 'Thinking...', speaking: 'Speaking...' };
  const HINTS = {
    starting: 'Starting microphone...', recording: 'Listening... tap the mic to send', transcribing: 'Transcribing...',
    thinking: 'Thinking...', speaking: 'Speaking... tap the mic to interrupt'
  };

  function show(view) { Object.values(views).forEach((node) => node.classList.add('hidden')); views[view].classList.remove('hidden'); }
  function scrollToEnd() { els.messages.scrollTop = els.messages.scrollHeight; }
  function shortText(value, fallback) {
    if (typeof value !== 'string') return fallback;
    const text = value.trim();
    if (!text || text[0] === '{' || text[0] === '[') return fallback;
    return text.slice(0, 300);
  }
  function friendly(error, fallback) { return error && error.friendly ? shortText(error.message, fallback) : fallback; }
  async function readJson(response) { try { return await response.json(); } catch (_) { return null; } }
  async function request(url, options = {}) { return fetch(url, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } }); }

  function message(text, who = 'system', sources = []) {
    const node = document.createElement('article'); node.className = `message ${who}`;
    const label = document.createElement('div'); label.className = 'label';
    label.textContent = who === 'user' ? 'You' : who === 'assistant' ? 'CampSage' : 'System';
    const body = document.createElement('div'); body.textContent = String(text);
    node.append(label, body);
    const items = Array.isArray(sources) ? sources.filter((item) => item && typeof item.source === 'string') : [];
    if (items.length) {
      const list = document.createElement('div'); list.className = 'sources';
      items.forEach((item) => { const chip = document.createElement('span'); chip.className = 'source'; chip.textContent = `${item.source}${item.page ? ` · page ${item.page}` : ''}`; list.append(chip); });
      node.append(list);
    }
    els.messages.append(node); scrollToEnd(); requestAnimationFrame(scrollToEnd);
    return node;
  }
  function greet() {
    if (state.greeted) return;
    state.greeted = true;
    const name = state.profile && typeof state.profile.name === 'string' ? state.profile.name.trim() : '';
    message(name ? `Hello ${name}! How can I help you?` : 'Hello! How can I help you?', 'assistant');
  }
  function renderProfile(profile) {
    profile = profile || {};
    state.profile = profile; $('#profile-class').textContent = profile.class || 'Student';
    const list = $('#profile-list'); list.replaceChildren();
    [['Semester', profile.semester], ['Branch', profile.branch], ['Attendance', profile.attendance?.overall || 'Available after login'], ['Fees', profile.fees?.status || 'Available after login'], ['Registration', profile.registration?.status || 'Available after login']].forEach(([name, value]) => {
      const dt = document.createElement('dt'); dt.textContent = name;
      const dd = document.createElement('dd'); dd.textContent = String(value ?? 'Not available');
      list.append(dt, dd);
    });
  }

  // ---- HUD -----------------------------------------------------------------
  function syncHud() {
    if (!HUD || !state.hudMounted) return;
    HUD.setState(state.voice === 'recording' ? 'listening' : state.voice === 'speaking' ? 'speaking' : 'idle');
  }
  function mountHud() {
    if (!HUD || state.hudMounted) return;
    try { HUD.mount(els.hud); state.hudMounted = true; syncHud(); } catch (_) { state.hudMounted = false; }
  }
  function unmountHud() {
    if (!HUD || !state.hudMounted) return;
    try { HUD.unmount(); } catch (_) {}
    state.hudMounted = false;
  }
  if (Voice) {
    Voice.onLevel((level, source) => {
      if (!state.hudMounted) return;
      if ((source === 'mic' && state.voice === 'recording') || (source === 'speaker' && state.voice === 'speaking')) HUD.setLevel(level);
    });
    Voice.onLimit(() => { if (state.voice === 'recording') finishRecording(); });
  }

  // ---- voice state -----------------------------------------------------------
  function setVoice(next) {
    state.voice = next;
    state.busy = next === 'transcribing' || next === 'thinking';
    els.askButton.disabled = state.busy;
    els.mic.disabled = state.busy || next === 'starting';
    els.mic.dataset.state = next;
    els.mic.setAttribute('aria-pressed', next === 'recording' ? 'true' : 'false');
    els.mic.setAttribute('aria-label', next === 'recording' ? 'Stop and send' : next === 'speaking' ? 'Interrupt and talk' : 'Start talking');
    els.connection.textContent = LABELS[next];
    els.voiceStatus.textContent = HINTS[next] || (state.mode === 'talk' ? 'Talk mode: tap the mic to speak, or type' : 'Chat mode: replies are silent');
    syncHud();
  }
  function stopSpeaking() {
    state.playSeq += 1;
    if (Voice) Voice.stopPlayback();
    if (state.voice === 'speaking') setVoice('idle');
  }
  function abortCapture() {
    if (state.voice !== 'starting' && state.voice !== 'recording') return;
    state.recSeq += 1;
    if (Voice) Voice.cancel();
    setVoice('idle');
  }
  function selectedMode() { return document.querySelector('input[name="mode"]:checked')?.value || 'chat'; }
  function updateMode() {
    const next = selectedMode();
    if (next === state.mode) return;
    state.mode = next;
    if (next === 'talk') {
      if (Voice) Voice.unlock();
      document.body.classList.add('mode-talk');
      els.hudPanel.setAttribute('aria-hidden', 'false');
      mountHud();
    } else {
      abortCapture(); stopSpeaking();
      document.body.classList.remove('mode-talk');
      els.hudPanel.setAttribute('aria-hidden', 'true');
      unmountHud();
    }
    setVoice(state.voice);
  }
  function resetUi() {
    state.epoch += 1; state.seq += 1; state.recSeq += 1; state.playSeq += 1;
    if (Voice) { Voice.cancel(); Voice.stopPlayback(); }
    unmountHud();
    state.mode = 'chat'; document.body.classList.remove('mode-talk'); els.hudPanel.setAttribute('aria-hidden', 'true');
    const chat = document.querySelector('input[name="mode"][value="chat"]'); if (chat) chat.checked = true;
    els.messages.replaceChildren(); els.question.value = '';
    state.greeted = false; state.profile = null;
    setVoice('idle');
  }
  function sessionExpired() {
    resetUi(); show('login');
    $('#login-error').textContent = 'Your session expired. Please sign in again.';
  }

  // ---- asking and speaking -----------------------------------------------------
  function playAnswer(base64) {
    if (!Voice) return;
    const epoch = state.epoch; const token = ++state.playSeq;
    const current = () => epoch === state.epoch && token === state.playSeq;
    setVoice('speaking');
    Voice.playPcm24k(base64)
      .catch((error) => { if (current()) message(friendly(error, 'Audio playback is unavailable. The answer is in the chat.'), 'system'); })
      .then(() => { if (current() && state.voice === 'speaking') setVoice('idle'); });
  }

  async function ask(question) {
    const epoch = state.epoch; const seq = ++state.seq; const talk = state.mode === 'talk';
    const current = () => epoch === state.epoch && seq === state.seq;
    setVoice('thinking');
    try {
      const response = await request('/api/ask', { method: 'POST', body: JSON.stringify({ question, audio: talk }) });
      const data = await readJson(response);
      if (!current()) return;
      if (response.status === 401) { sessionExpired(); return; }
      if (!data || typeof data !== 'object') { message(GENERIC_ERROR, 'system'); return; }
      const status = typeof data.status === 'string' ? data.status : (response.ok ? 'answered' : 'error');
      const answer = typeof data.answer === 'string' ? data.answer.trim() : '';
      if (!response.ok || status === 'error') { message(shortText(answer || data.error, GENERIC_ERROR), 'system'); return; }
      message(answer || NO_ANSWER, 'assistant', status === 'answered' ? data.sources : []);
      if (state.mode === 'talk' && typeof data.audio_base64 === 'string' && data.audio_base64) playAnswer(data.audio_base64);
    } catch (_) {
      if (current()) message(GENERIC_ERROR, 'system');
    } finally {
      if (current() && state.voice === 'thinking') setVoice('idle');
    }
  }

  async function finishRecording() {
    if (state.voice !== 'recording' || !Voice) return;
    const epoch = state.epoch;
    setVoice('transcribing');
    let text;
    try { text = await Voice.stopRecording(); }
    catch (error) {
      if (epoch !== state.epoch) return;
      setVoice('idle');
      message(friendly(error, 'I could not process the audio. Please try again.'), 'system');
      return;
    }
    if (epoch !== state.epoch) return;
    message(text, 'user');
    await ask(text);
  }

  // Called straight from the tap: no await before startRecording() so Safari keeps the user gesture.
  function onMicClick() {
    if (state.mode !== 'talk') return;
    if (!Voice) { message('Voice is not available in this browser. You can still type.', 'system'); return; }
    if (state.voice === 'recording') { finishRecording(); return; }
    if (state.voice !== 'idle' && state.voice !== 'speaking') return;
    stopSpeaking();
    const epoch = state.epoch; const rs = ++state.recSeq;
    const current = () => epoch === state.epoch && rs === state.recSeq && state.voice === 'starting';
    let started;
    try { started = Voice.startRecording(); } catch (error) { message(friendly(error, 'Could not start the microphone.'), 'system'); return; }
    setVoice('starting');
    started.then(
      () => { if (current()) setVoice('recording'); },
      (error) => { if (current()) { setVoice('idle'); message(friendly(error, 'Could not start the microphone. Please try again.'), 'system'); } }
    );
  }

  // ---- events -------------------------------------------------------------------
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('#login-error').textContent = '';
    const button = event.target.querySelector('button'); button.disabled = true;
    try {
      const response = await request('/api/login', { method: 'POST', body: JSON.stringify({ student_id: $('#student-id').value.trim(), password: $('#password').value }) });
      const data = await readJson(response);
      if (!response.ok || !data) throw new Error(shortText(data && data.error, 'Login failed'));
      resetUi(); renderProfile(data.profile);
      if (data.needs_key) show('key'); else { show('app'); greet(); }
      $('#password').value = '';
    } catch (error) { $('#login-error').textContent = shortText(error.message, 'Login failed'); }
    finally { button.disabled = false; }
  });
  $('#key-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('#key-error').textContent = '';
    const input = $('#api-key'); const key = input.value; input.value = '';
    const button = event.target.querySelector('button'); button.disabled = true;
    try {
      const response = await request('/api/connect-key', { method: 'POST', body: JSON.stringify({ api_key: key }) });
      const data = await readJson(response);
      if (!response.ok) throw new Error(shortText(data && data.error, 'Could not connect key'));
      show('app'); greet();
    } catch (error) { $('#key-error').textContent = shortText(error.message, 'Could not connect key'); }
    finally { button.disabled = false; }
  });
  $('#ask-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (state.busy) return;
    const question = els.question.value.trim();
    if (!question) return;
    els.question.value = '';
    if (state.mode === 'talk' && Voice) Voice.unlock();
    abortCapture(); stopSpeaking();
    message(question, 'user');
    ask(question);
  });
  els.mic.addEventListener('click', onMicClick);
  document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener('change', updateMode));
  $('#logout').addEventListener('click', async () => {
    resetUi(); show('login');
    try { await request('/api/logout', { method: 'POST', body: '{}' }); } catch (_) {}
  });

  setVoice('idle');
  (async () => {
    try {
      const response = await request('/api/me'); const data = await readJson(response);
      if (!data || !data.logged_in) return show('login');
      renderProfile(data.profile);
      if (data.needs_key) show('key'); else { show('app'); greet(); }
    } catch (_) { show('login'); }
  })();
})();

(() => {
  const $ = (selector) => document.querySelector(selector);
  const views = { login: $('#login-view'), key: $('#key-view'), app: $('#app-view') };
  const state = { profile: null, busy: false, mode: 'chat', recognition: null, speaking: false, audioContext: null, audioSource: null, audioResolve: null, audioToken: 0 };

  function show(view) { Object.values(views).forEach((node) => node.classList.add('hidden')); views[view].classList.remove('hidden'); }
  function message(text, who = 'system', sources = []) {
    const node = document.createElement('article'); node.className = `message ${who}`;
    const label = document.createElement('div'); label.className = 'label'; label.textContent = who === 'user' ? 'You' : who === 'assistant' ? 'CampSage' : 'System';
    node.append(label); const body = document.createElement('div'); body.textContent = text; node.append(body);
    if (sources.length) { const list = document.createElement('div'); list.className = 'sources'; sources.forEach((item) => { const chip = document.createElement('span'); chip.className = 'source'; chip.textContent = `${item.source}${item.page ? ` · page ${item.page}` : ''}`; list.append(chip); }); node.append(list); }
    $('#messages').append(node); node.scrollIntoView({ block: 'end' }); return node;
  }
  async function request(url, options = {}) { return fetch(url, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } }); }
  function renderProfile(profile) {
    state.profile = profile; $('#profile-class').textContent = profile.class || 'Student';
    const list = $('#profile-list'); list.replaceChildren();
    [['Semester', profile.semester], ['Branch', profile.branch], ['Attendance', profile.attendance?.overall || 'Available after login'], ['Fees', profile.fees?.status || 'Available after login'], ['Registration', profile.registration?.status || 'Available after login']].forEach(([name, value]) => { const dt = document.createElement('dt'); dt.textContent = name; const dd = document.createElement('dd'); dd.textContent = String(value ?? 'Not available'); list.append(dt, dd); });
  }
  function setBusy(value) { state.busy = value; $('#ask-button').disabled = value; $('#connection-state').textContent = value ? 'Thinking...' : 'Ready'; }
  function selectedMode() { return document.querySelector('input[name="mode"]:checked')?.value || 'chat'; }
  function updateMode() { state.mode = selectedMode(); if (state.mode !== 'talk') { stopAudio(); window.speechSynthesis?.cancel(); } $('#voice-status').textContent = state.mode === 'talk' ? 'Talk mode: microphone ready' : 'Chat mode: silent'; document.body.classList.toggle('mode-talk', state.mode === 'talk'); if (state.mode === 'talk') startRecognition(); else stopRecognition(); }

  function speak(text) {
    if (state.mode !== 'talk' || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel(); const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'en-IN'; utterance.rate = .94; utterance.pitch = .95;
    // Belal55's Gemini voice is Charon. Browser speech has no guaranteed Charon
    // voice, so prefer it when installed and otherwise use a local English voice.
    const voices = window.speechSynthesis.getVoices(); const preferred = voices.find((voice) => /charon/i.test(voice.name)) || voices.find((voice) => /^en(-IN|-GB|-US)/i.test(voice.lang)); if (preferred) utterance.voice = preferred;
    state.speaking = true; $('#voice-status').textContent = 'Speaking CampSage reply...'; utterance.onend = () => { state.speaking = false; }; window.speechSynthesis.speak(utterance);
  }
  function stopAudio() {
    state.audioToken += 1;
    const source = state.audioSource; state.audioSource = null;
    const resolve = state.audioResolve; state.audioResolve = null;
    if (source) { source.onended = null; try { source.stop(); } catch (_) {} try { source.disconnect(); } catch (_) {} }
    state.speaking = false; if (resolve) resolve(false);
  }
  async function playPcm16(base64) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) throw new Error('Web Audio is unavailable in this browser.');
    stopAudio(); const token = state.audioToken;
    const binary = atob(base64); if (!binary.length || binary.length % 2) throw new Error('The audio response was invalid.');
    const samples = new Int16Array(binary.length / 2);
    for (let index = 0; index < samples.length; index += 1) samples[index] = binary.charCodeAt(index * 2) | (binary.charCodeAt(index * 2 + 1) << 8);
    state.audioContext = state.audioContext || new AudioContext(); const context = state.audioContext; await context.resume(); if (state.audioToken !== token) return false;
    const buffer = context.createBuffer(1, samples.length, 24000); const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
    state.audioSource = source; state.speaking = true; $('#voice-status').textContent = 'Playing CampSage audio...';
    return new Promise((resolve) => {
      const finish = () => { if (state.audioToken !== token) return; state.audioSource = null; state.audioResolve = null; state.speaking = false; resolve(true); };
      state.audioResolve = () => resolve(false); source.onended = finish; try { source.start(); } catch (error) { finish(); throw error; }
    });
  }
  async function playAssistantResponse(text, audioBase64) {
    if (state.mode !== 'talk') return;
    if (audioBase64) {
      try { await playPcm16(audioBase64); } catch (_) { state.speaking = false; $('#voice-status').textContent = 'Audio playback unavailable'; }
      return;
    }
    speak(text);
  }
  function startRecognition() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { $('#voice-status').textContent = 'Talk mode needs Chrome or Edge speech recognition'; return; }
    stopRecognition(); const recognition = new Recognition(); recognition.lang = 'en-IN'; recognition.interimResults = false; recognition.continuous = false;
    recognition.onstart = () => { $('#voice-status').textContent = 'Listening...'; };
    recognition.onresult = (event) => { const text = event.results[0][0].transcript.trim(); $('#question').value = text; $('#ask-form').requestSubmit(); };
    recognition.onerror = () => { $('#voice-status').textContent = 'Microphone unavailable; you can type instead'; };
    recognition.onend = () => { if (state.mode === 'talk' && !state.busy) $('#voice-status').textContent = 'Talk mode: microphone ready'; };
    state.recognition = recognition; try { recognition.start(); } catch (_) {}
  }
  function stopRecognition() { if (state.recognition) { try { state.recognition.stop(); } catch (_) {} state.recognition = null; } }

  $('#login-form').addEventListener('submit', async (event) => { event.preventDefault(); $('#login-error').textContent = ''; const button = event.target.querySelector('button'); button.disabled = true;
    try { const response = await request('/api/login', { method: 'POST', body: JSON.stringify({ student_id: $('#student-id').value.trim(), password: $('#password').value }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Login failed'); renderProfile(data.profile); if (data.needs_key) show('key'); else { show('app'); message('You are signed in. Ask me about your college information or notices.', 'assistant'); } $('#password').value = ''; }
    catch (error) { $('#login-error').textContent = error.message; } finally { button.disabled = false; }
  });
  $('#key-form').addEventListener('submit', async (event) => { event.preventDefault(); $('#key-error').textContent = ''; const input = $('#api-key'); const key = input.value; input.value = ''; const button = event.target.querySelector('button'); button.disabled = true;
    try { const response = await request('/api/connect-key', { method: 'POST', body: JSON.stringify({ api_key: key }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not connect key'); show('app'); message('Your key is connected. Ask your first question.', 'assistant'); } catch (error) { $('#key-error').textContent = error.message; } finally { button.disabled = false; }
  });
  $('#ask-form').addEventListener('submit', async (event) => { event.preventDefault(); if (state.busy) return; const input = $('#question'); const question = input.value.trim(); if (!question) return; input.value = ''; message(question, 'user'); setBusy(true); stopRecognition();
     try { const response = await request('/api/ask', { method: 'POST', body: JSON.stringify({ question }) }); const data = await response.json(); if (response.status === 401) { show('login'); throw new Error('Your session expired. Please sign in again.'); } const answer = data.answer || 'I do not have an answer from the available sources.'; message(answer, 'assistant', data.sources || []); await playAssistantResponse(answer, data.audio_base64); } catch (error) { message(error.message || 'The assistant is temporarily unavailable.', 'system'); } finally { setBusy(false); if (state.mode === 'talk') startRecognition(); }
  });
  $('#logout').addEventListener('click', async () => { stopRecognition(); stopAudio(); window.speechSynthesis?.cancel(); state.mode = 'chat'; document.body.classList.remove('mode-talk'); document.querySelector('input[name="mode"][value="chat"]').checked = true; await request('/api/logout', { method: 'POST', body: '{}' }); $('#messages').replaceChildren(); show('login'); });
  document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener('change', updateMode));
  (async () => { try { const response = await request('/api/me'); const data = await response.json(); if (!data.logged_in) return show('login'); renderProfile(data.profile); show(data.needs_key ? 'key' : 'app'); if (!data.needs_key) message('Welcome back. Ask me about your timetable, fees, attendance or college notices.', 'assistant'); } catch (_) { show('login'); } })();
})();

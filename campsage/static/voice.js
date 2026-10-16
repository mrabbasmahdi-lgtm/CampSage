/* CampSage push-to-talk voice helper.
 *
 * window.CampVoice = { startRecording, stopRecording, cancel, playPcm24k, stopPlayback, onLevel, unlock }
 *
 * Capture : mic -> mono Float32 -> box-filter resample to 16 kHz -> PCM16 little-endian
 *           -> POST /api/transcribe (application/octet-stream).
 * Playback: base64 PCM16 mono 24 kHz little-endian (the Live reply) via Web Audio.
 * Playback never blocks anything else: playPcm24k returns a promise, the page stays interactive.
 * All failures reject with an Error whose .message is safe to show to the student.
 */
(function () {
  'use strict';

  var TARGET_RATE = 16000;
  var PLAYBACK_RATE = 24000;
  // Server accepts 3,200..1,000,000 bytes (campsage/app.py). Stay safely under the maximum:
  // 896,000 bytes = 28 s of 16 kHz PCM16 mono.
  var MIN_BYTES = 3200;
  var MAX_RECORD_BYTES = 896000;
  var RESUME_TIMEOUT_MS = 1500;

  var ctx = null;                 // one shared AudioContext (capture + playback), iOS-friendly
  var levelListeners = [];
  var rec = null;                 // active recording session
  var recToken = 0;               // bumped on cancel/new start to invalidate pending async work
  var play = null;                // active playback session
  var playToken = 0;
  var limitListeners = [];

  // ---------------------------------------------------------------- helpers
  function fail(message) { var e = new Error(message); e.friendly = true; return e; }

  function emitLevel(level, source) {
    if (!(level >= 0)) level = 0;
    if (level > 1) level = 1;
    for (var i = 0; i < levelListeners.length; i += 1) {
      try { levelListeners[i](level, source); } catch (_) { /* a bad listener must not break audio */ }
    }
  }

  function rmsToLevel(rms) {
    // Speech RMS is small; a gentle curve makes the orb visibly react.
    return Math.min(1, Math.sqrt(rms) * 1.8);
  }

  function getContext() {
    if (ctx && ctx.state !== 'closed') return ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw fail('Audio is not supported in this browser.');
    ctx = new AC();
    return ctx;
  }

  // Must be called synchronously inside a user gesture on iOS/Android Safari.
  function resumeContext() {
    var context = getContext();
    if (context.state === 'running') return Promise.resolve(context);
    var resumed;
    try { resumed = context.resume(); } catch (_) { resumed = null; }
    if (!resumed || typeof resumed.then !== 'function') return Promise.resolve(context);
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        if (context.state === 'running') resolve(context);
        else reject(fail('Tap the page once to enable audio, then try again.'));
      }, RESUME_TIMEOUT_MS);
      resumed.then(function () { clearTimeout(timer); resolve(context); },
                   function () { clearTimeout(timer); reject(fail('Audio could not be started. Tap the page and try again.')); });
    });
  }

  function micError(error) {
    var name = error && error.name;
    if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') {
      return fail('Microphone access was blocked. Allow the microphone for this site in your browser settings, then try again.');
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
      return fail('No microphone was found on this device.');
    }
    if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
      return fail('The microphone is busy or unavailable. Close other apps using it and try again.');
    }
    return fail('Could not start the microphone. Please try again.');
  }

  // ------------------------------------------------- streaming 16 kHz resampler
  // Box-filter (area average) resampling: cheap anti-aliasing when downsampling 44.1/48 kHz.
  function Resampler(inRate) {
    this.ratio = inRate / TARGET_RATE;   // input samples per output sample
    this.sum = 0;
    this.filled = 0;
  }
  Resampler.prototype.process = function (input, out) {
    var ratio = this.ratio, sum = this.sum, filled = this.filled;
    for (var i = 0; i < input.length; i += 1) {
      var x = input[i], w = 1;
      while (w > 1e-9) {
        var need = ratio - filled;
        if (w >= need) {
          sum += x * need;
          out.push(sum / ratio);
          sum = 0; filled = 0; w -= need;
        } else {
          sum += x * w; filled += w; w = 0;
        }
      }
    }
    this.sum = sum; this.filled = filled;
  };

  function floatToInt16(samples) {
    var pcm = new Int16Array(samples.length);
    for (var i = 0; i < samples.length; i += 1) {
      var s = samples[i];
      s = s < -1 ? -1 : (s > 1 ? 1 : s);
      pcm[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    }
    return pcm;
  }

  function int16ToLeBytes(chunks, totalSamples) {
    // Write bytes explicitly so the output is little-endian on any host.
    var bytes = new Uint8Array(totalSamples * 2);
    var offset = 0;
    for (var c = 0; c < chunks.length; c += 1) {
      var chunk = chunks[c];
      for (var i = 0; i < chunk.length; i += 1) {
        var v = chunk[i];
        bytes[offset++] = v & 0xff;
        bytes[offset++] = (v >> 8) & 0xff;
      }
    }
    return bytes;
  }

  // ------------------------------------------------------------ capture nodes
  var WORKLET_SOURCE = [
    'class CampCapture extends AudioWorkletProcessor {',
    '  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }',
    '  process(inputs) {',
    '    var ch = inputs[0] && inputs[0][0];',
    '    if (ch) {',
    '      for (var i = 0; i < ch.length; i += 1) {',
    '        this.buf[this.n++] = ch[i];',
    '        if (this.n === this.buf.length) { var out = this.buf; this.port.postMessage(out, [out.buffer]); this.buf = new Float32Array(2048); this.n = 0; }',
    '      }',
    '    }',
    '    return true;',
    '  }',
    '}',
    'registerProcessor("camp-capture", CampCapture);'
  ].join('\n');

  var workletReady = new WeakMap();   // context -> Promise<boolean>

  function ensureWorklet(context) {
    if (workletReady.has(context)) return workletReady.get(context);
    var ready;
    if (!context.audioWorklet || typeof context.audioWorklet.addModule !== 'function' ||
        typeof AudioWorkletNode === 'undefined' || typeof Blob === 'undefined' || !window.URL || !URL.createObjectURL) {
      ready = Promise.resolve(false);
    } else {
      var url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }));
      ready = context.audioWorklet.addModule(url).then(function () { return true; }, function () { return false; })
        .then(function (ok) { try { URL.revokeObjectURL(url); } catch (_) {} return ok; });
    }
    workletReady.set(context, ready);
    return ready;
  }

  function attachCapture(context, source, onSamples) {
    // Resolves to { disconnect() }.
    return ensureWorklet(context).then(function (hasWorklet) {
      var mute = context.createGain();
      mute.gain.value = 0;                 // keep the graph "pulled" without echoing the mic
      mute.connect(context.destination);
      var node;
      if (hasWorklet) {
        try {
          node = new AudioWorkletNode(context, 'camp-capture', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: 'explicit' });
          node.port.onmessage = function (event) { onSamples(event.data); };
        } catch (_) { node = null; }
      }
      if (!node) {
        if (!context.createScriptProcessor) { try { mute.disconnect(); } catch (_) {} throw fail('Recording is not supported in this browser.'); }
        node = context.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = function (event) { onSamples(new Float32Array(event.inputBuffer.getChannelData(0))); };
      }
      source.connect(node);
      node.connect(mute);
      return {
        disconnect: function () {
          try { if (node.port) node.port.onmessage = null; } catch (_) {}
          try { node.onaudioprocess = null; } catch (_) {}
          try { source.disconnect(node); } catch (_) {}
          try { node.disconnect(); } catch (_) {}
          try { mute.disconnect(); } catch (_) {}
        }
      };
    });
  }

  function releaseRecording(session) {
    if (!session) return;
    session.active = false;
    try { if (session.capture) session.capture.disconnect(); } catch (_) {}
    try { if (session.source) session.source.disconnect(); } catch (_) {}
    if (session.stream) {
      try { session.stream.getTracks().forEach(function (t) { t.onended = null; t.stop(); }); } catch (_) {}
    }
    session.capture = session.source = session.stream = null;
  }

  // ------------------------------------------------------------- recording API
  function startRecording() {
    // Stop any previous attempt first.
    if (rec) { releaseRecording(rec); rec = null; }
    var token = ++recToken;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(fail(window.isSecureContext === false
        ? 'The microphone needs a secure (https) connection.'
        : 'This browser does not support microphone recording.'));
    }

    var context;
    var resumePromise;
    try {
      // Create + resume synchronously, BEFORE awaiting anything, so iOS keeps the user gesture.
      context = getContext();
      resumePromise = resumeContext();
    } catch (error) { return Promise.reject(error); }

    var session = { token: token, active: false, stream: null, source: null, capture: null, chunks: [], samples: 0,
                    resampler: null, trackEnded: false, capped: false, error: null };
    rec = session;

    var streamPromise = navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });

    return Promise.all([resumePromise, streamPromise]).then(function (results) {
      var stream = results[1];
      session.stream = stream;
      if (token !== recToken || rec !== session) {   // cancelled while the permission prompt was open
        releaseRecording(session);
        throw fail('Recording was cancelled.');
      }
      session.resampler = new Resampler(context.sampleRate);
      stream.getTracks().forEach(function (track) {
        track.onended = function () { session.trackEnded = true; };
      });
      session.source = context.createMediaStreamSource(stream);
      return attachCapture(context, session.source, function (samples) { handleSamples(session, samples); });
    }).then(function (capture) {
      if (token !== recToken || rec !== session) { capture.disconnect(); releaseRecording(session); throw fail('Recording was cancelled.'); }
      session.capture = capture;
      session.active = true;
    }).catch(function (error) {
      // If getUserMedia succeeded but resume failed, make sure the mic is released.
      streamPromise.then(function (s) { try { s.getTracks().forEach(function (t) { t.stop(); }); } catch (_) {} }, function () {});
      releaseRecording(session);
      if (rec === session) rec = null;
      if (error && error.friendly) throw error;
      throw micError(error);
    });
  }

  function handleSamples(session, samples) {
    if (!session.active || session.capped) return;
    var sum = 0;
    for (var i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    emitLevel(rmsToLevel(Math.sqrt(sum / (samples.length || 1))), 'mic');

    var out = [];
    session.resampler.process(samples, out);
    var room = (MAX_RECORD_BYTES / 2) - session.samples;
    if (out.length >= room) {
      out.length = Math.max(0, room);
      session.capped = true;
    }
    if (out.length) {
      session.chunks.push(floatToInt16(out));
      session.samples += out.length;
    }
    if (session.capped) {
      for (var k = 0; k < limitListeners.length; k += 1) { try { limitListeners[k](); } catch (_) {} }
    }
  }

  function stopRecording() {
    var session = rec;
    if (!session || !session.active && !session.chunks.length) {
      return Promise.reject(fail('Recording has not started.'));
    }
    rec = null;
    releaseRecording(session);
    emitLevel(0, 'mic');

    var totalBytes = session.samples * 2;
    if (totalBytes < MIN_BYTES) {
      return Promise.reject(fail(session.trackEnded
        ? 'The microphone stopped unexpectedly. Please try again.'
        : 'That was too short. Hold the button and speak for a moment.'));
    }
    var body = int16ToLeBytes(session.chunks, session.samples);
    session.chunks = [];

    return fetch('/api/transcribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: body
    }).then(function (response) {
      return response.text().then(function (raw) {
        var data = null;
        try { data = raw ? JSON.parse(raw) : null; } catch (_) { data = null; }
        if (response.status === 401) throw fail('Your session expired. Please sign in again.');
        if (data && data.ok && typeof data.text === 'string') {
          var text = data.text.trim();
          if (!text) throw fail("I didn't catch that. Please try again.");
          return text;
        }
        if (data && data.error) throw fail(String(data.error));
        if (response.status === 413) throw fail('That recording was too long.');
        throw fail('I could not process the audio. Please try again.');
      });
    }, function () {
      throw fail('Could not reach CampSage. Check your connection and try again.');
    });
  }

  function cancel() {
    recToken += 1;
    if (rec) { releaseRecording(rec); rec = null; }
    emitLevel(0, 'mic');
  }

  // --------------------------------------------------------------- playback API
  function decodePcm16(base64) {
    var binary;
    try { binary = atob(base64); } catch (_) { throw fail('The audio response was invalid.'); }
    if (!binary.length || binary.length % 2) throw fail('The audio response was invalid.');
    var samples = new Float32Array(binary.length / 2);
    for (var i = 0; i < samples.length; i += 1) {
      var v = binary.charCodeAt(i * 2) | (binary.charCodeAt(i * 2 + 1) << 8);
      if (v & 0x8000) v -= 0x10000;
      samples[i] = v / 32768;
    }
    return samples;
  }

  function endPlayback(session, finished) {
    if (!session || session.done) return;
    session.done = true;
    if (session.timer) { clearInterval(session.timer); session.timer = null; }
    if (session.source) {
      session.source.onended = null;
      try { session.source.stop(); } catch (_) {}
      try { session.source.disconnect(); } catch (_) {}
    }
    try { if (session.analyser) session.analyser.disconnect(); } catch (_) {}
    if (play === session) play = null;
    emitLevel(0, 'speaker');
    session.resolve();   // resolves for both natural end and stopPlayback()
  }

  function playPcm24k(base64) {
    stopPlayback();
    var token = ++playToken;
    var samples;
    try { samples = decodePcm16(base64); } catch (error) { return Promise.reject(error); }

    var resumePromise;
    try { resumePromise = resumeContext(); } catch (error) { return Promise.reject(error); }

    return resumePromise.then(function (context) {
      if (token !== playToken) return undefined;   // stopped while resuming
      return new Promise(function (resolve) {
        var buffer = context.createBuffer(1, samples.length, PLAYBACK_RATE);
        if (buffer.copyToChannel) buffer.copyToChannel(samples, 0);
        else buffer.getChannelData(0).set(samples);

        var source = context.createBufferSource();
        source.buffer = buffer;
        var analyser = context.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        analyser.connect(context.destination);

        var session = { source: source, analyser: analyser, timer: null, done: false, resolve: resolve };
        play = session;
        source.onended = function () { endPlayback(session, true); };

        var data = new Float32Array(analyser.fftSize);
        session.timer = setInterval(function () {
          analyser.getFloatTimeDomainData(data);
          var sum = 0;
          for (var i = 0; i < data.length; i += 1) sum += data[i] * data[i];
          emitLevel(rmsToLevel(Math.sqrt(sum / data.length)), 'speaker');
        }, 50);

        try { source.start(); } catch (_) {
          endPlayback(session, false);
        }
      });
    });
  }

  function stopPlayback() {
    playToken += 1;
    if (play) endPlayback(play, false);
  }

  // ------------------------------------------------------------------ misc API
  function onLevel(callback) {
    if (typeof callback !== 'function') return function () {};
    levelListeners.push(callback);
    return function () {
      var index = levelListeners.indexOf(callback);
      if (index >= 0) levelListeners.splice(index, 1);
    };
  }

  // Optional: called when the recording hits the length cap (capture stops; stopRecording still works).
  function onLimit(callback) {
    if (typeof callback !== 'function') return function () {};
    limitListeners.push(callback);
    return function () {
      var index = limitListeners.indexOf(callback);
      if (index >= 0) limitListeners.splice(index, 1);
    };
  }

  // Optional: call from any tap/click handler (e.g. Send or mode toggle) so iOS allows later audio.
  function unlock() {
    try { return resumeContext().then(function () {}, function () {}); } catch (_) { return Promise.resolve(); }
  }

  window.CampVoice = {
    startRecording: startRecording,
    stopRecording: stopRecording,
    cancel: cancel,
    playPcm24k: playPcm24k,
    stopPlayback: stopPlayback,
    onLevel: onLevel,
    onLimit: onLimit,
    unlock: unlock,
    MAX_RECORD_SECONDS: MAX_RECORD_BYTES / (TARGET_RATE * 2)
  };
})();

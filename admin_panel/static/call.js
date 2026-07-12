/* Moka Sesli Asistan — tarayıcı arama istemcisi.
 *
 * Durum makinesi: idle → connecting → speaking(selamlama) → listening ⇄ thinking ⇄ speaking → ended
 * - VAD: AnalyserNode RMS'i; 600ms gürültü kalibrasyonu, konuşma başlangıcı
 *   eşik*3 (≥150ms), bitişi 700ms sessizlik. Kayıt MediaRecorder (webm/opus).
 * - Barge-in: agent konuşurken eşik*5 (≥300ms) → ses durur, kayıt başlar.
 */

(function () {
  "use strict";

  const cfg = window.CALL_CONFIG || { mode: "inbound", merchantId: "" };

  // --- DOM ---
  const el = {
    avatar: document.getElementById("avatar"),
    wave: document.getElementById("wave"),
    stateLabel: document.getElementById("state-label"),
    timer: document.getElementById("call-timer"),
    setup: document.getElementById("setup"),
    merchantSelect: document.getElementById("merchant-select"),
    btnStart: document.getElementById("btn-start"),
    controls: document.getElementById("controls"),
    btnInterrupt: document.getElementById("btn-interrupt"),
    btnEnd: document.getElementById("btn-end"),
    textToggle: document.getElementById("text-mode-toggle"),
    textForm: document.getElementById("text-input"),
    textField: document.getElementById("text-field"),
    transcript: document.getElementById("transcript"),
    latencyToggle: document.getElementById("latency-toggle"),
    merchantChip: document.getElementById("merchant-chip"),
    merchantLabel: document.getElementById("merchant-label"),
    handoffNote: document.getElementById("handoff-note"),
  };

  // --- durum ---
  let state = "idle";
  let callId = null;
  let stream = null;
  let audioCtx = null;
  let analyser = null;
  let vadTimer = null;
  let recorder = null;
  let chunks = [];
  let agentAudio = null;
  let callStartAt = null;
  let timerInterval = null;
  let handoffDone = false;
  let pendingUpload = false;

  // VAD parametreleri
  const VAD = {
    noiseFloor: 0.006,
    calibrated: false,
    startThr: 0.012,
    bargeThr: 0.025,
    speechStartMs: 150,
    speechEndMs: 700,
    bargeSustainMs: 300,
    minSpeechMs: 300,
    maxTurnMs: 20000,
  };
  let speechAboveSince = 0;
  let silenceSince = 0;
  let recordingStartedAt = 0;
  let bargeAboveSince = 0;

  const STATE_LABELS = {
    idle: "Aramayı başlatmak için dokunun",
    connecting: "Bağlanıyor…",
    speaking: "Ada konuşuyor…",
    listening: "Sizi dinliyorum…",
    thinking: "Bakıyorum…",
    ended: "Arama sona erdi",
  };

  function setState(next) {
    state = next;
    el.avatar.parentElement.classList.remove(
      "state-listening", "state-thinking", "state-speaking"
    );
    if (next === "listening") el.avatar.parentElement.classList.add("state-listening");
    if (next === "thinking") el.avatar.parentElement.classList.add("state-thinking");
    if (next === "speaking") el.avatar.parentElement.classList.add("state-speaking");
    el.wave.hidden = next !== "speaking";
    el.stateLabel.textContent = STATE_LABELS[next] || "";
    el.btnInterrupt.hidden = next !== "speaking" || !stream;
  }

  // --- transkript ---
  function addBubble(role, text, meta) {
    const bubble = document.createElement("div");
    bubble.className = "bubble " + role;
    bubble.textContent = text;
    if (meta && (meta.tool || meta.latency)) {
      const metaRow = document.createElement("div");
      metaRow.className = "meta";
      if (meta.tool) {
        const badge = document.createElement("span");
        badge.className = "tool-badge";
        badge.textContent = "🔧 " + meta.tool;
        metaRow.appendChild(badge);
      }
      if (meta.latency && el.latencyToggle.checked) {
        const lat = document.createElement("span");
        lat.className = "latency-badge";
        lat.textContent =
          "stt " + meta.latency.stt + "ms · llm " + meta.latency.llm +
          "ms · tts " + meta.latency.tts + "ms";
        metaRow.appendChild(lat);
      }
      bubble.appendChild(metaRow);
    }
    el.transcript.appendChild(bubble);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  function addSysNote(text) {
    const note = document.createElement("div");
    note.className = "sys-note";
    note.textContent = text;
    el.transcript.appendChild(note);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  // --- zamanlayici ---
  function startTimer() {
    callStartAt = Date.now();
    el.timer.hidden = false;
    timerInterval = setInterval(function () {
      const s = Math.floor((Date.now() - callStartAt) / 1000);
      el.timer.textContent =
        String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    }, 500);
  }

  // --- ses cikisi ---
  function playAgentAudio(url, onFinished) {
    if (!url) { onFinished(); return; }
    agentAudio = new Audio(url);
    agentAudio.onended = function () { agentAudio = null; onFinished(); };
    agentAudio.onerror = function () { agentAudio = null; onFinished(); };
    setState("speaking");
    agentAudio.play().catch(function () { agentAudio = null; onFinished(); });
  }

  function stopAgentAudio() {
    if (agentAudio) {
      agentAudio.onended = null;
      agentAudio.pause();
      agentAudio = null;
    }
  }

  // --- mikrofon + VAD ---
  async function initMic() {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaStreamSource(stream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
  }

  function currentRms() {
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / data.length);
  }

  async function calibrateNoise() {
    const samples = [];
    const t0 = performance.now();
    while (performance.now() - t0 < 600) {
      samples.push(currentRms());
      await new Promise(function (r) { setTimeout(r, 50); });
    }
    const avg = samples.reduce(function (a, b) { return a + b; }, 0) / (samples.length || 1);
    VAD.noiseFloor = avg;
    VAD.startThr = Math.max(avg * 3, 0.012);
    VAD.bargeThr = Math.max(avg * 5, 0.025);
    VAD.calibrated = true;
  }

  function startVadLoop() {
    stopVadLoop();
    vadTimer = setInterval(vadTick, 50);
  }
  function stopVadLoop() {
    if (vadTimer) { clearInterval(vadTimer); vadTimer = null; }
  }

  function vadTick() {
    if (!analyser || pendingUpload) return;
    const rms = currentRms();
    const now = performance.now();

    if (state === "listening") {
      if (!recorder || recorder.state !== "recording") {
        // konusma baslangici bekleniyor
        if (rms > VAD.startThr) {
          if (!speechAboveSince) speechAboveSince = now;
          if (now - speechAboveSince >= VAD.speechStartMs) startRecording();
        } else {
          speechAboveSince = 0;
        }
      } else {
        // kayit suruyor: bitis / max sure kontrolu
        if (rms < VAD.startThr) {
          if (!silenceSince) silenceSince = now;
          const spoke = now - recordingStartedAt - (now - silenceSince);
          if (now - silenceSince >= VAD.speechEndMs && spoke >= VAD.minSpeechMs) {
            stopRecordingAndSend();
          }
        } else {
          silenceSince = 0;
        }
        if (now - recordingStartedAt > VAD.maxTurnMs) stopRecordingAndSend();
      }
    } else if (state === "speaking") {
      // barge-in: kullanici soze girdi mi?
      if (rms > VAD.bargeThr) {
        if (!bargeAboveSince) bargeAboveSince = now;
        if (now - bargeAboveSince >= VAD.bargeSustainMs) {
          bargeAboveSince = 0;
          interruptAgent();
        }
      } else {
        bargeAboveSince = 0;
      }
    }
  }

  function startRecording() {
    speechAboveSince = 0;
    silenceSince = 0;
    chunks = [];
    let options = { mimeType: "audio/webm;codecs=opus" };
    try {
      recorder = new MediaRecorder(stream, options);
    } catch (e) {
      recorder = new MediaRecorder(stream);
    }
    recorder.ondataavailable = function (evt) {
      if (evt.data && evt.data.size > 0) chunks.push(evt.data);
    };
    recorder.start();
    recordingStartedAt = performance.now();
  }

  function stopRecordingAndSend() {
    if (!recorder || recorder.state !== "recording") return;
    silenceSince = 0;
    pendingUpload = true;
    recorder.onstop = function () {
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      chunks = [];
      recorder = null;
      sendAudioTurn(blob);
    };
    recorder.stop();
  }

  function interruptAgent() {
    stopAgentAudio();
    addSysNote("↩ söze girildi");
    setState("listening");
    startRecording();
  }

  // --- sunucu iletisimi ---
  async function sendAudioTurn(blob) {
    setState("thinking");
    const form = new FormData();
    form.append("call_id", callId);
    form.append("audio", blob, "turn.webm");
    try {
      const resp = await fetch("/call/turn", { method: "POST", body: form });
      const data = await resp.json();
      pendingUpload = false;
      handleTurnResponse(data);
    } catch (err) {
      pendingUpload = false;
      addSysNote("⚠ bağlantı hatası, tekrar dinliyorum");
      setState("listening");
    }
  }

  async function sendTextTurn(text) {
    setState("thinking");
    const form = new FormData();
    form.append("call_id", callId);
    form.append("text", text);
    addBubble("user", text);
    try {
      const resp = await fetch("/call/turn", { method: "POST", body: form });
      const data = await resp.json();
      handleTurnResponse(data, { skipUserBubble: true });
    } catch (err) {
      addSysNote("⚠ bağlantı hatası");
      setState("listening");
    }
  }

  function handleTurnResponse(data, opts) {
    opts = opts || {};
    if (state === "ended") return;  // kapatilan cagriya gec gelen yanit yok sayilir
    if (data.error) {
      addSysNote("⚠ " + data.error);
      setState("listening");
      return;
    }
    if (data.empty) {
      setState("listening");
      return;
    }
    if (data.transcript && !opts.skipUserBubble) {
      addBubble("user", data.transcript);
    }
    addBubble("agent", data.reply_text, {
      tool: data.tool,
      latency: data.latency_ms,
    });

    if (data.handoff && !handoffDone) {
      handoffDone = true;
      el.handoffNote.hidden = false;
    }

    playAgentAudio(data.audio_url, function () {
      if (handoffDone) {
        endCall("handoff");
      } else {
        setState("listening");
      }
    });
  }

  // --- cagri yasam dongusu ---
  async function startCall() {
    el.btnStart.disabled = true;
    el.btnStart.textContent = "Bağlanıyor…";
    setState("connecting");

    let micOk = true;
    try {
      await initMic();
    } catch (err) {
      micOk = false;
      addSysNote("🎙 mikrofona erişilemedi — yazarak test modu aktif");
    }

    const merchantId = el.merchantSelect.value || cfg.merchantId;
    try {
      const resp = await fetch("/call/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: cfg.mode, merchant_id: merchantId }),
      });
      const data = await resp.json();
      callId = data.call_id;

      if (data.merchant) {
        el.merchantChip.hidden = false;
        el.merchantLabel.textContent =
          data.merchant.business_name + " • " + data.merchant.merchant_id;
      }

      el.setup.hidden = true;
      el.controls.hidden = false;
      startTimer();

      if (micOk) {
        await calibrateNoise();
        startVadLoop();
      } else {
        el.textToggle.checked = true;
        el.textForm.hidden = false;
      }

      addBubble("agent", data.reply_text, { latency: data.latency_ms });
      playAgentAudio(data.audio_url, function () { setState("listening"); });
    } catch (err) {
      addSysNote("⚠ arama başlatılamadı: " + err);
      setState("idle");
      el.btnStart.disabled = false;
      el.btnStart.textContent = "Aramayı Başlat";
    }
  }

  async function endCall(outcome) {
    stopVadLoop();
    stopAgentAudio();
    if (recorder && recorder.state === "recording") {
      recorder.onstop = null;
      recorder.stop();
    }
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    if (timerInterval) clearInterval(timerInterval);
    setState("ended");
    el.controls.hidden = true;
    el.textForm.hidden = true;
    addSysNote("— arama sona erdi —");
    try {
      const resp = await fetch("/call/end", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_id: callId, mode: cfg.mode, outcome: outcome || "ended" }),
      });
      const data = await resp.json();
      if (data.summary) addSysNote("📋 Çağrı özeti: " + data.summary);
    } catch (e) { /* sessiz */ }
    // yeni arama icin kurulum panelini geri getir
    setTimeout(function () {
      el.setup.hidden = false;
      el.btnStart.disabled = false;
      el.btnStart.textContent = cfg.mode === "outbound" ? "AI Aramayı Başlatsın" : "Aramayı Başlat";
      el.handoffNote.hidden = true;
      handoffDone = false;
      callId = null;
    }, 1200);
  }

  // --- olaylar ---
  el.btnStart.addEventListener("click", startCall);
  el.btnEnd.addEventListener("click", function () { endCall("ended"); });
  el.btnInterrupt.addEventListener("click", interruptAgent);

  el.textToggle.addEventListener("change", function () {
    el.textForm.hidden = !el.textToggle.checked;
    if (!el.textForm.hidden) el.textField.focus();
  });

  el.textForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    const text = el.textField.value.trim();
    if (!text || !callId || state === "thinking") return;  // cift gonderim guard
    // VAD'in baslattigi bir kayit varsa iptal et: text turu esnasinda
    // arka planda kayit donmesin, upload tetiklenmesin.
    if (recorder && recorder.state === "recording") {
      recorder.onstop = null;
      recorder.stop();
      recorder = null;
      chunks = [];
    }
    pendingUpload = false;
    el.textField.value = "";
    stopAgentAudio();
    sendTextTurn(text);
  });

  setState("idle");
})();

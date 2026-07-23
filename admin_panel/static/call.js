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
    voiceSelect: document.getElementById("voice-select"),
    btnVoicePreview: document.getElementById("btn-voice-preview"),
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
    transcriptToggle: document.getElementById("transcript-toggle"),
    phoneClock: document.getElementById("phone-clock"),
    phoneTimer: document.getElementById("phone-timer"),
    phoneMic: document.getElementById("phone-mic"),
    phoneCaption: document.getElementById("phone-caption"),
    phoneEnd: document.getElementById("phone-end"),
    phoneScreen: document.querySelector(".phone-screen"),
  };

  // Telefon saat gostergesi (statuko cubugu)
  if (el.phoneClock) {
    var _now = new Date();
    el.phoneClock.textContent =
      String(_now.getHours()).padStart(2, "0") + ":" + String(_now.getMinutes()).padStart(2, "0");
  }

  function phoneSetTimer(text) { if (el.phoneTimer) el.phoneTimer.textContent = text; }
  function phoneShowMic(on) { if (el.phoneMic) el.phoneMic.hidden = !on; }
  function phoneShowCaption(text) {
    if (!el.phoneCaption) return;
    if (text) { el.phoneCaption.textContent = "\u201C" + text + "\u201D"; el.phoneCaption.hidden = false; }
  }

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
  let previewAudio = null;  // ses onizlemesi — arama baslarken durdurulmali

  function stopPreview() {
    if (previewAudio) {
      previewAudio.pause();
      previewAudio = null;
    }
    if (el.btnVoicePreview) {
      el.btnVoicePreview.disabled = false;
      el.btnVoicePreview.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>';
    }
  }

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
    connecting: "Moka Destek Hattı aranıyor…",
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
    const chain = (meta && meta.tools && meta.tools.length)
      ? meta.tools
      : (meta && meta.tool ? [meta.tool] : []);

    if (meta && (chain.length || meta.latency)) {
      const metaRow = document.createElement("div");
      metaRow.className = "meta";

      // Cok adimli agent loop: bir turda birden fazla arac calisabilir.
      // Zincirin TAMAMI gosterilir — ajanin dusundugunun gorunur kanitidir.
      chain.forEach(function (name, index) {
        if (index > 0) {
          const arrow = document.createElement("span");
          arrow.className = "tool-arrow";
          arrow.textContent = "→";
          metaRow.appendChild(arrow);
        }
        const badge = document.createElement("span");
        badge.className = "tool-badge";
        badge.textContent = (cfg.toolLabels && cfg.toolLabels[name]) || name;
        badge.title = name;
        metaRow.appendChild(badge);
      });

      if (meta.iterations > 1) {
        const rounds = document.createElement("span");
        rounds.className = "iteration-badge";
        rounds.textContent = meta.iterations + " tur";
        rounds.title = "Model araç sonucunu görüp yeniden karar verdi";
        metaRow.appendChild(rounds);
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
      const label =
        String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
      el.timer.textContent = label;
      phoneSetTimer(label);
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

  // Akan cevapta ses cumle cumle gelir ('audio' olaylari). Kuyruk sirayla
  // calar; 'done' gelince finishAudioQueue son parcanin bitisine kancalanir.
  let audioQueue = [];
  let audioQueueDone = false;
  let audioQueueFinish = null;
  let audioSegments = 0;   // bu turda kac parca ses geldi (0 = eski tek parca yol)
  let turnAbort = null;    // suren akis istegi; barge-in'de iptal edilir

  function resetAudioQueue() {
    audioQueue = [];
    audioQueueDone = false;
    audioQueueFinish = null;
  }

  function enqueueAgentAudio(url) {
    audioQueue.push(url);
    if (!agentAudio) playNextSegment();
  }

  function playNextSegment() {
    const next = audioQueue.shift();
    if (next === undefined) {
      // kuyruk bos: ya siradaki cumle henuz sentezleniyor ya da tur bitti
      if (audioQueueDone && audioQueueFinish) {
        const cb = audioQueueFinish;
        audioQueueFinish = null;
        cb();
      }
      return;
    }
    setState("speaking");
    agentAudio = new Audio(next);
    agentAudio.onended = function () { agentAudio = null; playNextSegment(); };
    agentAudio.onerror = function () { agentAudio = null; playNextSegment(); };
    agentAudio.play().catch(function () { agentAudio = null; playNextSegment(); });
  }

  function finishAudioQueue(onFinished) {
    audioQueueDone = true;
    audioQueueFinish = onFinished;
    if (!agentAudio && audioQueue.length === 0) {
      audioQueueFinish = null;
      onFinished();
    }
  }

  function stopAgentAudio() {
    resetAudioQueue();
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
        // Kayit ANINDA baslar: bekleme suresi kelimenin basini kirpiyordu
        // ("Merhaba Ada" -> "...da" -> yanlis transkript). Yanlis alarmlar
        // asagida yerel olarak cope atilir, sunucuya hic gitmez.
        if (rms > VAD.startThr) startRecording();
      } else {
        // kayit suruyor: bitis / max sure / yanlis alarm kontrolu
        if (rms < VAD.startThr) {
          if (!silenceSince) silenceSince = now;
          if (now - silenceSince >= VAD.speechEndMs) {
            const spoke = silenceSince - recordingStartedAt;
            if (spoke >= VAD.minSpeechMs) stopRecordingAndSend();
            else discardRecording();  // kisa gurultu — upload yok
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
    phoneShowMic(true);
  }

  function discardRecording() {
    phoneShowMic(false);
    if (!recorder) return;
    recorder.onstop = null;
    try { recorder.stop(); } catch (e) { /* zaten durmus */ }
    recorder = null;
    chunks = [];
    silenceSince = 0;
  }

  function stopRecordingAndSend() {
    if (!recorder || recorder.state !== "recording") return;
    silenceSince = 0;
    pendingUpload = true;
    phoneShowMic(false);
    recorder.onstop = function () {
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      chunks = [];
      recorder = null;
      sendAudioTurn(blob);
    };
    recorder.stop();
  }

  function interruptAgent() {
    // Akis suruyorsa iptal et: sunucu kalan cumleleri uretmeyi/seslendirmeyi
    // birakir, sadece tarayicidaki ses susmus olmaz.
    if (turnAbort) { turnAbort.abort(); turnAbort = null; }
    stopAgentAudio();
    addSysNote("— söze girildi —");
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
      // Kullanicinin ne dedigi STT'den sonra belli olur; balonu sunucu yazar.
      await streamTurn(form, { skipUserBubble: false });
    } catch (err) {
      addSysNote("Bağlantı hatası — tekrar dinliyorum.");
      setState("listening");
    } finally {
      pendingUpload = false;
    }
  }

  async function sendTextTurn(text) {
    setState("thinking");
    const form = new FormData();
    form.append("call_id", callId);
    form.append("text", text);
    addBubble("user", text);
    try {
      await streamTurn(form, { skipUserBubble: true });
    } catch (err) {
      addSysNote("Bağlantı hatası.");
      setState("listening");
    }
  }

  // --- akan cevap ---------------------------------------------------------
  //
  // Cevap tek blok halinde degil, uretildikce yazilir. Sunucu tarafi metni
  // yalnizca "kararli" hale geldikce yolluyor, bu yuzden ekrana yazilan
  // hicbir sey sonradan degismez (bkz. orchestrator._stream_polished).
  async function streamTurn(form, opts) {
    opts = opts || {};
    resetAudioQueue();
    audioSegments = 0;
    turnAbort = new AbortController();
    let response;
    try {
      response = await fetch("/call/turn/stream", {
        method: "POST", body: form, signal: turnAbort.signal,
      });
    } catch (err) {
      turnAbort = null;
      if (err.name === "AbortError") return;  // soze girildi — sessizce cik
      throw err;
    }
    // Sessizlik/hata durumunda sunucu duz JSON doner (akis baslamaz).
    if (!(response.headers.get("content-type") || "").includes("text/event-stream")) {
      turnAbort = null;
      handleTurnResponse(await response.json(), opts);
      return;
    }
    if (!response.ok || !response.body) { turnAbort = null; throw new Error("stream basarisiz"); }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let bubble = null;
    let streamed = "";

    function ensureBubble() {
      if (!bubble) {
        bubble = document.createElement("div");
        bubble.className = "bubble agent streaming";
        el.transcript.appendChild(bubble);
      }
      return bubble;
    }

    try {
      for (;;) {
        let value, done;
        try {
          ({ value, done } = await reader.read());
        } catch (err) {
          if (err.name === "AbortError") {
            // barge-in: yazilani koru, durumu interruptAgent yonetti
            if (bubble) bubble.classList.remove("streaming");
            return;
          }
          throw err;
        }
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE olaylari bos satirla ayrilir.
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          let name = null, payload = null;
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) {
              try { payload = JSON.parse(line.slice(5).trim()); } catch (e) { payload = null; }
            }
          }
          if (!name || !payload) continue;

          if (name === "tool") {
            // Planlama uzun surebilir; kullanici sessizce beklemesin.
            const label = (cfg.toolLabels && cfg.toolLabels[payload.name]) || payload.name;
            el.stateLabel.textContent = label + "…";
          } else if (name === "delta") {
            if (state === "ended") return;
            if (!bubble) setState("speaking");   // yazmaya basladik
            streamed += payload.text;
            ensureBubble().textContent = streamed;
            el.transcript.scrollTop = el.transcript.scrollHeight;
          } else if (name === "audio") {
            // Cumle sesi hazir: kuyruga ekle, ilk parca hemen calmaya baslar.
            if (state === "ended") return;
            audioSegments += 1;
            enqueueAgentAudio(payload.url);
          } else if (name === "error") {
            addSysNote(payload.detail || "Cevap üretilemedi.");
            setState("listening");
            return;
          } else if (name === "done") {
            if (bubble) bubble.remove();       // meta satirli nihai balonla degistir
            handleTurnResponse(payload, { skipUserBubble: opts.skipUserBubble });
            return;
          }
        }
      }

      // Akis 'done' gelmeden koptuysa yazilani koru.
      if (bubble) bubble.classList.remove("streaming");
      setState("listening");
    } finally {
      turnAbort = null;
    }
  }

  function handleTurnResponse(data, opts) {
    opts = opts || {};
    if (state === "ended") return;  // kapatilan cagriya gec gelen yanit yok sayilir
    if (data.error) {
      addSysNote(data.error);
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
    if (data.transcript) phoneShowCaption(data.transcript);
    addBubble("agent", data.reply_text, {
      tool: data.tool,
      tools: data.tools,
      iterations: data.iterations,
      latency: data.latency_ms,
    });

    if (data.handoff && !handoffDone) {
      handoffDone = true;
      el.handoffNote.hidden = false;
    }

    const finishTurn = function () {
      if (handoffDone) {
        endCall("handoff");
      } else {
        setState("listening");
      }
    };
    if (audioSegments > 0) {
      // Ses zaten cumle cumle calindi/caliyor; son parca bitince tur kapanir.
      finishAudioQueue(finishTurn);
    } else {
      playAgentAudio(data.audio_url, finishTurn);
    }
  }

  // --- cagri yasam dongusu ---
  async function startCall() {
    stopPreview();  // onizleme selamlamayla cakismasin, VAD kalibrasyonunu bozmasin
    phoneSetTimer(cfg.mode === "outbound" ? "gelen arama…" : "aranıyor…");
    el.btnStart.disabled = true;
    el.btnStart.textContent = "Bağlanıyor…";
    setState("connecting");

    let micOk = true;
    try {
      await initMic();
    } catch (err) {
      micOk = false;
      addSysNote("Mikrofona erişilemedi — yazarak test modu etkin.");
    }

    const merchantId = el.merchantSelect.value || cfg.merchantId;
    try {
      const resp = await fetch("/call/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: cfg.mode,
          merchant_id: merchantId,
          voice_id: el.voiceSelect ? el.voiceSelect.value : null,
        }),
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

      if (el.phoneScreen) el.phoneScreen.classList.add("in-call");
      phoneSetTimer("00:00");
      addBubble("agent", data.reply_text, { latency: data.latency_ms });
      playAgentAudio(data.audio_url, function () { setState("listening"); });
    } catch (err) {
      addSysNote("Arama başlatılamadı: " + err);
      setState("idle");
      el.btnStart.disabled = false;
      el.btnStart.textContent = "Aramayı Başlat";
    }
  }

  async function endCall(outcome) {
    stopVadLoop();
    if (turnAbort) { turnAbort.abort(); turnAbort = null; }
    stopAgentAudio();
    if (recorder && recorder.state === "recording") {
      recorder.onstop = null;
      recorder.stop();
    }
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    if (timerInterval) clearInterval(timerInterval);
    setState("ended");
    phoneSetTimer("arama sona erdi");
    phoneShowMic(false);
    if (el.phoneScreen) el.phoneScreen.classList.remove("in-call");
    el.controls.hidden = true;
    el.textForm.hidden = true;
    el.textToggle.checked = false;
    addSysNote("— arama sona erdi —");
    try {
      const resp = await fetch("/call/end", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_id: callId, mode: cfg.mode, outcome: outcome || "ended" }),
      });
      const data = await resp.json();
      if (data.summary) addSysNote("Çağrı özeti: " + data.summary);
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

  // Ses onizleme: adayin kisa tanitim cumlesi calinir
  if (el.btnVoicePreview && el.voiceSelect) {
    el.btnVoicePreview.addEventListener("click", function () {
      if (previewAudio) { stopPreview(); return; }  // ikinci tik = durdur
      el.btnVoicePreview.disabled = true;
      el.btnVoicePreview.textContent = "…";
      previewAudio = new Audio("/call/voice-preview/" + el.voiceSelect.value);
      previewAudio.onended = stopPreview;
      previewAudio.onerror = stopPreview;
      previewAudio.play()
        .then(function () { el.btnVoicePreview.disabled = false; el.btnVoicePreview.textContent = "II"; })
        .catch(stopPreview);
    });
  }
  el.btnEnd.addEventListener("click", function () { endCall("ended"); });
  if (el.phoneEnd) el.phoneEnd.addEventListener("click", function () {
    if (state !== "idle" && state !== "ended") endCall("ended");
  });
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
    phoneShowCaption(text);
    sendTextTurn(text);
  });

  // Transkript panelini ac/kapa (tercih hatirlanir)
  if (el.transcriptToggle) {
    var shell = document.querySelector(".call-shell");
    function applyTranscript(visible) {
      shell.classList.toggle("no-transcript", !visible);
      el.transcriptToggle.classList.toggle("active", visible);
      try { localStorage.setItem("call.transcript", visible ? "1" : "0"); } catch (e) {}
    }
    var saved = "1";
    try { saved = localStorage.getItem("call.transcript") || "1"; } catch (e) {}
    applyTranscript(saved === "1");
    el.transcriptToggle.addEventListener("click", function () {
      applyTranscript(shell.classList.contains("no-transcript"));
    });
  }

  setState("idle");

  // Ekran goruntusu modu (?snapshot=1): gercek bir gorusmeyi statik olarak
  // yeniden kurar — README/sunum gorselleri icin. Demo akisini etkilemez.
  (function () {
    if (new URLSearchParams(window.location.search).get("snapshot") !== "1") return;
    el.setup.hidden = true;
    el.controls.hidden = false;
    el.merchantChip.hidden = false;
    el.merchantLabel.textContent = "Demiray Kuruyemiş • M-1001";
    el.timer.hidden = false;
    el.timer.textContent = "00:41";
    phoneSetTimer("00:41");
    if (el.phoneScreen) el.phoneScreen.classList.add("in-call");
    phoneShowCaption("Çok adım teşekkür ederim. İyi akşamlar.");
    setState("listening");
    el.stateLabel.textContent = "Sizi dinliyorum…";
    addBubble("agent", "Moka'ya hoş geldiniz Mehmet Bey, ben Ada. Size nasıl yardımcı olabilirim?");
    addBubble("user", "Merhaba Ada, nasılsın?");
    addBubble("agent", "İyiyim, teşekkür ederim. Size nasıl yardımcı olabilirim?", { tool: "answer_general" });
    addBubble("user", "Benim dün param yatmadığı nedenini öğrenebilir miyim?");
    addBubble("agent", "Dünkü net hakedişiniz 44 bin 104 lira, yarın saat 10:00'de sonu 44 17 ile biten IBAN hesabınıza gönderilecek. Başka bir konuda yardımcı olabilir miyim?", { tool: "get_settlement_status" });
    addBubble("user", "Çok adım teşekkür ederim. İyi akşamlar.");
    addBubble("agent", "Rica ederim, iyi akşamlar.", { tool: "answer_general" });
  })();
})();

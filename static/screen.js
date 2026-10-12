'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="riverlens-token"]').content;
const SAMPLE_MS = 500;
const HISTORY_MS = 30000;
let stream = null, source = null, sourceReady = false, imageURL = null;
let frames = [], revision = 0, loadTicket = 0, sharing = false;
let busy = false, controller = null, sampleTimer = null, autoTimer = null;
let aiReady = false, callsAvailable = false, lastSentImage = null;
let reportImage = null, reportWasLive = false, reportExpiresAt = 0, expiryTimer = null;

const fastMode = () => $('analysis-mode').value === 'fast';
function deadlineMs() {
  const value = Number($('deadline').value);
  return [2000, 3000, 5000, 8000, 15000, 30000].includes(value) ? value : 5000;
}

function clearAdvice(reason) {
  clearTimeout(expiryTimer);
  expiryTimer = null;
  reportImage = null;
  $('result').hidden = true;
  $('result-empty').hidden = false;
  $('result-note').textContent = reason;
  $('confidence').textContent = 'Da aggiornare';
}

function message(text, error = false) {
  $('operation-status').textContent = text;
  $('operation-status').classList.toggle('error', error);
}

function controls() {
  const allowed = aiReady && callsAvailable && sourceReady && $('consent').checked;
  $('analyze').disabled = !allowed || busy;
  $('auto').disabled = !allowed || source !== 'screen';
  $('stop').disabled = !source && !sharing;
  $('share').disabled = sharing;
  $('result-panel').setAttribute('aria-busy', String(busy));
}

function pauseAutomatic() {
  $('auto').checked = false;
  clearTimeout(autoTimer);
  autoTimer = null;
}

function invalidate() {
  revision++;
  controller?.abort();
  clearTimeout(autoTimer);
  autoTimer = null;
  frames = [];
  lastSentImage = null;
  clearAdvice('In attesa di un’analisi sul contesto aggiornato.');
  $('confidence').textContent = 'In attesa';
  $('frame-preview').hidden = true;
  $('frame-preview').removeAttribute('src');
  $('frame-info').textContent = 'In attesa di un nuovo fotogramma.';
}

function stopSource() {
  loadTicket++;
  pauseAutomatic();
  clearTimeout(sampleTimer);
  sampleTimer = null;
  invalidate();
  // stop() does not emit "ended": clean up explicitly here as well.
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
  $('video').pause();
  $('video').srcObject = null;
  $('video').hidden = true;
  $('image').hidden = true;
  $('image').removeAttribute('src');
  if (imageURL) URL.revokeObjectURL(imageURL);
  imageURL = null;
  source = null;
  sourceReady = false;
  $('placeholder').hidden = false;
  $('source-badge').textContent = 'Nessuna sorgente';
  $('capture-status').textContent = 'Cattura interrotta. I fotogrammi locali sono stati eliminati.';
  controls();
}

function resetCropValues() {
  $('crop-x').value = '0'; $('crop-y').value = '0';
  $('crop-w').value = '100'; $('crop-h').value = '100';
}

function cropBounds() {
  const values = ['crop-x', 'crop-y', 'crop-w', 'crop-h'].map(id => {
    const raw = $(id).value;
    return raw.trim() === '' ? NaN : Number(raw);
  });
  const [x, y, w, h] = values;
  if (values.some(v => !Number.isFinite(v)) || x < 0 || y < 0 || w < 1 || h < 1 || x + w > 100 || y + h > 100) {
    throw new Error('Ritaglio non valido: sinistra + larghezza e alto + altezza devono essere al massimo 100%.');
  }
  return {x: x / 100, y: y / 100, w: w / 100, h: h / 100};
}

function captureFrame() {
  if (!sourceReady) throw new Error('Attendi che la sorgente sia pronta.');
  const live = source === 'screen';
  const element = live ? $('video') : $('image');
  const track = stream?.getVideoTracks()[0];
  if (live && (!track || track.readyState !== 'live' || track.muted || element.readyState < 2)) {
    throw new Error('La finestra non sta fornendo immagini. Rendila visibile o ricollega lo schermo.');
  }
  const width = live ? element.videoWidth : element.naturalWidth;
  const height = live ? element.videoHeight : element.naturalHeight;
  if (!width || !height) throw new Error('L’immagine non è ancora disponibile.');
  const crop = cropBounds();
  const sw = width * crop.w, sh = height * crop.h;
  let scale = Math.min(1, (fastMode() ? 1280 : 1600) / Math.max(sw, sh));
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', {alpha: false});
  let image = '';
  // Bound each normalized frame to 1.2 MB, so three frames fit the API's 4 MB total.
  for (let attempt = 0; attempt < 6; attempt++) {
    canvas.width = Math.max(1, Math.round(sw * scale));
    canvas.height = Math.max(1, Math.round(sh * scale));
    context.drawImage(element, width * crop.x, height * crop.y, sw, sh, 0, 0, canvas.width, canvas.height);
    image = canvas.toDataURL('image/jpeg', fastMode() ? 0.82 : 0.86);
    if (image.length < 1_600_000) break;
    scale *= 0.8;
  }
  if (!image.startsWith('data:image/jpeg;base64,') || image.length >= 1_600_000) {
    throw new Error('Immagine troppo grande. Ritaglia un’area più piccola.');
  }
  const now = Date.now();
  const frame = {image, captured_at: new Date(now).toISOString()};
  frames = frames.filter(f => now - Date.parse(f.captured_at) <= HISTORY_MS);
  const last = frames.at(-1);
  if (last && Date.parse(last.captured_at) >= now) frame.captured_at = new Date(Date.parse(last.captured_at) + 1).toISOString();
  // Keep distinct frames only. Refresh the timestamp when the source is unchanged.
  if (last?.image === image) frames[frames.length - 1] = frame;
  else {
    frames.push(frame);
  }
  frames = frames.slice(-3);
  $('frame-preview').src = image;
  $('frame-preview').hidden = false;
  $('frame-info').textContent = `${canvas.width} × ${canvas.height} · ${frames.length} fotogramm${frames.length === 1 ? 'a' : 'i'} in memoria · ${new Date(now).toLocaleTimeString('it-IT')}`;
  markStale();
  return frame;
}

function markStale() {
  if (!reportImage) return;
  if (reportImage !== frames.at(-1)?.image || (reportWasLive && performance.now() >= reportExpiresAt)) {
    clearAdvice('Consiglio ritirato: immagine cambiata o tempo disponibile scaduto.');
  }
}

function sampleLoop() {
  clearTimeout(sampleTimer);
  if (source !== 'screen' || !sourceReady) return;
  try {
    captureFrame();
    scheduleAutomatic();
  } catch (error) {
    pauseAutomatic();
    invalidate();
    message(error.message, true);
  }
  sampleTimer = setTimeout(sampleLoop, SAMPLE_MS);
}

async function shareScreen() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    message('Condivisione schermo non disponibile in questo browser. Carica uno screenshot oppure apri RiverLens in un browser desktop compatibile.', true);
    return;
  }
  stopSource();
  resetCropValues();
  const ticket = loadTicket;
  sharing = true;
  controls();
  try {
    // Called directly from a click to preserve transient user activation.
    const selected = await navigator.mediaDevices.getDisplayMedia({
      video: {frameRate: {ideal: 5, max: 10}}, audio: false,
      selfBrowserSurface: 'exclude', surfaceSwitching: 'exclude',
    });
    if (ticket !== loadTicket) {
      selected.getTracks().forEach(track => track.stop());
      return;
    }
    stream = selected;
    source = 'screen';
    const track = selected.getVideoTracks()[0];
    track.addEventListener('ended', () => {
      if (stream !== selected) return;
      stopSource();
      message('Condivisione terminata. Le analisi automatiche sono ferme.');
    });
    track.addEventListener('mute', () => {
      if (stream !== selected) return;
      pauseAutomatic();
      invalidate();
      message('Sorgente sospesa: analisi automatica fermata. Verifica la finestra condivisa.', true);
    });
    $('video').srcObject = selected;
    await $('video').play();
    if (ticket !== loadTicket) return;
    sourceReady = true;
    $('video').hidden = false;
    $('placeholder').hidden = true;
    $('source-badge').textContent = 'Schermo collegato';
    $('capture-status').textContent = 'Cattura locale ogni 0,5 s. Audio escluso. Scegli la finestra del gioco per evitare l’effetto specchio.';
    sampleLoop();
    message('Schermo pronto. Puoi analizzare ora o attivare l’analisi automatica.');
  } catch (error) {
    if (ticket !== loadTicket) return;
    stopSource();
    message(error.name === 'NotAllowedError'
      ? 'Condivisione annullata o permesso negato. Su macOS verifica anche il permesso di registrazione schermo del browser.'
      : 'Impossibile leggere lo schermo. Riprova o carica uno screenshot.', true);
  } finally {
    sharing = false;
    controls();
  }
}

async function loadFile(file) {
  if (!file) return;
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 12_000_000) {
    message('Scegli un’immagine PNG, JPEG o WebP fino a 12 MB.', true);
    return;
  }
  stopSource();
  resetCropValues();
  const ticket = loadTicket;
  const url = URL.createObjectURL(file);
  imageURL = url;
  // Decode a separate element: an earlier load must not replace a newer selection.
  const decoded = new Image();
  decoded.src = url;
  try {
    await decoded.decode();
    if (ticket !== loadTicket) return;
    if (decoded.naturalWidth * decoded.naturalHeight > 40_000_000) throw new Error('Usa un’immagine fino a 40 megapixel.');
    $('image').src = url;
    await $('image').decode();
    if (ticket !== loadTicket) return;
    source = 'file';
    sourceReady = true;
    $('image').hidden = false;
    $('placeholder').hidden = true;
    $('source-badge').textContent = 'Screenshot caricato';
    $('capture-status').textContent = 'Immagine caricata in memoria. Il timestamp indica il caricamento, non l’ora originale del gioco.';
    captureFrame();
    message('Screenshot pronto. Autorizza l’invio e premi “Analizza adesso”.');
  } catch (error) {
    if (ticket !== loadTicket) return;
    stopSource();
    message(error.message.startsWith('Usa un’immagine') ? error.message : 'Immagine non leggibile. Prova un altro file.', true);
  } finally {
    controls();
  }
}

async function refreshStatus() {
  const response = await fetch('/api/status');
  if (!response.ok) throw new Error('Impossibile verificare il collegamento al server.');
  const status = await response.json();
  aiReady = status.screen_ai_ready ?? status.ai_ready;
  callsAvailable = status.api_usage.attempts < status.api_call_limit;
  $('connection').textContent = aiReady
    ? `AI configurata · ${status.screen_model || status.model}. Il tempo effettivo dipende dal modello e dalla rete.`
    : 'Per l’analisi configura OPENAI_API_KEY e OPENAI_MODEL in .env e riavvia RiverLens. Scegli un modello con supporto immagini e risposte strutturate. La cattura locale è già disponibile.';
  $('connection').classList.toggle('warning', !aiReady);
  $('usage').textContent = `Chiamate API: ${status.api_usage.attempts} / ${status.api_call_limit} per questo avvio, condivise con lo studio poker.`;
  if (!aiReady || !callsAvailable) pauseAutomatic();
  controls();
}

function renderList(id, items) {
  const nodes = items.map(text => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  });
  $(id).replaceChildren(...nodes);
}

function renderResult(result, frame, live, expiresAt) {
  reportImage = frame.image;
  reportWasLive = live;
  reportExpiresAt = expiresAt;
  clearTimeout(expiryTimer);
  if (live) expiryTimer = setTimeout(markStale, Math.max(0, expiresAt - performance.now()));
  $('result-empty').hidden = true;
  $('result').hidden = false;
  $('game-name').textContent = [result.game.title, result.game.genre].filter(Boolean).join(' · ') || 'Gioco non riconosciuto';
  $('confidence').textContent = `Confidenza ${ {low: 'bassa', medium: 'media', high: 'alta'}[result.confidence] } · AI`;
  $('result-time').textContent = `Fotogramma ${new Date(result.captured_at).toLocaleTimeString('it-IT')} · ${result.frame_count} immagin${result.frame_count === 1 ? 'e' : 'i'} · ${result.model}`;
  $('move').textContent = result.next_move?.action || (result.status === 'not_game' ? 'Non vedo un gioco da analizzare.' : 'Serve un dettaglio in più.');
  $('reason').textContent = result.next_move?.reason || '';
  renderList('steps', result.next_move?.steps || []);
  $('question').textContent = result.question || '';
  $('question').hidden = !result.question;
  $('summary').textContent = result.summary;
  $('evidence').hidden = result.mode === 'fast';
  $('result-grid').classList.toggle('fast', result.mode === 'fast');
  renderList('observations', result.observations);
  renderList('changes', result.changes);
  renderList('uncertainties', result.uncertainties);
  $('fast-uncertainty').textContent = result.mode === 'fast' ? result.uncertainties.join(' ') : '';
  $('fast-uncertainty').hidden = !result.uncertainties.length || result.mode !== 'fast';
  $('changes-title').hidden = !result.changes.length;
  $('uncertainties-title').hidden = !result.uncertainties.length;
  $('alternatives').replaceChildren(...result.alternatives.map(item => {
    const box = document.createElement('div'); box.className = 'alternative';
    const title = document.createElement('h4'); title.textContent = `Alternativa: ${item.action}`;
    const reason = document.createElement('p'); reason.textContent = item.reason;
    box.append(title, reason);
    return box;
  }));
  markStale();
}

function scheduleAutomatic() {
  clearTimeout(autoTimer);
  autoTimer = null;
  if (!$('auto').checked || !$('consent').checked || source !== 'screen' || busy) return;
  // The sampler wakes idle analysis; after a response only the latest changed frame matters.
  if (frames.at(-1)?.image && frames.at(-1).image !== lastSentImage) {
    autoTimer = setTimeout(() => analyze(true), 0);
  }
}

async function analyze(automatic = false) {
  if (busy || !sourceReady || !aiReady || !callsAvailable || !$('consent').checked) return;
  clearTimeout(autoTimer);
  autoTimer = null;
  const generation = revision;
  const started = performance.now();
  const budget = deadlineMs();
  const expiresAt = started + budget;
  let frame;
  try {
    frame = captureFrame();
    if (performance.now() >= expiresAt) throw new Error('Cattura troppo lenta per il tempo disponibile. Ritaglia un’area più piccola.');
  } catch (error) {
    pauseAutomatic();
    invalidate();
    message(error.message, true);
    return;
  }
  if (automatic && lastSentImage === frame.image) {
    message('Immagine invariata: nessuna nuova chiamata AI.');
    return;
  }
  const live = source === 'screen';
  const request = {
    consent: true,
    frames: live && !fastMode() ? frames.slice() : [frame],
    mode: fastMode() ? 'fast' : 'detailed',
    deadline_ms: budget,
    game_hint: $('game-hint').value.trim(),
    goal: $('goal').value.trim(),
  };
  busy = true;
  controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    if (generation !== revision) return;
    timedOut = true;
    pauseAutomatic();
    clearAdvice('Tempo disponibile scaduto. Nessuna mossa utilizzabile.');
    message('Tempo massimo superato: analisi automatica in pausa.', true);
    controller?.abort();
  }, Math.max(0, expiresAt - performance.now()));
  controls();
  message(`Analisi in corso… Tempo massimo: ${budget / 1000} s.`);
  try {
    const response = await fetch('/api/screen/analyze', {
      method: 'POST', signal: controller.signal,
      headers: {'Content-Type': 'application/json', 'X-RiverLens-Token': token},
      body: JSON.stringify(request),
    });
    const result = await response.json();
    if (generation !== revision) return;
    const elapsed = performance.now() - started;
    $('latency').textContent = `Ultima risposta: ${(elapsed / 1000).toFixed(2)} s · tempo massimo ${budget / 1000} s`;
    if (timedOut || elapsed >= budget) throw new Error('Risposta fuori tempo: mossa scartata. Analisi automatica in pausa.');
    if (!response.ok) throw new Error(result.error || 'Analisi non riuscita.');
    lastSentImage = frame.image;
    // Re-sample at completion, including when background timers have been throttled.
    if (live && captureFrame().image !== frame.image) {
      clearAdvice('Schermo cambiato durante l’analisi: risposta scartata.');
      message('Schermo cambiato: preparo l’ultimo fotogramma.');
      return;
    }
    if (performance.now() >= expiresAt) throw new Error('Tempo disponibile scaduto: mossa scartata.');
    renderResult(result, frame, live, expiresAt);
    message(result.status === 'ready' ? 'Analisi completata. Verifica il consiglio rispetto allo stato attuale.' : 'Analisi completata: servono altre informazioni prima di proporre una mossa.');
  } catch (error) {
    if (generation !== revision) return;
    pauseAutomatic();
    // Never leave an old successful proposal looking like the failed new request.
    clearAdvice('Nessuna nuova mossa disponibile.');
    $('confidence').textContent = 'Analisi non disponibile';
    $('latency').textContent = `Ultimo tentativo: ${((performance.now() - started) / 1000).toFixed(2)} s · tempo massimo ${budget / 1000} s`;
    message(error.name === 'AbortError' ? 'Tempo scaduto o analisi interrotta. Nessun nuovo consiglio disponibile.' : error.message, true);
  } finally {
    clearTimeout(timeout);
    controller = null;
    busy = false;
    try { await refreshStatus(); } catch {
      pauseAutomatic();
      aiReady = false;
      message('Server non raggiungibile. Ricarica la pagina dopo averlo riavviato.', true);
    }
    controls();
    scheduleAutomatic();
  }
}

function contextChanged() {
  pauseAutomatic();
  invalidate();
  try { if (sourceReady) captureFrame(); } catch (error) { message(error.message, true); return; }
  message('Contesto aggiornato. Analizza di nuovo o riattiva l’analisi automatica.');
}

$('share').onclick = shareScreen;
$('stop').onclick = () => { stopSource(); message('Cattura e analisi automatica interrotte.'); };
$('file').onchange = event => { const file = event.target.files[0]; event.target.value = ''; return loadFile(file); };
$('analyze').onclick = () => analyze(false);
$('auto').onchange = () => {
  if ($('auto').checked) analyze(true);
  else { pauseAutomatic(); message('Analisi automatica in pausa. La cattura locale continua.'); }
};
$('analysis-mode').onchange = contextChanged;
$('deadline').onchange = contextChanged;
$('consent').onchange = () => {
  if (!$('consent').checked) {
    pauseAutomatic();
    invalidate();
    message('Invio a OpenAI disattivato. La cattura locale può continuare.');
  }
  controls();
};
for (const id of ['game-hint', 'goal', 'crop-x', 'crop-y', 'crop-w', 'crop-h']) $(id).oninput = contextChanged;
$('reset-crop').onclick = () => { resetCropValues(); contextChanged(); };
document.addEventListener('paste', event => {
  const item = Array.from(event.clipboardData?.items || []).find(value => value.type.startsWith('image/'));
  if (item) { event.preventDefault(); loadFile(item.getAsFile()); }
});
window.addEventListener('pagehide', stopSource);
refreshStatus().catch(() => { message('Server non raggiungibile. Avvia RiverLens e ricarica la pagina.', true); controls(); });

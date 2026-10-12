'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="riverlens-token"]').content;
let hands = [], selected = 0, step = 0, tab = 'replay', current = null, lastResult = null;
let aiReady = false, busy = false, generation = 0, imageData = null, toastTimer;
let studyTimer, aiJob=null, aiStudyBusy=false, savedBudget=null;
const symbols = {h:'♥', d:'♦', c:'♣', s:'♠'};
const format = n => Number(n).toLocaleString('it-IT', {maximumFractionDigits:2});
const pct = n => (n * 100).toFixed(1).replace('.', ',') + '%';
const presets = {
  draw:{hero:'Ah Qh', board:'Jh Th 2c', pot:20, call:8, opponents:1, villain:''},
  pair:{hero:'9s 9d', board:'Ac Kd 4h', pot:22, call:9, opponents:1, villain:''},
  river:{hero:'2c 3d', board:'As Ks Qs Js Ts', pot:100, call:20, opponents:1, villain:'7h 8h'}
};
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-RiverLens-Token':token}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Richiesta non riuscita.');
  return result;
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true, 6500); }
function el(tag, cls, content) { const node=document.createElement(tag); if(cls)node.className=cls; if(content!==undefined)node.textContent=content; return node; }
function card(code) {
  if (!code) return el('div','card empty','♠');
  const node=el('div','card'+(['h','d'].includes(code[1])?' red':''));
  node.append(el('span','rank',code[0]==='T'?'10':code[0]),el('span','suit',symbols[code[1]]));
  node.setAttribute('aria-label',code);return node;
}
function invalidate() {
  generation++; lastResult=null;
  $('analysis-result').hidden=true; $('analysis-empty').hidden=false;
  $('analysis-status').textContent=''; $('ai-answer').textContent='';
  $('decision-action').textContent='Aggiornamento…';$('decision-reason').textContent='';
  $('decision-assumptions').textContent='';$('legal-actions').replaceChildren();
  $('opponent-profiles').replaceChildren();$('budget-summary').textContent='Aggiornamento dei limiti…';
  $('decision-ai-status').textContent='';aiJob=null;
  clearTimeout(studyTimer);studyTimer=setTimeout(updateStudy,350);
}
function setTab(next) {
  tab=next; invalidate();
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  $('image-section').hidden=tab!=='image'; $('study-grid').hidden=tab==='image';
  $('decision-panel').hidden=tab==='image';$('profiles-panel').hidden=tab==='image';
  $('lab-section').hidden=tab!=='lab'; $('replay-panel').hidden=tab!=='replay';
  $('breadcrumb').textContent={replay:'REPLAY',lab:'LABORATORIO',image:'SCREENSHOT'}[tab];
  $('page-title').textContent={replay:'Ogni mano ha una storia.',lab:'Cambia le carte in tavola.',image:'Dall’immagine, ai dati.'}[tab];
  $('page-subtitle').textContent={replay:'Rivedi le decisioni. Separa i numeri dalle sensazioni.',lab:'Esplora uno scenario e rendi esplicite le tue ipotesi.',image:'Una lettura assistita, da verificare sempre.'}[tab];
  if(tab==='replay')renderReplay();
  if(tab==='lab'){current=null;renderLabPreview();$('calculate-button').disabled=false;}
}
function renderHandList() {
  $('hand-count').textContent=hands.length;
  $('hand-list').replaceChildren(...hands.map((h,i)=>{
    const button=el('button','hand-item'+(selected===i?' active':''));
    const info=el('span','',`#${h.id}`);info.append(el('small','',h.isDemo?'Mano dimostrativa':'Hand history importata'));
    button.append(info,el('b','',h.hole.map(c=>c[0]+symbols[c[1]]).join(' ')));
    button.onclick=()=>chooseHand(i);return button;
  }));
}
function chooseHand(index) {
  selected=index;
  const frames=hands[index].snapshots;
  step=frames.findIndex(s=>s.hero_decision && s.street==='FLOP' && s.call>0);
  if(step<0)step=Math.max(0,frames.findIndex(s=>s.hero_decision));
  setTab('replay');renderHandList();
}
function renderTable(hole, board, players, hero, pot, call, street, nextActor, button, unit='chips') {
  $('hero-cards').replaceChildren(...hole.map(card));
  $('board-cards').replaceChildren(...Array.from({length:5},(_,i)=>card(board[i])));
  $('pot-number').replaceChildren(document.createTextNode(format(pot)),el('small','',unit));
  $('street-badge').textContent=street;
  const heroPlayer=players.find(p=>p.name===hero);
  $('hero-name').textContent=hero;
  $('hero-stack').textContent=heroPlayer?.stack===null?'Scenario':format(heroPlayer?.stack||0)+' '+unit;
  $('hero-position').textContent=heroPlayer?.seat===button?'BTN':'HERO';
  $('call-amount').textContent=format(call)+' '+unit;
  $('opponent-seats').replaceChildren(...players.filter(p=>p.name!==hero).map(p=>{
    const seat=el('div','seat'+(p.folded?' folded':'')+(p.name===nextActor?' active':''));
    seat.append(el('span','mini-card'));
    const text=el('div');text.append(el('span','seat-name',p.name+(p.seat===button?' · BTN':'')),el('b','',p.folded?'Fold':p.stack===null?'? '+unit:format(p.stack)+' '+unit));
    if(p.shown?.length)text.append(el('small','',p.shown.map(c=>c[0]+symbols[c[1]]).join(' ')));
    seat.append(text);return seat;
  }));
}
function renderReplay() {
  if(!hands.length)return;
  const hand=hands[selected], s=hand.snapshots[step];
  current={hero:s.hole,board:s.board,opponents:s.opponents,pot:s.pot,call:s.call,complex_pot:s.complex_pot};
  $('table-kicker').textContent=hand.isDemo?'MANO DIMOSTRATIVA · CHIP SIMULATE':'MANO CONCLUSA · REPLAY';
  $('hand-title').textContent=`#${hand.id} · ${s.hero}`;
  renderTable(s.hole,s.board,s.players,s.hero,s.pot,s.call,s.street,s.next_actor,hand.button,hand.unit);
  $('turn-label').textContent=s.hero_decision?'Punto decisionale di Hero':s.next_actor?'Prossima azione: '+s.next_actor:'Osserva il replay';
  $('table-message').textContent=s.label;
  $('step-slider').max=hand.snapshots.length-1;$('step-slider').value=step;
  $('step-count').textContent=`${step+1} / ${hand.snapshots.length}`;
  $('previous-step').disabled=step===0;$('next-step').disabled=step===hand.snapshots.length-1;
  $('calculate-button').disabled=!s.hero_decision||s.opponents<1||busy;
  $('action-list').replaceChildren(...hand.snapshots.map((frame,i)=>{
    const b=el('button','action-item'+(i===step?' active':'')+(i>step?' future':''));
    b.append(el('span','',String(i+1).padStart(2,'0')),document.createTextNode(i<=step?frame.label:'Azione successiva · nascosta'));
    b.onclick=()=>goStep(i);return b;
  }));
  const active=$('action-list').querySelector('.active');
  if(active)$('action-list').scrollTop=active.offsetTop-$('action-list').offsetTop-50;
}
function goStep(index) {step=index;invalidate();renderReplay();}
function labValues() {return {hero:$('hero-input').value,board:$('board-input').value,pot:$('pot-input').value,call:$('call-input').value,opponents:$('opponents-input').value,villain:$('villain-input').value};}
function fillLab(data) {
  for(const k of ['hero','board','pot','call','opponents','villain']) {
    if(data[k]!==undefined)$(`${k}-input`).value=Array.isArray(data[k])?data[k].join(' '):(data[k]??'');
  }
}
function looseCards(text) { return (text.match(/[2-9TJQKA][cdhs]/gi)||[]).map(c=>c[0].toUpperCase()+c[1].toLowerCase()); }
function renderLabPreview(result) {
  const raw=labValues(),data=result?.context;
  const hero=data?.hero||looseCards(raw.hero),board=data?.board||looseCards(raw.board);
  const n=Number(data?.opponents||raw.opponents||1);
  const players=[{name:'Hero',stack:null,seat:0},...Array.from({length:n},(_,i)=>({name:'Avversario '+(i+1),stack:null,seat:i+1,shown:data?.villain||[]}))];
  renderTable(hero,board,players,'Hero',Number(data?.pot??raw.pot)||0,Number(data?.call??raw.call)||0,{0:'PREFLOP',3:'FLOP',4:'TURN',5:'RIVER'}[board.length]||'SCENARIO',null,null);
  $('table-kicker').textContent='LABORATORIO · SCENARIO SIMULATO';$('hand-title').textContent='La tua simulazione';
  $('table-message').textContent='Gli stack non entrano nel modello semplificato.';$('turn-label').textContent='Scenario modificabile';
}
function showResult(result) {
  lastResult=result;$('analysis-empty').hidden=true;$('analysis-result').hidden=false;
  $('equity-ring').style.setProperty('--equity',(result.equity*100)+'%');
  $('equity-value').textContent=pct(result.equity);
  $('equity-confidence').textContent=result.exact?'Risultato esatto, tutte le carte note':`± ${(result.margin95*100).toFixed(1).replace('.',',')} punti · errore MC indicativo al 95%`;
  $('made-hand').textContent=result.made_hand;
  $('win-value').textContent=pct(result.win);$('tie-value').textContent=pct(result.tie);
  $('odds-value').textContent=result.context.call?pct(result.pot_odds):'Nessun call';
  $('ev-value').textContent=result.context.call?(result.call_ev_model>=0?'+':'')+format(result.call_ev_model)+' chip':'—';
  $('assumption-text').textContent=result.assumption;
  $('sample-count').textContent=result.exact?'Confronto esatto':format(result.samples)+' simulazioni';
  $('calculation-time').textContent=result.elapsed_ms+' ms';
}
async function calculate() {
  if(busy)return;
  if(tab==='replay'&&!hands[selected]?.snapshots[step]?.hero_decision)return;
  const data=tab==='lab'?labValues():current;
  if(!data)return;
  busy=true;invalidate();const revision=generation;
  $('calculate-button').disabled=true;$('calculate-button').textContent='Calcolo in corso…';
  try {
    const result=await api('/api/analyze',data);
    if(revision!==generation)return;
    current=result.context;showResult(result);
    if(tab==='lab')renderLabPreview(result);
  } catch(e){if(revision===generation)$('analysis-status').textContent=e.message;}
  finally {busy=false;$('calculate-button').textContent='Calcola equity ↗';$('calculate-button').disabled=tab==='replay'&&!hands[selected]?.snapshots[step]?.hero_decision;}
}
async function loadDemo() {
  const data=await api('/api/examples');hands=data.hands.map(h=>({...h,isDemo:true}));chooseHand(0);
}
async function importHands() {
  $('import-confirm').disabled=true;$('import-error').textContent='';
  try {
    const data=await api('/api/import',{text:$('history-text').value});
    hands=data.hands;chooseHand(0);$('import-dialog').close();
    toast(`${data.added} mani aggiunte, ${data.duplicates} già presenti.`+(data.errors.length?` ${data.errors.length} scartate: ${data.errors[0].message}`:''));
  }catch(e){$('import-error').textContent=e.message;}
  finally{$('import-confirm').disabled=false;}
}
function showSettings() { $('settings-dialog').showModal(); }
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{fillLab(presets[b.dataset.preset]);invalidate();renderLabPreview();});
$('open-import').onclick=()=>{$('import-error').textContent='';$('import-dialog').showModal();};
$('settings-button').onclick=showSettings;
$('import-confirm').onclick=importHands;
$('demo-button').onclick=async()=>{try{await loadDemo();$('import-dialog').close();}catch(e){$('import-error').textContent=e.message;}};
$('history-file').onchange=async event=>{const f=event.target.files[0];if(f){if(f.size>2_000_000){$('import-error').textContent='Il file supera 2 MB.';return;}$('history-text').value=await f.text();}};
$('step-slider').oninput=event=>goStep(Number(event.target.value));
$('previous-step').onclick=()=>goStep(Math.max(0,step-1));
$('next-step').onclick=()=>goStep(Math.min(hands[selected].snapshots.length-1,step+1));
$('lab-form').onsubmit=event=>{event.preventDefault();calculate();};
$('lab-form').oninput=()=>{invalidate();current=null;renderLabPreview();};
$('calculate-button').onclick=calculate;
document.addEventListener('keydown',e=>{
  if(tab!=='replay'||!hands.length||['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||document.querySelector('dialog[open]'))return;
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();goStep(Math.min(hands[selected].snapshots.length-1,Math.max(0,step+(e.key==='ArrowLeft'?-1:1))));}
});
$('explain-button').onclick=async()=>{
  if(!aiReady)return showSettings();
  if(!lastResult)return toast('Prima calcola l’equity di questo punto.');
  if(!$('ai-consent').checked)return toast('Conferma i dati da inviare a OpenAI.');
  const revision=generation;$('explain-button').disabled=true;$('ai-answer').textContent='Analisi in corso…';
  try{const result=await api('/api/explain',{scenario:lastResult.context,consent:true});if(revision===generation)$('ai-answer').textContent=result.text+'\n\nModello: '+result.model;}
  catch(e){if(revision===generation)$('ai-answer').textContent=e.message;}
  finally{$('explain-button').disabled=false;}
};
$('image-file').onchange=async e=>{
  const file=e.target.files[0];imageData=null;$('read-image').disabled=true;$('uploaded-image').hidden=true;$('image-placeholder').hidden=false;
  if(!file)return;
  if(file.size>4_000_000||!['image/png','image/jpeg','image/webp'].includes(file.type))return toast('Scegli un’immagine PNG, JPEG o WebP inferiore a 4 MB.');
  const reader=new FileReader();reader.onload=()=>{imageData=reader.result;$('uploaded-image').src=imageData;$('uploaded-image').hidden=false;$('image-placeholder').hidden=true;$('read-image').disabled=false;};reader.readAsDataURL(file);
};
$('read-image').onclick=async()=>{
  if(!aiReady)return showSettings();
  if(!$('archived-check').checked||!$('vision-consent').checked)return toast('Conferma origine archiviata e invio dell’immagine.');
  $('read-image').disabled=true;$('vision-status').textContent='Lettura in corso…';
  try {
    const data=await api('/api/vision',{image:imageData,archived:true,consent:true});
    fillLab({...data,villain:'',opponents:data.opponents>=1&&data.opponents<=8?data.opponents:1});
    $('vision-notes').hidden=false;$('vision-notes').textContent='Trascrizione AI da verificare: '+(data.notes||'Controlla tutte le carte e gli importi prima di calcolare.');
    setTab('lab');toast('Dati trascritti. Controllali prima di calcolare.');$('vision-status').textContent='Trascrizione completata.';
  }catch(e){$('vision-status').textContent=e.message;}
  finally{$('read-image').disabled=false;}
};
function studyRequest() {
  if(tab==='replay') {
    const h=hands[selected];if(!h)return null;
    return {source:h.isDemo?'demo':'archive',hand_id:h.id,step};
  }
  if(tab!=='lab')return null;
  const scenario=labValues();
  scenario.stack=$('stack-input').value;scenario.big_blind=$('big-blind-input').value;
  scenario.hero_bet=Number($('hero-bet-input').value);scenario.current_bet=scenario.hero_bet+Number(scenario.call);
  scenario.invested=$('invested-input').value;scenario.raise_reopened=$('raise-reopened-input').checked;
  scenario.min_raise_increment=$('min-raise-input').value||Math.max(Number(scenario.big_blind),scenario.current_bet);
  return {source:'simulation',scenario};
}
function actionLabel(a) {
  if(!a)return 'DATI INSUFFICIENTI';
  if(a.action==='fold'||a.action==='check')return a.action.toUpperCase();
  if(a.action==='raise')return 'RAISE A '+format(a.raise_to);
  if(a.action==='bet')return 'BET '+format(a.amount_add);
  if(a.action==='all_in')return 'ALL-IN '+format(a.amount_add)+(a.underlying==='call'?' · CALL':'');
  return 'CALL '+format(a.amount_add);
}
function renderStudy(report) {
  const rec=report.recommendation,budget=report.budget;
  $('decision-action').textContent=!budget.configured?'CONFIGURA IL BUDGET':!report.legal_actions.length?'NESSUNA DECISIONE':actionLabel(report.selected_action);
  $('decision-reason').textContent=rec.rationale;
  $('decision-source').textContent=rec.source==='openai'?'OPENAI · '+rec.confidence.toUpperCase():'REGOLA DIDATTICA · SUPPORTO BASSO';
  $('decision-assumptions').textContent=(rec.assumptions||[]).join(' ');
  $('legal-actions').replaceChildren(...report.legal_actions.map(a=>el('span','legal-pill',a.action.toUpperCase()+(a.max_add?' '+format(a.min_add)+(a.max_add!==a.min_add?'–'+format(a.max_add):''):'')+(a.underlying==='call'&&a.all_in?' (call)':''))));
  $('budget-summary').textContent=budget.configured&&budget.unit&&budget.bankroll_before_hand!==undefined?
    `${budget.demo?'DEMO · ':''}${budget.unit} · Bankroll prima della mano ${format(budget.bankroll_before_hand)} · Risultato precedente ${format(budget.net_before_hand)} · Già impegnato ${format(budget.committed_this_hand)} · Ancora impegnabile ${format(budget.allowed_add)}. ${budget.stop_session?'STOP SESSIONE. ':''}${budget.reason}`:budget.reason;
  $('history-count').textContent=report.past_hands+' mani precedenti';
  $('opponent-profiles').replaceChildren(...report.opponents.map(p=>{
    const box=el('div','profile-item');box.append(el('strong','',p.name),el('span','profile-count',p.hands+' mani · '+p.style));
    const value=x=>x===null?'n/d':pct(x);
    box.append(el('p','',`VPIP ${value(p.vpip)} · PFR ${value(p.pfr)} · Aggressività ${value(p.aggression_frequency)} · Fold su puntata ${value(p.fold_to_bet)} (${p.fold_opportunities} occasioni)`));return box;
  }));
  if(!report.opponents.length)$('opponent-profiles').append(el('p','small','Nessun avversario con storico in questo scenario.'));
  const status={disabled:'Proposta locale. AI automatica disattivata.',not_configured:'Configura la chiave e il modello OpenAI.',completed:'Proposta AI verificata contro azioni e budget.',cached:'Proposta AI già analizzata: nessuna nuova chiamata.',pending:'Analisi dello stesso punto già in corso.',skipped:'Nessuna chiamata AI: dati o limiti non consentono la proposta.',fallback:'AI non disponibile: riferimento locale visibile.'};
  $('decision-ai-status').textContent=(status[report.ai_status]||'')+(report.ai_error?' '+report.ai_error:'');
  if(report.equity){showResult(report.equity);current=report.equity.context;}
}
async function updateStudy() {
  const request=studyRequest();if(!request)return;
  const revision=generation;
  try {
    const report=await api('/api/recommend',request);if(revision!==generation)return;
    renderStudy(report);
    if($('auto-ai').checked&&aiReady&&report.candidates.length&&!report.blocked){aiJob={request,revision};processAIQueue();}
  }catch(e){if(revision===generation){$('decision-action').textContent='VERIFICA I DATI';$('decision-reason').textContent=e.message;}}
}
async function processAIQueue() {
  if(aiStudyBusy)return;
  aiStudyBusy=true;
  try {
    while(aiJob&&$('auto-ai').checked){
      const job=aiJob;aiJob=null;
      if(job.revision!==generation)continue;
      $('decision-ai-status').textContent='Analisi AI in corso; il riferimento locale resta visibile.';
      try{const report=await api('/api/recommend',{...job.request,use_ai:true,consent:true});if(job.revision===generation&&$('auto-ai').checked)renderStudy(report);}
      catch(e){if(job.revision===generation)$('decision-ai-status').textContent=e.message;}
    }
  }finally{aiStudyBusy=false;}
}
$('auto-ai').onchange=()=>{if($('auto-ai').checked&&!aiReady){$('auto-ai').checked=false;showSettings();return;}invalidate();};
$('budget-button').onclick=()=>{
  if(savedBudget){$('bankroll-config').value=savedBudget.bankroll;$('stop-loss-config').value=savedBudget.stop_loss;$('hand-limit-config').value=savedBudget.max_hand_loss;$('unit-config').value=savedBudget.unit;$('start-hand-config').value=savedBudget.start_hand_id;}
  $('budget-error').textContent='';$('budget-dialog').showModal();
};
$('budget-form').onsubmit=async e=>{
  e.preventDefault();
  try {const result=await api('/api/session',{bankroll:$('bankroll-config').value,stop_loss:$('stop-loss-config').value,max_hand_loss:$('hand-limit-config').value,unit:$('unit-config').value,start_hand_id:$('start-hand-config').value});savedBudget=result.budget;$('budget-dialog').close();invalidate();}
  catch(error){$('budget-error').textContent=error.message;}
};
(async()=>{
  try {const [status,archive,session]=await Promise.all([api('/api/status'),api('/api/archive'),api('/api/session')]);aiReady=status.ai_ready;savedBudget=session.budget;
    if(archive.hands.length){hands=archive.hands;chooseHand(0);}else await loadDemo();
    $('ai-dot').classList.toggle('muted',!aiReady);
    $('connection-status').textContent=(aiReady?'Collegamento configurato · modello: '+status.model:'OpenAI non configurato. Il motore locale è già pronto.')+` Limite API: ${status.api_call_limit} tentativi per avvio.`;
    $('explain-button').textContent=aiReady?'Spiega con AI ↗':'Configura OpenAI ↗';
  }catch(e){toast(e.message);$('hand-title').textContent='Impossibile caricare le mani';}
})();

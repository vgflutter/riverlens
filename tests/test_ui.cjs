// Behavioral checks with a minimal DOM, not a browser/layout test. No network calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const code = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');

function setup() {
  const elements = new Map();
  function element() {
    return {value:'',checked:false,hidden:false,indeterminate:false,textContent:'',
      classList:{toggle(){}},style:{setProperty(){}},append(){},replaceChildren(){},
      setAttribute(){},querySelector(){return null;},checkValidity(){return true;},
      reportValidity(){return true;},close(){},showModal(){}};
  }
  for (const tag of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    assert.ok(!elements.has(tag[1]), 'Duplicate ID: '+tag[1]);
    elements.set(tag[1], Object.assign(element(), {value:tag[0].match(/\bvalue="([^"]*)"/)?.[1]||''}));
  }
  const get = id => {assert.ok(elements.has(id), 'Missing element: '+id);return elements.get(id);};
  get('unit-input').value='chips';get('opponents-input').value='1';
  const calls=[];
  const context=vm.createContext({
    document:{getElementById:get,querySelector:()=>({content:'test-token'}),
      querySelectorAll:()=>[],addEventListener(){},createElement:element,createTextNode:text=>text},
    setTimeout(){return 1;},clearTimeout(){},
    fetch:async (url,options)=>{
      if(!options.method)return new Promise(()=>{}); // Startup is irrelevant to these event checks.
      calls.push({url,body:JSON.parse(options.body)});
      if(url==='/api/vision')return {ok:true,json:async()=>({hero:['Ah','Qh'],board:['Jh','Th','2c'],
        pot:20,call:8,opponents:1,stack:null,unit:null,account_balance:null,account_unit:null,
        big_blind:null,hero_bet:null,invested:null,min_raise_increment:null,raise_reopened:null,notes:''})};
      if(url==='/api/session')return {ok:true,json:async()=>({budget:JSON.parse(options.body)})};
      throw new Error('Unexpected request: '+url);
    }
  });
  vm.runInContext(code,context);
  return {get,calls,run:script=>vm.runInContext(script,context)};
}

test('unverified screenshot clears stale stack and cannot trigger recommendations or calculations',async()=>{
  const {get,calls,run}=setup();
  get('archived-check').checked=true;get('vision-consent').checked=true;get('auto-ai').checked=true;
  run("aiReady=true;imageData='data:image/png;base64,YQ==';tab='image';");
  await get('read-image').onclick();
  assert.equal(get('stack-input').value,'');
  assert.equal(get('unit-input').value,'');
  assert.equal(get('invested-input').value,'');
  assert.equal(get('vision-verified').checked,false);
  assert.equal(get('raise-reopened-input').indeterminate,true);
  await run('updateStudy()');
  await run('calculate()');
  assert.equal(get('decision-action').textContent,'VERIFICA LA TRASCRIZIONE');
  assert.deepEqual(calls.map(x=>x.url),['/api/vision']);
});

test('editing confirmed screenshot inputs revokes confirmation and stops queued AI work',async()=>{
  const {get,calls,run}=setup();
  run("tab='lab';imageReview=true;aiJob={request:{},revision:generation};");
  get('vision-verified').checked=true;
  get('lab-form').oninput({target:{id:'stack-input'}});
  assert.equal(get('vision-verified').checked,false);
  assert.equal(run('aiJob'),null);
  await run('updateStudy()');
  assert.equal(calls.length,0);
});

test('personal limits save without a bankroll field and can be removed',async()=>{
  const {get,calls}=setup();
  get('stop-loss-config').value='';get('hand-limit-config').value='10';get('unit-config').value='EUR';
  await get('budget-form').onsubmit({preventDefault(){}});
  assert.deepEqual(calls[0].body,{stop_loss:'',max_hand_loss:'10',unit:'EUR',start_hand_id:'0'});
  await get('clear-limits').onclick();
  assert.deepEqual(calls[1].body,{});
});

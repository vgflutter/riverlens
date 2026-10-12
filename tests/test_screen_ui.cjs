// Browser-independent event-flow checks. Media, canvas and provider calls are mocked.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/screen.html'), 'utf8');
const code = fs.readFileSync(path.join(root, 'static/screen.js'), 'utf8');

function report() {
  return {status:'ready',game:{title:'Tris',genre:'Puzzle',confidence:'high'},
    next_move:{action:'<img src=x onerror=alert(1)>',reason:'Blocca la riga.',steps:['Alto a destra.']},
    observations:['Due O.'],changes:[],uncertainties:[],alternatives:[],question:null,
    summary:'Turno di X.',confidence:'medium',model:'mock',captured_at:new Date().toISOString(),frame_count:1};
}

function setup() {
  const elements = new Map(), events = {}, timers = new Map(), calls = [], tracks = [];
  let timerId = 0, pixels = 'A', fail = false, delayed = false, resolveRequest, apiLimit = 20;
  const element = () => ({value:'',checked:false,hidden:false,disabled:false,textContent:'',src:'',
    readyState:4,videoWidth:1920,videoHeight:1080,naturalWidth:1920,naturalHeight:1080,
    classList:{toggle(){}},setAttribute(){},removeAttribute(key){delete this[key];},
    append(){},replaceChildren(...children){this.children=children;},pause(){},async play(){}});
  for (const tag of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    assert.ok(!elements.has(tag[1]), 'Duplicate ID '+tag[1]);
    elements.set(tag[1], {...element(), value:tag[0].match(/\bvalue="([^"]*)"/)?.[1] || ''});
  }
  const get = id => {assert.ok(elements.has(id), 'Missing element '+id);return elements.get(id);};
  get('interval').value = '10';
  const context = vm.createContext({
    document:{getElementById:get,querySelector:()=>({content:'test-token'}),
      createElement:tag=>tag==='canvas'?{getContext:()=>({drawImage(){}}),toDataURL:()=>`data:image/jpeg;base64,${pixels}`} : element(),
      addEventListener:(name,callback)=>{events[name]=callback;}},
    window:{addEventListener:(name,callback)=>{events[name]=callback;}},
    navigator:{mediaDevices:{getDisplayMedia:async options=>{
      assert.equal(options.audio, false);
      const track={readyState:'live',muted:false,stopped:false,listeners:{},
        stop(){this.stopped=true;},addEventListener(name,callback){this.listeners[name]=callback;}};
      tracks.push(track);
      return {getTracks:()=>[track],getVideoTracks:()=>[track]};
    }}},
    URL:{revokeObjectURL(){}},AbortController,
    setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},
    clearTimeout:id=>timers.delete(id),
    fetch:async (url,options)=>{
      if(url==='/api/status')return {ok:true,json:async()=>({ai_ready:true,model:'mock',api_usage:{attempts:calls.length},api_call_limit:apiLimit})};
      assert.equal(url,'/api/screen/analyze');
      calls.push(JSON.parse(options.body));
      if(delayed)await new Promise(resolve=>{resolveRequest=resolve;}); // Deliberately ignore abort: test revision guards too.
      return {ok:!fail,json:async()=>fail?{error:'Provider error'}:report()};
    },
  });
  vm.runInContext(code,context);
  const run = script=>vm.runInContext(script,context);
  return {get,calls,tracks,events,timers,run,
    setPixels:value=>{pixels=value;},setLimit:value=>{apiLimit=value;},
    setFailure:()=>{fail=true;},delay:()=>{delayed=true;},resolve:()=>resolveRequest(),
    async ready(){await run('refreshStatus()');get('consent').checked=true;await get('share').onclick();},
  };
}

test('local capture is independent of AI consent and sends no images on connect', async()=>{
  const app=setup();
  await app.run('refreshStatus()');
  await app.get('share').onclick();
  assert.equal(app.calls.length,0);
  assert.equal(app.get('analyze').disabled,true);
  assert.equal(app.run('frames.length'),1);
  assert.ok([...app.timers.values()].some(t=>t.ms===2000));
  await app.run('analyze()');
  assert.equal(app.calls.length,0);
  app.get('consent').checked=true;
  app.get('consent').onchange();
  assert.equal(app.get('analyze').disabled,false);
  await app.get('analyze').onclick();
  assert.equal(app.calls.length,1);
  assert.equal(app.calls[0].frames.length,1);
  assert.equal(app.get('move').textContent,'<img src=x onerror=alert(1)>'); // Rendered as text, never HTML.
});

test('automatic mode suppresses identical images, keeps at most 3 frames and waits between calls', async()=>{
  const app=setup();await app.ready();
  app.get('auto').checked=true;
  await app.run('analyze(true)');
  await app.run('analyze(true)');
  assert.equal(app.calls.length,1);
  assert.ok([...app.timers.values()].some(t=>t.ms===10000));
  for(const pixels of ['B','C','D','E']){app.setPixels(pixels);app.run('captureFrame()');}
  assert.equal(app.run('frames.length'),3);
  await app.run('analyze(true)');
  assert.equal(app.calls.length,2);
  assert.equal(app.calls[1].frames.length,3);
  assert.match(app.calls[1].frames.at(-1).image,/E$/);
});

test('an in-flight request is not overlapped and stop discards late results', async()=>{
  const app=setup();await app.ready();app.delay();
  const first=app.run('analyze()');
  await app.run('analyze()');
  assert.equal(app.calls.length,1);
  app.get('stop').onclick();
  assert.equal(app.tracks[0].stopped,true);
  assert.equal(app.run('frames.length'),0);
  assert.equal(app.get('auto').checked,false);
  app.resolve();await first;
  assert.equal(app.get('result').hidden,true);
  assert.equal(app.run('reportImage'),null);
});

test('ending a share, revoking consent and editing context stop automatic work', async()=>{
  for(const action of ['ended','consent','goal']){
    const app=setup();await app.ready();app.get('auto').checked=true;app.delay();
    const pending=app.run('analyze(true)');
    if(action==='ended')app.tracks[0].listeners.ended();
    if(action==='consent'){app.get('consent').checked=false;app.get('consent').onchange();}
    if(action==='goal'){app.get('goal').value='Nuovo gioco';app.get('goal').oninput();}
    app.resolve();await pending;
    assert.equal(app.get('auto').checked,false);
    assert.equal(app.get('result').hidden,true);
    assert.equal([...app.timers.values()].filter(t=>t.ms===10000).length,0);
  }
});

test('changing a live frame makes the previous recommendation visibly stale', async()=>{
  const app=setup();await app.ready();await app.run('analyze()');
  assert.equal(app.get('stale').hidden,true);
  app.setPixels('changed');app.run('captureFrame()');
  assert.equal(app.get('stale').hidden,false);
});

test('invalid crop and muted capture cannot trigger paid requests', async()=>{
  for(const mode of ['crop','mute']){
    const app=setup();await app.ready();app.get('auto').checked=true;
    if(mode==='crop')app.get('crop-x').value='50';
    else app.tracks[0].muted=true;
    await app.run('analyze()');
    assert.equal(app.calls.length,0);
    assert.equal(app.get('auto').checked,false);
    assert.equal(app.get('result').hidden,true);
  }
});

test('provider error and exhausted cap stop automatic calls', async()=>{
  const app=setup();await app.ready();await app.run('analyze()');
  app.setFailure();app.setPixels('new');app.get('auto').checked=true;
  await app.run('analyze(true)');
  assert.equal(app.get('result').hidden,true);
  assert.equal(app.get('auto').checked,false);
  assert.equal(app.get('operation-status').textContent,'Provider error');
  app.setLimit(app.calls.length);await app.run('refreshStatus()');
  const count=app.calls.length;
  await app.run('analyze()');
  assert.equal(app.calls.length,count);
  assert.equal(app.get('analyze').disabled,true);
});

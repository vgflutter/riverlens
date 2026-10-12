"""Offline recommendations assembled from authoritative archived snapshots."""
from collections import OrderedDict
import copy
import hashlib
import json
import threading
from archive import DEMO_BUDGET
from decisions import assess, cents
from engine import cards, number
from profiles import profiles


def simulation_state(data):
    if not isinstance(data, dict):
        raise ValueError('Scenario non valido.')
    state = {key:cents(data.get(key, default), key)/100 for key,default in
             [('stack',200),('hero_bet',0),('current_bet',0),('big_blind',2),('min_raise_increment',2),('invested',0),('pot',0)]}
    state.update({'hero':cards(data.get('hero',[])), 'board':cards(data.get('board',[])),
                  'villain':cards(data.get('villain',[])), 'unit':'chips', 'hero_turn':True,
                  'raise_reopened':data.get('raise_reopened',True), 'opponent_can_respond':True})
    if 'min_raise_increment' not in data:
        state['min_raise_increment']=max(state['big_blind'],state['current_bet'])
    opponents=number(data.get('opponents',1),'Avversari',8)
    if opponents<1 or not opponents.is_integer():
        raise ValueError('Numero di avversari non valido.')
    if state['current_bet']-state['hero_bet']>state['stack']:
        state['complex_pot']=True
    state['opponents']=int(opponents)
    state['street']={0:'PREFLOP',3:'FLOP',4:'TURN',5:'RIVER'}.get(len(state['board']),'UNKNOWN')
    if state['invested']<state['hero_bet'] or state['pot']<state['current_bet']:
        raise ValueError('Importi incoerenti: verifica puntata, contributo alla mano e piatto.')
    return state


def replay_state(hand, index):
    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(hand['snapshots']):
        raise ValueError('Punto del replay non valido.')
    snap=hand['snapshots'][index]
    hero=next(p for p in snap['players'] if p['name']==hand['hero'])
    previous=hand['snapshots'][:index+1]
    last_aggressor=next((s['actor'] for s in reversed(previous) if s.get('action') in ('bet','raise')),None)
    state={'hero':snap['hole'],'board':snap['board'],'pot':snap['pot'],'stack':hero['stack'],
           'hero_bet':hero['bet'],'invested':hero['invested'],'opponents':snap['opponents'],
           'unit':hand['unit'],'hero_turn':snap['hero_decision'],'folded':hero['folded'],
           'current_bet':snap['current_bet'],'big_blind':snap['big_blind'],
           'min_raise_increment':snap['min_raise_increment'],'raise_reopened':snap['raise_reopened'],
           'complex_pot':snap['complex_pot'],'ambiguous_bet':snap['ambiguous_bet'],
           'street':snap['street'],'last_aggressor':last_aggressor,
           'position':'button' if hero['seat']==hand['button'] else 'seat '+str(hero['seat']),
           'opponent_can_respond':any(not p['folded'] and p['name']!=hand['hero'] and p['stack']>0 for p in snap['players'])}
    # Numeric actions only: no raw user text, revealed cards, or future snapshots enter the prompt.
    actions=[{'actor':s['actor'],'action':s.get('action'),'street':s['street'],'pot_after':s['pot'],
              'bet_to':next(p['bet'] for p in s['players'] if p['name']==s['actor'])}
             for s in previous if s['kind']=='action']
    names=[p['name'] for p in snap['players'] if p['name']!=hand['hero'] and not p['folded']]
    return state, actions, names


def anonymize(state, observed, opponent_profiles, report):
    names=[]
    for p in opponent_profiles:
        if p['name'] not in names:names.append(p['name'])
    for a in observed:
        if a['actor'] not in names:names.append(a['actor'])
    mapping={name:f'player_{i+1}' for i,name in enumerate(names)}
    safe_state=copy.deepcopy(state)
    safe_state['last_aggressor']=mapping.get(state.get('last_aggressor'))
    safe_profiles=[{**p,'name':mapping[p['name']]} for p in opponent_profiles]
    safe_actions=[{**a,'actor':mapping[a['actor']]} for a in observed]
    equity=copy.deepcopy(report['equity'])
    if equity:equity.pop('elapsed_ms',None)
    return {'style':'decisive_positive_ev_with_loss_limits','state':safe_state,'observed_actions':safe_actions,
            'opponent_profiles':safe_profiles,'budget':report['budget'],'equity_reference':equity,
            'candidates':report['candidates'],'local_baseline':report['recommendation']}


def schema_for(options):
    ids=[x['id'] for x in options]
    choice={'type':['string','null'],'enum':ids+[None]}
    return {'type':'object','additionalProperties':False,
            'properties':{'candidate_id':choice,'confidence':{'type':'string','enum':['low','medium','high']},
                          'rationale':{'type':'string'},'assumptions':{'type':'array','items':{'type':'string'}},
                          'alternatives':{'type':'array','items':{'type':'object','additionalProperties':False,
                            'properties':{'candidate_id':{'type':'string','enum':ids},'reason':{'type':'string'}},
                            'required':['candidate_id','reason']}}},
            'required':['candidate_id','confidence','rationale','assumptions','alternatives']}


def validate_answer(answer, options):
    if not isinstance(answer,dict) or set(answer)!={'candidate_id','confidence','rationale','assumptions','alternatives'}:
        raise ValueError('Risposta AI con schema non valido.')
    ids={x['id'] for x in options}
    if answer['candidate_id'] is not None and answer['candidate_id'] not in ids:
        raise ValueError('L’AI ha proposto una mossa non ammessa dai limiti.')
    if answer['confidence'] not in ('low','medium','high') or not isinstance(answer['rationale'],str) or not 1<=len(answer['rationale'])<=900:
        raise ValueError('Motivazione AI non valida.')
    if not isinstance(answer['assumptions'],list) or len(answer['assumptions'])>3 or any(not isinstance(a,str) or len(a)>400 for a in answer['assumptions']):
        raise ValueError('Ipotesi AI non valide.')
    if not isinstance(answer['alternatives'],list) or len(answer['alternatives'])>2:
        raise ValueError('Alternative AI non valide.')
    for alternative in answer['alternatives']:
        if not isinstance(alternative,dict) or set(alternative)!={'candidate_id','reason'} or alternative['candidate_id'] not in ids or not isinstance(alternative['reason'],str) or len(alternative['reason'])>400:
            raise ValueError('Alternativa AI non valida.')
    return {**answer,'source':'openai'}


class Study:
    def __init__(self, archive, demo_loader):
        self.archive,self.demo_loader=archive,demo_loader
        self.cache=OrderedDict()
        self.lock=threading.Lock()
        self.pending=set()

    def prepare(self,data):
        source=data.get('source')
        if source not in ('demo','archive','simulation'):
            raise ValueError('Sorgente ammessa: demo, archive o simulation.')
        if source=='simulation':
            state=simulation_state(data.get('scenario',{}))
            config=self.archive.budget() or DEMO_BUDGET
            past,observed,names,net=[],[],[],0
        else:
            all_hands=self.demo_loader() if source=='demo' else self.archive.hands()
            hand=next((h for h in all_hands if h['id']==str(data.get('hand_id'))),None)
            if hand is None:
                raise ValueError('Mano conclusa non presente nell’archivio selezionato.')
            state,observed,names=replay_state(hand,data.get('step'))
            past=[h for h in all_hands if int(h['id'])<int(hand['id']) and h['unit']==hand['unit']]
            config=DEMO_BUDGET if source=='demo' else self.archive.budget()
            start=int(config.get('start_hand_id','0')) if config else 0
            net=round(sum(h['hero_net'] for h in past if h['hero']==hand['hero'] and int(h['id'])>=start),2)
            if config and int(hand['id'])<start:
                config=None
        observed_profiles=profiles(past,names)
        report=assess(state,config,net,observed_profiles)
        report.update({'source':source,'style':'decisive_positive_ev','past_hands':len(past),
                       'state_fingerprint':hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()[:16]})
        return report,anonymize(state,observed,observed_profiles,report)

    def recommend(self,data,ai_call=None,model='',prompt=''):
        report,payload=self.prepare(data)
        report['ai_status']='disabled'
        if data.get('use_ai') is True:
            if data.get('consent') is not True:
                raise ValueError('Conferma una volta l’invio automatico dei punti di studio a OpenAI.')
            if report['blocked'] or not report['candidates']:
                report['ai_status']='skipped'
            elif ai_call is None:
                report['ai_status']='not_configured'
            else:
                key=hashlib.sha256((json.dumps(payload,sort_keys=True)+model+prompt).encode()).hexdigest()
                with self.lock:
                    cached=copy.deepcopy(self.cache.get(key))
                    running=key in self.pending
                    if not cached and not running:self.pending.add(key)
                if cached:
                    report['recommendation']=cached
                    report['ai_status']='cached'
                elif running:
                    report['ai_status']='pending'
                else:
                    try:
                        answer=validate_answer(ai_call(payload,schema_for(report['candidates'])),report['candidates'])
                        with self.lock:
                            self.cache[key]=copy.deepcopy(answer)
                            while len(self.cache)>128:self.cache.popitem(last=False)
                        report['recommendation']=answer
                        report['ai_status']='completed'
                    except (ValueError,TypeError,KeyError) as error:
                        report['ai_status']='fallback'
                        report['ai_error']=str(error)[:220]
                    finally:
                        with self.lock:self.pending.discard(key)
        chosen=report['recommendation']['candidate_id']
        report['selected_action']=next((x for x in report['candidates'] if x['id']==chosen),None)
        return report

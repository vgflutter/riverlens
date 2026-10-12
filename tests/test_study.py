import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from archive import Archive, validate_budget
from decisions import legal_actions, budget_status, candidates, assess
from history import parse_histories
from profiles import profiles
from study import Study, replay_state, simulation_state, validate_answer
import server

DEMO=(Path(__file__).resolve().parents[1]/'examples/demo.txt').read_text()
LIMITS={'stop_loss':200,'max_hand_loss':100,'unit':'chips','start_hand_id':'0'}


def sample_state(**changes):
    return dict({'hero':['Ah','Qh'],'board':['Jh','Th','2c'],'pot':20,'stack':194,'hero_bet':0,
                 'current_bet':8,'big_blind':2,'min_raise_increment':8,'invested':6,'opponents':1,
                 'unit':'chips','raise_reopened':True,'hero_turn':True},**changes)


class ActionTests(unittest.TestCase):
    def test_facing_bet(self):
        a=legal_actions(sample_state())
        self.assertEqual([x['action'] for x in a],['fold','call','raise','all_in'])
        self.assertEqual(a[1]['min_add'],8)
        self.assertEqual(a[2]['min_add'],16)
        self.assertEqual(a[2]['max_add'],193.99)

    def test_check_and_bet_when_unopened(self):
        a=legal_actions(sample_state(current_bet=0,min_raise_increment=2))
        self.assertEqual([x['action'] for x in a],['check','bet','all_in'])
        self.assertEqual(a[1]['min_add'],2)

    def test_committed_bet_affects_raise_cost(self):
        a=legal_actions(sample_state(hero_bet=4,current_bet=12,min_raise_increment=8))
        self.assertEqual(a[1]['min_add'],8)
        self.assertEqual(a[2]['min_add'],16) # total raise-to = 20

    def test_short_all_in_and_reopening(self):
        a=legal_actions(sample_state(stack=10))
        self.assertEqual([x['action'] for x in a],['fold','call','all_in'])
        self.assertEqual([x['action'] for x in legal_actions(sample_state(stack=10,raise_reopened=False))],['fold','call'])
        a=legal_actions(sample_state(stack=5))
        self.assertEqual([x['action'] for x in a],['fold','all_in'])
        self.assertEqual(a[1]['underlying'],'call')
        self.assertEqual(a[1]['min_add'],5)

    def test_no_raise_when_nobody_can_respond(self):
        self.assertEqual([x['action'] for x in legal_actions(sample_state(opponent_can_respond=False))],['fold','call'])
        self.assertEqual(legal_actions(sample_state(hero_turn=False)),[])

    def test_every_candidate_is_in_range_and_budget(self):
        for stack in (5,8,10,16,100):
            for cap in (0,2,9,15,50):
                state=sample_state(stack=stack)
                a=legal_actions(state)
                for c in candidates(a,state,{'allowed_add':cap}):
                    spec=next(x for x in a if x['action']==c['action'])
                    self.assertLessEqual(c['amount_add'],cap)
                    self.assertLessEqual(c['amount_add'],stack)
                    self.assertGreaterEqual(c['amount_add'],spec['min_add'])
                    self.assertLessEqual(c['amount_add'],spec['max_add'])


class BudgetTests(unittest.TestCase):
    def test_auto_stack_does_not_double_count_bets_or_history(self):
        for config in (None, validate_budget({}), validate_budget({'unit':'EUR'})):
            for net in (-1000, 0, 500):
                b=budget_status(config,sample_state(invested=6),net)
                self.assertEqual(b['allowed_add'],194)
                self.assertEqual(b['observed_stack'],194)
                self.assertIsNone(b['account_balance'])
                self.assertFalse(b['manual_limits'])
                self.assertFalse(b['unavailable'])

    def test_unknown_stack_never_uses_fallback_or_calls_ai(self):
        for raw in (None,''):
            state=simulation_state({'hero':'Ah Qh','stack':raw})
            r=assess(state,None,0,[])
            self.assertIsNone(r['recommendation']['candidate_id'])
            self.assertFalse(r['budget']['configured'])
            self.assertTrue(r['blocked'])
            self.assertEqual(r['candidates'],[])
        self.assertIsNone(simulation_state({'hero':'Ah Qh'})['stack'])

    def test_optional_limits_are_independent_and_stack_is_hard_cap(self):
        hand_only=validate_budget({'max_hand_loss':20,'unit':'chips'})
        self.assertEqual(budget_status(hand_only,sample_state())['allowed_add'],14)
        stop_only=validate_budget({'stop_loss':200,'unit':'chips'})
        self.assertEqual(budget_status(stop_only,sample_state(),net=-180)['allowed_add'],14)
        self.assertEqual(budget_status(stop_only,sample_state(),net=1000)['allowed_add'],194)
        self.assertIsNone(validate_budget({'stop_loss':'','max_hand_loss':None})['unit'])

    def test_stack_zero_is_observed_not_unknown(self):
        b=budget_status(None,sample_state(stack=0))
        self.assertTrue(b['configured'])
        self.assertEqual(b['observed_stack'],0)
        self.assertFalse(b['unavailable'])
        self.assertEqual(b['allowed_add'],0)

    def test_balance_cannot_be_manually_configured(self):
        for field in ('bankroll','stack','account_balance'):
            with self.assertRaises(ValueError):validate_budget({field:1000})
        for value in (0,-1,False,float('nan'),float('inf'),2.001):
            with self.assertRaises(ValueError):validate_budget({'stop_loss':value,'unit':'chips'})

    def test_invested_and_previous_losses_are_counted(self):
        b=budget_status(LIMITS,sample_state(invested=30),net=-160)
        self.assertEqual(b['allowed_add'],10)
        self.assertEqual(b['observed_stack'],194)
        self.assertIsNone(b['account_balance'])

    def test_stop_and_hand_limit(self):
        b=budget_status(LIMITS,sample_state(invested=100))
        self.assertEqual(b['allowed_add'],0)
        b=budget_status(LIMITS,sample_state(),net=-200)
        self.assertTrue(b['stop_session'])
        r=assess(sample_state(),LIMITS,-200,[])
        self.assertEqual(r['recommendation']['candidate_id'],'fold:0')

    def test_wrong_unit_blocks_proposal(self):
        r=assess(sample_state(),{**LIMITS,'unit':'EUR'},0,[])
        self.assertIsNone(r['recommendation']['candidate_id'])
        self.assertTrue(r['blocked'])
        self.assertEqual(r['budget']['unit'],'chips')

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):validate_budget({**LIMITS,'stop_loss':2.001})
        with self.assertRaises(ValueError):validate_budget({**LIMITS,'max_hand_loss':0})


class ArchiveStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'archive.sqlite3'
        self.archive=Archive(self.path)
        self.hands=parse_histories(DEMO)['hands']
        self.study=Study(self.archive,lambda:copy.deepcopy(self.hands))
        self.idx=next(i for i,s in enumerate(self.hands[0]['snapshots']) if s['hero_decision'] and s['street']=='FLOP' and s['call'])
        self.request={'source':'demo','hand_id':'100001','step':self.idx}

    def tearDown(self):self.tmp.cleanup()

    def test_persistence_dedup_and_conflict_are_atomic(self):
        self.assertEqual(self.archive.import_hands(self.hands)['added'],2)
        self.assertEqual(Archive(self.path).import_hands(self.hands)['duplicates'],2)
        changed=copy.deepcopy(self.hands[0]);changed['hero_net']=1
        extra=copy.deepcopy(self.hands[0]);extra['id']='99999'
        with self.assertRaises(ValueError):self.archive.import_hands([extra,changed])
        self.assertEqual(len(self.archive.hands()),2)
        self.archive.save_budget(LIMITS)
        self.assertEqual(Archive(self.path).budget()['stop_loss'],200)
        self.assertNotIn('bankroll',Archive(self.path).budget())

    def test_old_bankroll_migrates_to_limits_without_changing_stack(self):
        with self.archive.connect() as db:
            db.execute("INSERT INTO settings VALUES ('budget',?)",(json.dumps({**LIMITS,'bankroll':1,'demo':False}),))
        loaded=Archive(self.path).budget()
        self.assertNotIn('bankroll',loaded)
        b=budget_status(loaded,sample_state())
        self.assertEqual(b['observed_stack'],194)
        self.assertEqual(b['allowed_add'],94)

    def test_replay_stack_changes_with_each_snapshot_and_ignores_client_override(self):
        self.archive.import_hands(self.hands)
        for index,snapshot in enumerate(self.hands[0]['snapshots']):
            hero=next(p for p in snapshot['players'] if p['name']==self.hands[0]['hero'])
            report,_=self.study.prepare({**self.request,'source':'archive','step':index,
                                        'stack':999999,'account_balance':999999})
            self.assertEqual(report['budget']['observed_stack'],hero['stack'])
            self.assertEqual(report['budget']['allowed_add'],hero['stack'])
            self.assertIsNone(report['budget']['account_balance'])

    def test_missing_stack_skips_paid_call_and_simulation_provenance_is_explicit(self):
        invoke=MagicMock()
        report=self.study.recommend({'source':'simulation','scenario':{'hero':'Ah Qh'},
                                     'use_ai':True,'consent':True},invoke)
        invoke.assert_not_called()
        self.assertEqual(report['ai_status'],'skipped')
        state=simulation_state({'hero':'Ah Qh','stack':12,'unit':'EUR','account_balance':1000,'budget_source':'hand_history'})
        self.assertEqual(state['budget_source'],'simulation')
        self.assertEqual(budget_status(None,state)['unit'],'EUR')
        self.assertIsNone(budget_status(None,state)['account_balance'])

    def test_previous_hands_only_and_no_future_leak(self):
        report,payload=self.study.prepare(self.request)
        self.assertEqual(report['past_hands'],0)
        self.assertEqual(report['opponents'][0]['hands'],0)
        text=json.dumps(payload)
        self.assertNotIn('Kh',text)
        self.assertNotIn('Jc',text)
        self.assertNotIn('Alex',text)
        self.assertNotIn('100001',text)
        self.assertNotIn('hero_net',text)
        second=self.hands[1]
        idx=next(i for i,s in enumerate(second['snapshots']) if s['hero_decision'] and s['street']=='FLOP')
        report,_=self.study.prepare({'source':'demo','hand_id':second['id'],'step':idx})
        self.assertEqual(report['past_hands'],1)
        self.assertEqual(report['budget']['net_before_hand'],72)

    def test_stats_are_observed_and_sample_aware(self):
        p=profiles(self.hands[:1],['Alex'])[0]
        self.assertEqual(p['vpip'],1)
        self.assertEqual(p['pfr'],0)
        self.assertAlmostEqual(p['aggression_frequency'],2/3)
        self.assertEqual(p['sample_quality'],'insufficient')
        self.assertEqual(p['fold_to_bet'],0)
        self.assertEqual(p['fold_opportunities'],1)

    def test_archived_stack_automatic_and_optional_session_start(self):
        self.archive.import_hands(self.hands)
        req={**self.request,'source':'archive'}
        automatic=self.study.recommend(req)
        self.assertIsNotNone(automatic['selected_action'])
        self.assertEqual(automatic['budget']['observed_stack'],194)
        self.assertEqual(automatic['budget']['allowed_add'],194)
        self.assertEqual(automatic['budget']['source'],'hand_history')
        self.assertIsNone(automatic['budget']['account_balance'])
        self.archive.save_budget(LIMITS)
        self.assertIsNotNone(self.study.recommend(req)['selected_action'])
        self.archive.save_budget({**LIMITS,'start_hand_id':'100002'})
        self.assertIsNone(self.study.recommend(req)['selected_action'])

    def test_ai_response_validated_cached_and_budget_keyed(self):
        counter=[]
        def ai(payload,schema):
            counter.append(payload)
            return {'candidate_id':'call:800','confidence':'low','rationale':'Ipotesi di call da verificare sul range.',
                    'assumptions':['Range non noto.'],'alternatives':[]}
        req={**self.request,'use_ai':True,'consent':True}
        first=self.study.recommend(req,ai,'model','prompt')
        second=self.study.recommend(req,ai,'model','prompt')
        self.assertEqual(first['ai_status'],'completed')
        self.assertEqual(second['ai_status'],'cached')
        self.assertEqual(len(counter),1)
        self.assertEqual(first['selected_action']['amount_add'],8)
        self.study.recommend(req,ai,'different-model','prompt')
        self.assertEqual(len(counter),2)

    def test_illegal_model_action_cannot_replace_local_choice(self):
        def ai(*args):return {'candidate_id':'all_in:99999999','confidence':'high','rationale':'Punta tutto.', 'assumptions':[],'alternatives':[]}
        r=self.study.recommend({**self.request,'use_ai':True,'consent':True},ai)
        self.assertEqual(r['ai_status'],'fallback')
        self.assertEqual(r['recommendation']['source'],'local')
        with self.assertRaises(ValueError):self.study.recommend({**self.request,'use_ai':True},ai)

    def test_changed_budget_invalidates_cached_move(self):
        self.archive.import_hands(self.hands)
        self.archive.save_budget(LIMITS)
        req={**self.request,'source':'archive','use_ai':True,'consent':True}
        def ai(*args):return {'candidate_id':'call:800','confidence':'low','rationale':'Call di studio.', 'assumptions':[],'alternatives':[]}
        self.assertEqual(self.study.recommend(req,ai)['ai_status'],'completed')
        self.archive.save_budget({**LIMITS,'max_hand_loss':10})
        result=self.study.recommend(req,ai)
        self.assertEqual(result['budget']['allowed_add'],4)
        self.assertEqual(result['ai_status'],'fallback')
        self.assertEqual(result['selected_action']['action'],'fold')

    def test_inflight_duplicate_does_not_spend_twice(self):
        started,release=threading.Event(),threading.Event()
        calls=[]
        def ai(*args):
            calls.append(1);started.set();release.wait(3)
            return {'candidate_id':'call:800','confidence':'low','rationale':'Call di studio.', 'assumptions':[],'alternatives':[]}
        req={**self.request,'use_ai':True,'consent':True}
        thread=threading.Thread(target=lambda:self.study.recommend(req,ai))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.study.recommend(req,ai)['ai_status'],'pending')
            self.assertEqual(len(calls),1)
        finally:
            release.set();thread.join(3)

    def test_unknown_source_and_partial_call_fail_closed(self):
        with self.assertRaises(ValueError):self.study.prepare({'source':'live'})
        state=simulation_state({'hero':'Ah Qh','board':'Jh Th 2c','pot':20,'current_bet':8,'stack':5})
        self.assertTrue(state['complex_pot'])
        self.assertIsNone(assess(state,LIMITS,0,[])['recommendation']['candidate_id'])


class RaiseHistoryTests(unittest.TestCase):
    def test_minimum_full_raise_and_hero_rights(self):
        h=parse_histories(DEMO)['hands'][0]
        flop=next(s for s in h['snapshots'] if s['label']=='Alex: bets 8')
        self.assertEqual(flop['min_raise_increment'],8)
        self.assertTrue(flop['raise_reopened'])
        pre=next(s for s in h['snapshots'] if s['label']=='Hero: raises 4 to 6')
        self.assertEqual(pre['min_raise_increment'],4)
        self.assertFalse(pre['raise_reopened'])

    def test_short_raise_does_not_reopen_for_prior_raiser(self):
        text="""PokerStars Hand #777: Hold'em No Limit (1/2) - 2026/10/01
Table 'Test' 3-max Seat #1 is the button
Seat 1: Hero (100 in chips)
Seat 2: A (7 in chips)
Seat 3: B (100 in chips)
A: posts small blind 1
B: posts big blind 2
*** HOLE CARDS ***
Dealt to Hero [As Ah]
Hero: raises 4 to 6
A: raises 1 to 7 and is all-in
B: folds
Hero: calls 1
*** FLOP *** [2c 3d 4h]
*** TURN *** [2c 3d 4h] [8s]
*** RIVER *** [2c 3d 4h 8s] [9c]
*** SHOW DOWN ***
Hero collected 16 from pot
*** SUMMARY ***
Total pot 16 | Rake 0
"""
        h=parse_histories(text)['hands'][0]
        i=next(i for i,s in enumerate(h['snapshots']) if s['hero_decision'] and s['current_bet']==7)
        state,_,_=replay_state(h,i)
        self.assertFalse(state['raise_reopened'])
        self.assertEqual([a['action'] for a in legal_actions(state)],['fold','call'])


class CostLimitTests(unittest.TestCase):
    @patch.dict(os.environ,{'OPENAI_API_KEY':'test-only','OPENAI_MODEL':'test-only','OPENAI_MAX_CALLS':'0'})
    @patch('server.urllib.request.build_opener')
    def test_zero_budget_never_connects(self,network):
        with self.assertRaisesRegex(ValueError,'Limite di chiamate'):
            server.response_text('test',[])
        network.assert_not_called()


if __name__=='__main__':unittest.main()

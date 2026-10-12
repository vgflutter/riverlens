import itertools
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import analyze, cards, evaluate, DECK
from history import parse_histories, parse_one
import server

DEMO = (Path(__file__).resolve().parents[1] / 'examples' / 'demo.txt').read_text()


def reference_five(hand):
    """Independent five-card oracle for checking the seven-card evaluator."""
    ranks = sorted(['23456789TJQKA'.index(c[0])+2 for c in hand], reverse=True)
    groups = sorted(((ranks.count(r), r) for r in set(ranks)), reverse=True)
    same_suit = len({c[1] for c in hand}) == 1
    consecutive = len(set(ranks)) == 5 and ranks[0]-ranks[-1] == 4
    high = ranks[0] if consecutive else 5 if ranks == [14,5,4,3,2] else 0
    if high and same_suit: return (8, high)
    if groups[0][0] == 4: return (7, groups[0][1], groups[1][1])
    if [g[0] for g in groups] == [3,2]: return (6, groups[0][1], groups[1][1])
    if same_suit: return (5, *ranks)
    if high: return (4, high)
    if groups[0][0] == 3: return (3, groups[0][1], *sorted([g[1] for g in groups[1:]], reverse=True))
    if [g[0] for g in groups] == [2,2,1]: return (2, *sorted([groups[0][1],groups[1][1]], reverse=True), groups[2][1])
    if groups[0][0] == 2: return (1, groups[0][1], *sorted([g[1] for g in groups[1:]], reverse=True))
    return (0, *ranks)


class EvaluatorTests(unittest.TestCase):
    def test_against_independent_five_card_oracle(self):
        rng=random.Random(19)
        for _ in range(600):
            hand=rng.sample(DECK,7)
            self.assertEqual(evaluate(hand),max(reference_five(h) for h in itertools.combinations(hand,5)))

    def test_wheel_and_two_trips(self):
        self.assertEqual(evaluate(cards('As 2s 3s 4s 5s Kd Qh')),(8,5))
        self.assertEqual(evaluate(cards('Ah Ad As Kh Kd Ks 2c')),(6,14,13))

    def test_exact_shared_board_and_call_formula(self):
        a=analyze(dict(hero='2c 3d',board='As Ks Qs Js Ts',villain='7h 8h',pot=100,call=20))
        self.assertEqual(a['equity'],.5)
        self.assertEqual(a['tie'],1)
        self.assertEqual(a['win'],0)
        self.assertEqual(a['samples'],1)
        self.assertAlmostEqual(a['pot_odds'],1/6)
        self.assertEqual(a['call_ev_model'],40)

    def test_multiway_shared_board(self):
        a=analyze(dict(hero='2c 3d',board='As Ks Qs Js Ts',opponents=3,iterations=100))
        self.assertEqual(a['equity'],.25)

    def test_aa_against_kk(self):
        a=analyze(dict(hero='Ac Ad',villain='Kc Kd',iterations=5000))
        self.assertTrue(.79<a['equity']<.85,a)

    def test_reject_invalid_states(self):
        for change in ({'hero':'Ah Ah'}, {'board':'Ah Kd Qs'}, {'board':'2c 3d'}, {'pot':float('nan')},
                       {'opponents':1.2}, {'call':30,'pot':20}, {'complex_pot':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                analyze(dict(hero='Ah Qh',**{k:v for k,v in change.items() if k!='hero'}) if 'hero' not in change else change)


class ImportTests(unittest.TestCase):
    def setUp(self): self.hands=parse_histories(DEMO)['hands']

    def test_flop_pot_and_no_future_cards(self):
        h=self.hands[0]
        s=next(s for s in h['snapshots'] if s['hero_decision'] and s['street']=='FLOP' and s['call']>0)
        self.assertEqual(s['pot'],20)
        self.assertEqual(s['call'],8)
        self.assertEqual(s['board'],['Jh','Th','2c'])
        self.assertEqual(s['opponents'],1)
        self.assertTrue(all(not p['shown'] for p in s['players']))
        self.assertEqual(next(p for p in s['players'] if p['name']=='Hero')['stack'],194)

    def test_returned_bet_and_chip_conservation(self):
        self.assertEqual(self.hands[1]['total_pot'],13)
        for hand in self.hands:
            self.assertEqual(sum(p['stack'] for p in hand['snapshots'][-1]['players']),600)

    def test_partial_invalid_and_unknown_actions(self):
        first=DEMO.split('PokerStars Hand #100002')[0]
        for text in [first.split('*** SUMMARY ***')[0],first.replace('Total pot 144','Total pot 145'),
                     first.replace('Alex: bets 8','Alex: makes a strange bet 8'),
                     first.replace('Hero collected 144 from pot',''),
                     first.replace('[Jh Th 2c] [3s]','[Jh Th 4c] [3s]')]:
            with self.subTest(text=text[-60:]),self.assertRaises(ValueError): parse_one(text)

    def test_batch_keeps_valid_hands(self):
        result=parse_histories(DEMO+'\nPokerStars Hand #77: Omaha Pot Limit\n*** SUMMARY ***')
        self.assertEqual(len(result['hands']),2)
        self.assertEqual(len(result['errors']),1)

    def test_rake(self):
        first=DEMO.split('PokerStars Hand #100002')[0].replace('Hero collected 144','Hero collected 142').replace('Rake 0','Rake 2')
        self.assertEqual(parse_one(first)['total_pot'],144)


class AiBoundaryTests(unittest.TestCase):
    def test_requires_explicit_consent(self):
        with self.assertRaises(ValueError):server.explain({'scenario':{'hero':'Ac Ad'}})
        with self.assertRaises(ValueError):server.vision({'image':'data:image/png;base64,YQ=='})

    @patch('server.response_text',return_value='Spiegazione di prova')
    @patch.dict('os.environ',{'OPENAI_MODEL':'mock-model'})
    def test_explanation_recomputes_metrics(self,mock):
        result=server.explain({'consent':True,'scenario':{'hero':'Ac Ad','iterations':100},'equity':0})
        self.assertEqual(result['text'],'Spiegazione di prova')
        payload=json.loads(mock.call_args.args[1][0]['text'])
        self.assertGreater(payload['equity'],.7)
        self.assertNotIn('OPENAI_API_KEY',json.dumps(payload))

    @patch('server.response_text',return_value='{"hero":["Ah","Kd"],"board":[],"pot":20,"call":8,"opponents":1,"notes":"Verifica"}')
    def test_vision_transcription(self,mock):
        result=server.vision({'consent':True,'archived':True,'image':'data:image/png;base64,YQ=='})
        self.assertEqual(result['hero'],['Ah','Kd'])

    @patch('server.response_text',return_value='{"hero":["Ah","Ah"],"board":[]}')
    def test_vision_rejects_duplicates(self,mock):
        with self.assertRaises(ValueError):server.vision({'consent':True,'archived':True,'image':'data:image/png;base64,YQ=='})


if __name__=='__main__': unittest.main()

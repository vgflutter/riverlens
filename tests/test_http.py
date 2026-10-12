import io
import json
import os
from pathlib import Path
import re
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.archive_patch=patch.object(server,'ARCHIVE',server.Archive(Path(cls.tmp.name)/'test.sqlite3'))
        cls.archive_patch.start()
        cls.study_patch=patch.object(server,'STUDY',server.Study(server.ARCHIVE,lambda:server.parse_histories((server.ROOT/'examples'/'demo.txt').read_text())['hands']))
        cls.study_patch.start()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'
        cls.client=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        cls.archive_patch.stop()
        cls.study_patch.stop()
        cls.tmp.cleanup()

    def request(self,path,data=None,headers=None):
        merged={'Content-Type':'application/json','X-RiverLens-Token':server.TOKEN}
        merged.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=merged)
        return self.client.open(req,timeout=4)

    def test_page_and_import_to_analysis(self):
        with self.request('/') as response:
            html=response.read().decode()
            self.assertIn('RiverLens',html)
            self.assertIn(server.TOKEN,html)
            self.assertNotIn('__TOKEN__',html)
        demo=(server.ROOT/'examples'/'demo.txt').read_text()
        with self.request('/api/import',{'text':demo}) as response:
            hand=json.load(response)['hands'][0]
        snap=next(s for s in hand['snapshots'] if s['hero_decision'] and s['street']=='FLOP' and s['call'])
        with self.request('/api/analyze',{'hero':snap['hole'],'board':snap['board'],'opponents':snap['opponents'],'pot':snap['pot'],'call':snap['call']}) as response:
            self.assertAlmostEqual(json.load(response)['pot_odds'],8/28)

    def test_secrets_and_request_boundaries(self):
        for path,data,headers,status in [('/.env',None,{},404),('/api/analyze',{'hero':'Ac Ad'},{'X-RiverLens-Token':'wrong'},403),
                ('/api/analyze',{'hero':'Ac Ad'},{'Origin':'https://example.com'},403),
                ('/api/analyze',{'hero':'Ac Ad'},{'Host':'evil.test'},403),
                ('/api/analyze',{'hero':'Ac Ac'},{},400),('/api/analyze',[],{},400)]:
            with self.subTest(path=path,headers=headers):
                with self.assertRaises(urllib.error.HTTPError) as error:self.request(path,data,headers)
                self.assertEqual(error.exception.code,status)

    def test_budget_and_automatic_recommendation_flow(self):
        with self.request('/api/import',{'text':(server.ROOT/'examples'/'demo.txt').read_text()}) as reply:
            hand=json.load(reply)['hands'][0]
        step=next(i for i,s in enumerate(hand['snapshots']) if s['hero_decision'] and s['street']=='FLOP' and s['call'])
        with self.request('/api/session',{'bankroll':1000,'stop_loss':200,'max_hand_loss':100,'unit':'chips'}) as reply:
            self.assertEqual(json.load(reply)['budget']['bankroll'],1000)
        with self.request('/api/recommend',{'source':'archive','hand_id':hand['id'],'step':step}) as reply:
            report=json.load(reply)
            self.assertIsNotNone(report['selected_action'])
            self.assertLessEqual(report['selected_action']['amount_add'],report['budget']['allowed_add'])
        with self.request('/api/archive') as reply:
            self.assertEqual(len(json.load(reply)['hands']),2)


class ApiAdapterTests(unittest.TestCase):
    @patch.dict(os.environ,{'OPENAI_API_KEY':'test-secret','OPENAI_MODEL':'test-model'})
    @patch('server.urllib.request.build_opener')
    def test_response_adapter(self,build):
        mock=MagicMock()
        mock.__enter__.return_value=io.BytesIO(json.dumps({'status':'completed','output':[{'type':'reasoning'},
                {'type':'message','content':[{'type':'output_text','text':'Analisi di prova'}]}]}).encode())
        build.return_value.open.return_value=mock
        self.assertEqual(server.response_text('Tutor',[{'type':'input_text','text':'Scenario'}]),'Analisi di prova')
        req=build.return_value.open.call_args.args[0]
        body=json.loads(req.data)
        self.assertEqual(req.full_url,'https://api.openai.com/v1/responses')
        self.assertFalse(body['store'])
        self.assertEqual(body['model'],'test-model')
        self.assertEqual(req.headers['Authorization'],'Bearer test-secret')

    @patch.dict(os.environ,{'OPENAI_API_KEY':'test-secret','OPENAI_MODEL':'test-model'})
    @patch('server.urllib.request.build_opener')
    def test_partial_response_not_presented_as_complete(self,build):
        reply=MagicMock()
        reply.__enter__.return_value=io.BytesIO(b'{"status":"incomplete","output":[]}')
        build.return_value.open.return_value=reply
        with self.assertRaisesRegex(ValueError,'incompleta'):server.response_text('Tutor',[])

    @patch.dict(os.environ,{'OPENAI_API_KEY':'test-secret','OPENAI_MODEL':'test-model'})
    @patch('server.urllib.request.build_opener')
    def test_structured_output_request_and_token_usage(self,build):
        reply=MagicMock()
        reply.__enter__.return_value=io.BytesIO(b'{"status":"completed","usage":{"input_tokens":10,"output_tokens":20},"output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}]}')
        build.return_value.open.return_value=reply
        schema={'type':'object','properties':{},'required':[],'additionalProperties':False}
        before=dict(server.API_USAGE)
        server.response_text('Tutor',[],schema)
        payload=json.loads(build.return_value.open.call_args.args[0].data)
        self.assertEqual(payload['text']['format']['schema'],schema)
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(server.API_USAGE['input_tokens'],before['input_tokens']+10)


if __name__=='__main__':unittest.main()

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from storage import mysql_store


class Cursor:
    lastrowid = 91
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def execute(self, query, params=None): self.executed=(query,params)
    def executemany(self, query, rows):
        self.query=query; self.rows=rows
        assert query.count('%s') == len(rows[0])

class Connection:
    def __init__(self): self.cursor_value=Cursor(); self.committed=False
    def cursor(self): return self.cursor_value
    def commit(self): self.committed=True
    def rollback(self): pass
    def close(self): pass

class ConsensusPersistenceTest(unittest.TestCase):
    def test_audit_fields_are_inserted(self):
        conn=Connection()
        audit=json.dumps({'source':'cloud_adjudication','cloud':{'decision':{'sentiment_score':.4}}})
        item=SimpleNamespace(post_id='p',title='t',platform='x',newbie_score=1,
            newbie_confidence='high',level='中间派',sentiment_score=.4,sentiment_label='greed',
            emotion_tags=['optimism'],sentiment_confidence=.8,intent='buy',intent_strength=.7,
            position_status='none',market_outlook='bullish',content_type='opinion',key_signals=[],
            reasoning='r',matched_newbie=[],matched_pro=[],sentiment_source='llm',llm_error='',
            adjudication_source='cloud_adjudication',adjudication_json=audit)
        posts={'gold':[{'id':'p','run_id':1,'title':'t','content':'c'}]}
        with patch.object(mysql_store,'_connect',return_value=conn), patch.object(mysql_store,'_ensure_tables'):
            with patch.dict(os.environ,{'MOM_INDEX_LLM_PROFILE':'local-consensus-cloud',
                'MOM_INDEX_LLM_MODEL':'14B+27B+cloud-adjudication','MOM_INDEX_LLM_PROMPT_VERSION':'sentiment-consensus-v1'}):
                batch=mysql_store.persist_standalone_analysis({'gold':[item]},posts)
        self.assertEqual(batch,91)
        self.assertTrue(conn.committed)
        self.assertIn('adjudication_source',conn.cursor_value.query)
        self.assertEqual(conn.cursor_value.rows[0][-2: ],('cloud_adjudication',audit))

if __name__=='__main__': unittest.main()

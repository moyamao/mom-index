import unittest
from unittest.mock import patch

from analyzer.llm_consensus import adjudicate, material_disagreements, _decision


def value(**overrides):
    base = dict(content_type='opinion', sentiment_score=0.2, sentiment_label='neutral',
        emotion_tags=[], emotion_intensity=0.2, intent='neutral', intent_strength=0,
        position_status='unknown', market_outlook='unknown', investor_maturity='general',
        maturity_confidence=.7, confidence=.8, reasoning='test')
    base.update(overrides)
    return base


def response(decision=None, ok=True):
    return {'ok':ok,'model':'m','decision':decision if ok else None,'attempts':[],
            **({} if ok else {'error':'failed'})}


class ConsensusTests(unittest.TestCase):
    configs={name:{'base_url':'x','model':name,'api_key':''} for name in ('local_27b','cloud')}
    def stored(self, **overrides):
        decision=value(reasoning='14')
        decision.update(overrides)
        return dict(id=1,batch_id=2,analysis_profile='mini-14b',model_name='14b',prompt_version='v1',
            content_type=decision['content_type'],sentiment_score=decision['sentiment_score'],
            sentiment_label=decision['sentiment_label'],emotion_tags_json='[]',
            sentiment_confidence=decision['confidence'],intent=decision['intent'],
            intent_strength=decision['intent_strength'],position_status=decision['position_status'],
            market_outlook=decision['market_outlook'],level='中间派',reasoning=decision['reasoning'])
    def invoke(self, replies, stored_14b=None):
        if stored_14b is None:
            stored_14b=self.stored()
        with patch('analyzer.llm_consensus.call_model', side_effect=replies) as call:
            result,audit=adjudicate(title='t',content='c',sector='s',platform='p',configs=self.configs,
                timeout=1,stored_14b=stored_14b)
        return result,audit,call.call_count

    def test_agreement_skips_cloud_and_uses_27b(self):
        result,audit,calls=self.invoke([response(value(reasoning='27'))])
        self.assertEqual(calls,1)
        self.assertEqual(audit['source'],'local_agreement_27b')
        self.assertEqual(result.reasoning,'27')

    def test_material_label_disagreement_uses_cloud(self):
        result,audit,calls=self.invoke(
            [response(value(intent='buy')),response(value(intent='sell'))],
            stored_14b=self.stored(intent='sell'),
        )
        self.assertEqual(calls,2)
        self.assertEqual(audit['source'],'cloud_adjudication')
        self.assertEqual(result.intent,'sell')
        self.assertIn('intent',audit['disagreements'])

    def test_large_score_disagreement_uses_cloud(self):
        self.assertIn('sentiment_score',material_disagreements(_decision(value(sentiment_score=-.4)),_decision(value(sentiment_score=.1))))

    def test_cloud_failure_marks_review_and_falls_back_27b(self):
        result,audit,calls=self.invoke(
            [response(value(intent='buy')),response(ok=False)],
            stored_14b=self.stored(intent='sell'),
        )
        self.assertEqual(result.intent,'buy')
        self.assertTrue(audit['requires_review'])
        self.assertEqual(audit['source'],'cloud_failure_fallback_27b')

    def test_27b_failure_falls_back_to_stored_14b(self):
        result,audit,calls=self.invoke([response(ok=False)])
        self.assertEqual(calls,1)
        self.assertEqual(result.reasoning,'14')
        self.assertTrue(audit['requires_review'])

    def test_missing_14b_uses_27b_without_cloud(self):
        with patch('analyzer.llm_consensus.call_model', return_value=response(value(intent='buy'))) as call:
            result,audit=adjudicate(title='t',content='c',sector='s',platform='p',configs=self.configs,
                timeout=1,stored_14b=None)
        self.assertEqual(call.call_count,1)
        self.assertEqual(result.intent,'buy')
        self.assertEqual(audit['source'],'missing_14b_reference_27b')
        self.assertTrue(audit['requires_review'])

    def test_auxiliary_differences_do_not_trigger_cloud(self):
        first=_decision(value(intent_strength=.1,position_status='none',investor_maturity='novice'))
        second=_decision(value(intent_strength=.9,position_status='unknown',investor_maturity='professional'))
        self.assertEqual(material_disagreements(first,second),[])

    def test_minor_sentiment_boundary_does_not_trigger_cloud(self):
        first=_decision(value(sentiment_label='neutral',sentiment_score=.05))
        second=_decision(value(sentiment_label='mixed',sentiment_score=-.1))
        self.assertEqual(material_disagreements(first,second),[])

    def test_opposite_outlook_is_material(self):
        first=_decision(value(market_outlook='bullish'))
        second=_decision(value(market_outlook='bearish'))
        self.assertEqual(material_disagreements(first,second),['market_outlook'])


if __name__=='__main__':
    unittest.main()

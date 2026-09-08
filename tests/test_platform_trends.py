import unittest

from analyzer.platform_trends import build_platform_trends


class PlatformTrendsTest(unittest.TestCase):
    def test_sentiment_mean_and_change_use_100_point_scale(self):
        rows = [
            self._row('p1', '2026-09-07 09:00:00', 0.2),
            self._row('p2', '2026-09-07 10:00:00', 0.8),
        ]

        result = build_platform_trends(rows)
        hourly = next(item for item in result['series'] if item['period'] == 'hourly')

        self.assertEqual(hourly['records'][0]['sentiment_mean'], 20.0)
        self.assertEqual(hourly['records'][1]['sentiment_mean'], 80.0)
        self.assertEqual(hourly['records'][1]['change'], 60.0)
        self.assertIn('-100 至 +100', result['note'])

    @staticmethod
    def _row(post_id, post_datetime, sentiment_score):
        return {
            'analysis_profile': 'mini-14b',
            'sector': 'storage',
            'platform': 'xueqiu',
            'post_id': post_id,
            'post_datetime': post_datetime,
            'analysis_engine': 'llm',
            'content_type': 'opinion',
            'level': '观点帖',
            'sentiment_score': sentiment_score,
            'intent': 'neutral',
        }


if __name__ == '__main__':
    unittest.main()

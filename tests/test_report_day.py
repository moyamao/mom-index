import unittest
from datetime import date, datetime

from analyzer.platform_trends import build_model_sector_history, build_platform_trends
from post_time import report_day
from storage.mysql_store import _row_day
from unittest.mock import patch
import pipeline


class ReportDayTest(unittest.TestCase):
    def test_overnight_collection_assigns_midnight_post_to_previous_day(self):
        published = datetime(2026, 10, 16, 0, 1)
        completed = datetime(2026, 10, 16, 1, 22)
        self.assertEqual(report_day(published, completed), date(2026, 10, 15))
        self.assertEqual(_row_day({"post_datetime": published, "source_run_at": completed}), date(2026, 10, 15))

    def test_daytime_run_and_earlier_publication_keep_calendar_day(self):
        self.assertEqual(report_day("2026-10-16 00:01:00", "2026-10-16 12:00:00"), date(2026, 10, 16))
        self.assertEqual(report_day("2026-10-15 23:30:00", "2026-10-16 01:22:00"), date(2026, 10, 15))

    def test_overnight_pipeline_cache_uses_prior_report_date(self):
        with patch.object(pipeline, "beijing_now", return_value=datetime(2026, 10, 16, 1, 22)):
            snapshot = pipeline._build_current_snapshot({})
        self.assertEqual(snapshot["date"], "2026-10-15")
        self.assertEqual(snapshot["timestamp"], "2026-10-16 01:22:00")

    def test_history_and_daily_trend_use_report_day_but_hourly_uses_publication(self):
        row = {
            "analysis_profile": "mini-14b", "sector": "gold", "platform": "xueqiu",
            "post_id": "one", "post_datetime": datetime(2026, 10, 16, 0, 1),
            "source_run_at": datetime(2026, 10, 16, 1, 22),
            "analysis_engine": "llm", "content_type": "opinion", "level": "观点帖",
            "sentiment_score": 0.8, "intent": "hold",
        }
        self.assertEqual(build_model_sector_history([row])["mini-14b"]["gold"][0]["date"], "2026-10-15")
        series = build_platform_trends([row])["series"]
        self.assertEqual(next(item for item in series if item["period"] == "daily")["records"][0]["date"], "2026-10-15")
        self.assertEqual(next(item for item in series if item["period"] == "hourly")["records"][0]["date"], "2026-10-16 00:00:00")


if __name__ == "__main__":
    unittest.main()

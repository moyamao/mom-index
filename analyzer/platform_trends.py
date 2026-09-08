"""Publication-time sentiment trends; each source post contributes once."""
from collections import defaultdict
from datetime import datetime, timedelta

from post_time import normalize_social_datetime


def build_platform_trends(rows):
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        profile = row.get('analysis_profile') or 'legacy'
        key = (row['sector'], row['platform'], row['post_id'], profile)
        if not row['post_id'] or key in seen:
            continue
        seen.add(key)
        stamp = normalize_social_datetime(row.get('post_datetime'))
        if (not stamp or row.get('analysis_engine') != 'llm'
                or row.get('content_type') != 'opinion'
                or row.get('level') in {'垃圾帖', '资讯帖'}):
            continue
        dt = datetime.fromisoformat(stamp)
        buckets = {'hourly': dt.strftime('%Y-%m-%d %H:00:00'),
                   'daily': dt.strftime('%Y-%m-%d'),
                   'weekly': (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')}
        for period, bucket in buckets.items():
            groups[(profile, row['sector'], row['platform'], period, bucket)].append(row)
    series = defaultdict(list)
    for (profile, sector, platform, period, bucket), items in sorted(groups.items()):
        count = len(items)
        # LLM scores are stored on [-1, 1]; expose the same [-100, 100]
        # sentiment index used by the dashboard.
        mean = sum(float(x['sentiment_score']) for x in items) / count * 100
        records = series[(profile, sector, platform, period)]
        current_dt = datetime.fromisoformat(bucket)
        step = timedelta(hours=1) if period == 'hourly' else timedelta(days=7 if period == 'weekly' else 1)
        previous = records[-1] if records else None
        contiguous = previous and datetime.fromisoformat(previous['date']) + step == current_dt
        records.append({'date': bucket, 'post_count': count, 'sentiment_mean': round(mean, 4),
                        'change': round(mean - previous['sentiment_mean'], 4) if contiguous else None,
                        'fear_ratio': round(sum(float(x['sentiment_score']) < 0 for x in items) / count, 4),
                        'greed_ratio': round(sum(float(x['sentiment_score']) > 0 for x in items) / count, 4),
                        'buy_count': sum(x['intent'] == 'buy' for x in items),
                        'sell_count': sum(x['intent'] == 'sell' for x in items)})
    return {'timezone': 'Asia/Shanghai', 'time_basis': 'post_datetime',
            'note': '按模型与发布时间分组；缺失时段不补零；change仅比较连续时段；情绪指数范围为 -100 至 +100。',
            'series': [{'analysis_profile': profile, 'sector': s, 'platform': p, 'period': t, 'records': r}
                       for (profile, s, p, t), r in sorted(series.items())]}


def build_model_sector_history(rows):
    """Aggregate deduplicated opinion sentiment by profile, sector and source day."""
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        profile = row.get('analysis_profile') or 'legacy'
        post_id = row.get('post_id') or ''
        key = (profile, row.get('sector'), row.get('platform'), post_id)
        if not post_id or key in seen:
            continue
        seen.add(key)
        stamp = normalize_social_datetime(row.get('post_datetime'))
        if (not stamp or row.get('analysis_engine') != 'llm'
                or row.get('content_type') != 'opinion'
                or row.get('level') in {'垃圾帖', '资讯帖'}):
            continue
        day = datetime.fromisoformat(stamp).strftime('%Y-%m-%d')
        groups[(profile, row['sector'], day)].append(float(row.get('sentiment_score') or 0))

    history = defaultdict(lambda: defaultdict(list))
    for (profile, sector, day), scores in sorted(groups.items()):
        history[profile][sector].append({
            'date': day,
            'index': round(sum(scores) / len(scores) * 100, 1),
            'post_count': len(scores),
        })
    return {profile: dict(sectors) for profile, sectors in history.items()}


def fetch_platform_trends():
    from storage.mysql_store import _connect, mysql_enabled
    if not mysql_enabled():
        return {'timezone': 'Asia/Shanghai', 'series': [], 'note': 'MySQL未启用'}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT p.sector, p.platform, p.post_id, p.post_datetime,
                       a.level, a.sentiment_score, a.intent, a.analysis_profile,
                       a.analysis_engine, a.content_type
                FROM mom_index_posts p JOIN mom_index_analysis a
                  ON a.run_id=p.run_id AND a.sector=p.sector
                 AND a.platform=p.platform AND a.post_id=p.post_id
                WHERE p.post_datetime IS NOT NULL
                ORDER BY p.run_id DESC, p.id DESC, a.id DESC
            ''')
            return build_platform_trends(cur.fetchall())
    finally:
        conn.close()


def fetch_model_sector_history():
    from storage.mysql_store import _connect, mysql_enabled
    if not mysql_enabled():
        return {}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT p.sector, p.platform, p.post_id, p.post_datetime,
                       a.level, a.sentiment_score, a.analysis_profile,
                       a.analysis_engine, a.content_type
                FROM mom_index_posts p JOIN mom_index_analysis a
                  ON a.run_id=p.run_id AND a.sector=p.sector
                 AND a.platform=p.platform AND a.post_id=p.post_id
                WHERE p.post_datetime IS NOT NULL
                ORDER BY a.id DESC
            ''')
            return build_model_sector_history(cur.fetchall())
    finally:
        conn.close()

"""
宝妈指数 LLM 分析引擎
多维度精准分类，输出有理有据的判定逻辑
"""
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import time

from .llm_sentiment import (
    analyze_sentiment_with_llm,
    llm_max_posts_per_run,
    llm_ready,
    llm_request_interval_seconds,
    should_run_llm_second_pass,
    llm_model_name,
    llm_profile,
    llm_prompt_version,
    llm_mode,
)

# ============================================================
# 信号定义库
# ============================================================

@dataclass
class Signal:
    """单个判定信号"""
    name: str
    weight: float          # 权重 (-5 到 +5, 正=小白, 负=专业)
    description: str       # 人类可读的描述

# ---- 小白信号 (正向) ----
NEWBIE_SIGNALS = [
    Signal("身份自述", 8, "明确自称小白/新手/刚入门/宝妈"),
    Signal("知识求助", 6, "在问基础问题（怎么买/在哪看/什么意思）"),
    Signal("决策依赖", 7, "请求他人替自己做投资决策（该不该/要不要/能不能）"),
    Signal("情绪恐慌", 5, "表达明显的恐惧/焦虑/后悔情绪"),
    Signal("跟风行为", 6, "提及跟别人买/听说/博主推荐/朋友说"),
    Signal("过度乐观", 4, "非理性乐观（梭哈/稳赚/必涨/躺赚）"),
    Signal("短期思维", 3, "关注明天/后天/今天涨跌，非长期视角"),
    Signal("金额极小", 2, "讨论几百几千块的投资，试水心态"),
    Signal("术语缺失", 4, "全文无任何专业术语（PE/PB/ETF/溢价率等）"),
    Signal("互动异常", 3, "大量emoji/感叹号/问号，情绪化表达"),
]

# ---- 专业信号 (负向) ----
PRO_SIGNALS = [
    Signal("专业术语", -5, "使用PE/PB/ROE/基本面/技术面/估值等专业词汇"),
    Signal("策略思维", -4, "讨论定投/仓位/分散/对冲/止损等策略"),
    Signal("数据引用", -4, "引用具体数据/财报/宏观经济指标"),
    Signal("风险意识", -3, "明确提及风险/仅供参考/不构成建议"),
    Signal("长期视角", -3, "讨论长期趋势/定投计划/年度收益"),
    Signal("冷静表达", -2, "理性分析，客观陈述，无情绪化表达"),
]

# 关键词匹配规则
NEWBIE_KEYWORDS = {
    "身份自述": ["小白", "新手", "新人", "刚入", "第一次", "菜鸟", "萌新", "宝妈", "全职妈妈", "学生党"],
    "知识求助": ["不懂", "请教", "各位大哥", "大佬", "请问", "有没有人", "谁知道", "求助", "怎么买", "在哪看", "什么意思"],
    "决策依赖": ["该不该", "要不要", "能不能", "可以吗", "行不行", "靠谱吗", "还能上车吗", "现在入手", "还会涨吗", "还会跌吗"],
    "情绪恐慌": ["好慌", "救命", "完了", "哭了", "怕了", "吓死", "心态崩", "太惨", "亏死了", "割肉", "后悔", "早知道"],
    "跟风行为": ["跟着买的", "别人推荐", "博主说", "听说", "朋友说", "同事买", "群里说", "都在买"],
    "过度乐观": ["冲", "梭哈", "稳赚", "必涨", "躺赚", "满仓干", "起飞", "暴富"],
    "短期思维": ["明天涨", "今天跌", "后天走势", "今天买"],
}

PRO_KEYWORDS = {
    "专业术语": ["PE", "PB", "ROE", "溢价率", "折价", "估值", "基本面", "技术面", "MACD", "KDJ", "ETF", "联接", "LOF"],
    "策略思维": ["定投", "仓位", "分散", "对冲", "止损", "止盈", "网格", "轮动", "资产配置"],
    "数据引用": ["季报", "年报", "GDP", "CPI", "非农", "美联储", "加息", "降息", "收益率", "年化"],
    "风险意识": ["仅供参考", "不构成建议", "个人观点", "理性投资", "风险自担", "谨慎"],
    "长期视角": ["长期持有", "定投计划", "年度", "养老", "十年"],
    "冷静表达": ["分析", "观点", "看法", "逻辑", "原因在于"],
}

# ---- 买入/卖出意图关键词 ----
BUY_KEYWORDS = [
    "上车", "冲", "梭哈", "all in", "满仓", "抄底", "加仓", "买入", "买了", "入手", "已入",
    "追", "杀入", "建仓", "补仓", "定投", "已上车",
    "还能买吗", "还能上车吗", "可以买吗", "能不能买", "要不要入",
    "想买", "想入", "心动", "看着眼馋", "忍不住",
    "后悔没买", "错过", "买少了", "早知道就买了", "再不买",
]

SELL_KEYWORDS = [
    "割肉", "割", "止损", "清仓", "减仓", "出货", "卖了", "出了", "跑了", "走人",
    "不玩了", "离场", "下车", "赎回",
    "要不要割", "要不要走", "该不该卖", "还能留吗", "要不要清",
    "想卖", "想走", "想割", "想跑",
    "亏了", "亏麻了", "亏惨了", "深套", "套牢", "后悔买了", "被套",
    "跌麻了", "跌惨了", "血亏", "亏死", "跌死", "跌崩",
]

COMMON_SPAM_PATTERNS = [
    "我是冲着金条来的",
    "金条来的，你呢",
    "领金条",
    "签到",
    "打卡",
    "广告",
]

GOLD_ACTIVITY_SPAM_PATTERNS = [
    "这是我的实盘战绩",
    "欢迎前来pk",
    "欢迎来交流",
    "欢迎前来交流",
    "我的持仓在此",
    "晒晒我的etf持仓",
    "报名参赛",
    "红包拿",
    "实盘赛里",
]

STORAGE_INFO_PATTERNS = [
    "产业链梳理",
    "全产业链",
    "产业链图谱",
    "图谱",
    "供应商名单",
    "名单",
    "产能清单",
    "工厂、产能",
    "工厂产能",
    "核心供应商",
    "一家公司",
    "产品介绍",
    "概念股梳理",
    "概念梳理",
    "全景梳理",
    "参数对比",
    "规格对比",
    "容量对比",
    "选购指南",
    "购买建议",
    "晒单",
    "开箱",
    "作业",
    "报价",
    "价格",
    "到手价",
]

STORAGE_COMMERCE_HINTS = [
    "闪迪", "西部数据", "西数", "三星", "致态", "铠侠", "海康", "英睿达", "美光", "海力士",
    "ssd", "固态", "硬盘", "内存条", "u盘", "tf卡", "micro sd", "sd卡", "颗粒", "缓存",
]

STORAGE_SPEC_HINTS = [
    "dram", "ddr4", "ddr5", "hbm", "nand", "pcie", "nvme", "sata", "tlc", "qlc",
    "1tb", "2tb", "4tb", "8gb", "16gb", "32gb", "64gb", "6400", "7200", "6000",
]

QUESTION_HINTS = [
    "吗", "？", "?", "怎么", "为什么", "能不能", "要不要", "还能", "别慌", "怎么办",
    "是不是", "值不值", "可不可以", "同学", "注意", "去留",
]


# ============================================================
# 分析引擎
# ============================================================

@dataclass
class AnalysisResult:
    """单条帖子的完整分析结果"""
    post_id: str
    title: str
    platform: str
    sector: str
    
    # 分数
    newbie_score: float = 0.0       # 小白总分 (0-100)
    newbie_confidence: str = "low"   # 置信度: high/medium/low
    
    # 命中信号
    matched_newbie: List[Tuple[str, str, float]] = field(default_factory=list)  
    matched_pro: List[Tuple[str, str, float]] = field(default_factory=list)
    
    # 判定
    level: str = "未判定"      # 纯小白/偏小白/中间派/偏专业/专业
    reasoning: str = ""        # 人类可读的推理过程
    sentiment_score: float = 0  # -1(恐慌) ~ +1(贪婪)
    sentiment_label: str = "neutral"  # fear/greed/neutral/mixed
    emotion_intensity: float = 0.0    # 0~1 情绪强度
    sentiment_confidence: float = 0.0 # 0~1 置信度
    sentiment_source: str = "rules"   # rules / llm
    llm_error: str = ""
    intent: str = "neutral"     # buy/sell/neutral — 买入/卖出意图
    intent_strength: float = 0  # 0~1 意图强度
    
    # 用于前端展示
    key_signals: List[str] = field(default_factory=list)
    source_date: str = ""
    source_datetime: str = ""
    analysis_profile: str = "rules"
    model_name: str = "rules-v1"
    prompt_version: str = "rules-v1"


def _normalize_post_datetime(post: Dict) -> tuple[str, str]:
    """尽量统一帖子时间，优先真实发布时间，回退到采集时间。"""
    published_at = (post.get("published_at") or "").strip()
    if published_at:
        return published_at[:10], published_at

    raw_date = (post.get("date") or "").strip()
    now = datetime.now()

    full_date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%m-%d",
    ]
    for fmt in full_date_formats:
        try:
            parsed = datetime.strptime(raw_date, fmt)
            if fmt.startswith("%m-%d"):
                parsed = parsed.replace(year=now.year)
                if parsed > now:
                    parsed = parsed.replace(year=now.year - 1)
            if fmt == "%Y-%m-%d":
                return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y-%m-%d 00:00:00")
            if fmt == "%m-%d":
                return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y-%m-%d 00:00:00")
            if "%H:%M" in fmt:
                return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    collected_at = (post.get("collected_at") or "").strip()
    if collected_at:
        try:
            normalized = collected_at.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    fallback = now.strftime("%Y-%m-%d %H:%M:%S")
    return fallback[:10], fallback


def analyze_post(post: Dict, sector: str, run_llm_second_pass: bool = True) -> AnalysisResult:
    """分析单条帖子，返回详细判定"""
    title = post.get("title", "")
    content = post.get("content", "")
    full_text = f"{title} {content}" if content else title
    source_date, source_datetime = _normalize_post_datetime(post)
    
    # 0. 垃圾过滤
    spam_patterns = list(COMMON_SPAM_PATTERNS)
    if sector == "gold":
        spam_patterns.extend(GOLD_ACTIVITY_SPAM_PATTERNS)

    for spam in spam_patterns:
        if spam in full_text:
            result = AnalysisResult(
                post_id=post.get("id", ""),
                title=title[:180],
                platform=post.get("platform", "unknown"),
                sector=sector,
                newbie_score=0,
                newbie_confidence="high",
                level="垃圾帖",
                reasoning=f"检测到垃圾/活动帖（命中: 「{spam}」），已过滤，不计入指数。",
                source_date=source_date,
                source_datetime=source_datetime,
            )
            return result

    if _is_storage_info_post(title, content, sector):
        result = AnalysisResult(
            post_id=post.get("id", ""),
            title=title[:180],
            platform=post.get("platform", "unknown"),
            sector=sector,
            newbie_score=0,
            newbie_confidence="high",
            level="资讯帖",
            reasoning="检测到偏产业梳理/名单/图谱类资讯帖，不计入宝妈指数。",
            source_date=source_date,
            source_datetime=source_datetime,
        )
        return result
    
    result = AnalysisResult(
        post_id=post.get("id", ""),
        title=title[:180],
        platform=post.get("platform", "unknown"),
        sector=sector,
        source_date=source_date,
        source_datetime=source_datetime,
    )
    
    # 1. 逐信号匹配
    matched_newbie = []
    matched_pro = []
    
    for signal in NEWBIE_SIGNALS:
        keywords = NEWBIE_KEYWORDS.get(signal.name, [])
        matched_kws = [kw for kw in keywords if kw.lower() in full_text.lower()]
        if matched_kws:
            matched_newbie.append((signal.name, signal.description, signal.weight, matched_kws))
    
    for signal in PRO_SIGNALS:
        keywords = PRO_KEYWORDS.get(signal.name, [])
        matched_kws = [kw for kw in keywords if kw.lower() in full_text.lower()]
        if matched_kws:
            matched_pro.append((signal.name, signal.description, signal.weight, matched_kws))
    
    # 2. 额外特征
    # 标题长度很短 + 情绪化
    extra_score = 0
    extra_reasons = []
    
    if len(title) < 12 and any(kw in title for kw in ["涨", "跌", "买", "卖"]):
        extra_score += 3
        extra_reasons.append("标题极短+情绪化，典型小白特征")
    
    if title.endswith("吗") or title.endswith("呢") or title.endswith("？"):
        extra_score += 2
        extra_reasons.append("以问句结尾，在寻求答案")
    
    # 3. 计算总分
    total_newbie = sum(s[2] for s in matched_newbie) + extra_score
    total_pro = abs(sum(s[2] for s in matched_pro))
    
    raw_score = total_newbie - total_pro * 0.8  # 专业信号打8折
    result.newbie_score = max(0, min(100, raw_score * 4 + 10))
    
    # 4. 置信度
    total_signals = len(matched_newbie) + len(matched_pro)
    if total_signals >= 4:
        result.newbie_confidence = "high"
    elif total_signals >= 2:
        result.newbie_confidence = "medium"
    else:
        result.newbie_confidence = "low"
    
    # 5. 判定等级
    s = result.newbie_score
    if s >= 50:
        result.level = "纯小白"
    elif s >= 35:
        result.level = "偏小白"
    elif s >= 20:
        result.level = "中间派"
    elif s >= 10:
        result.level = "偏专业"
    else:
        result.level = "专业投资者"
    
    # 6. 生成推理文本
    result.reasoning = _generate_reasoning(
        title, matched_newbie, matched_pro, extra_reasons,
        total_newbie, total_pro, result
    )
    
    # 7. 情绪分析
    result.sentiment_score = _analyze_sentiment(full_text)
    if result.sentiment_score > 0:
        result.sentiment_label = "greed"
    elif result.sentiment_score < 0:
        result.sentiment_label = "fear"
    else:
        result.sentiment_label = "neutral"
    result.emotion_intensity = abs(result.sentiment_score)
    result.sentiment_confidence = 0.35 if result.sentiment_score != 0 else 0.2
    
    # 8. 买入/卖出意图判定
    buy_count = sum(1 for kw in BUY_KEYWORDS if kw in full_text)
    sell_count = sum(1 for kw in SELL_KEYWORDS if kw in full_text)
    
    if buy_count > sell_count:
        result.intent = "buy"
        result.intent_strength = min(1.0, buy_count / 5)
    elif sell_count > buy_count:
        result.intent = "sell"
        result.intent_strength = min(1.0, sell_count / 5)
    else:
        result.intent = "neutral"
        result.intent_strength = 0

    # 8.5 LLM 二次判定：默认只处理规则不确定的样本
    if run_llm_second_pass and should_run_llm_second_pass(result):
        _apply_llm_second_pass(result, post)

    # 9. 关键信号摘要（用于前端卡片）
    result.key_signals = []
    for name, desc, weight, kws in matched_newbie[:3]:
        result.key_signals.append(f"「{name}」{desc} (命中: {', '.join(kws[:2])})")
    for name, desc, weight, kws in matched_pro[:2]:
        result.key_signals.append(f"「{name}」{desc} (命中: {', '.join(kws[:2])})")
    if result.sentiment_source == "llm":
        result.key_signals.append(
            f"「LLM情绪」{result.sentiment_label} / 强度{result.emotion_intensity:.2f} / 意图{result.intent}"
        )
    elif result.llm_error:
        result.key_signals.append(f"「LLM二判失败」{result.llm_error[:100]}")
    
    result.matched_newbie = [(n, d, w) for n, d, w, _ in matched_newbie]
    result.matched_pro = [(n, d, w) for n, d, w, _ in matched_pro]
    
    return result


def _generate_reasoning(
    title: str,
    matched_newbie: List[Tuple],
    matched_pro: List[Tuple],
    extra_reasons: List[str],
    total_newbie: float,
    total_pro: float,
    result: AnalysisResult,
) -> str:
    """生成人类可读的推理文本"""
    parts = []
    
    # 开头
    preview = (title or "").strip()
    if len(preview) > 80:
        preview = preview[:80] + "..."
    parts.append(f"帖子「{preview}」")
    
    if not matched_newbie and not matched_pro:
        parts.append("未命中明确的信号词，内容较短或信息不足。")
        parts.append("根据有限信息判定为中间派。")
        return " ".join(parts)
    
    # 小白信号
    if matched_newbie:
        signal_descs = [f"{name}({weight}分)" for name, desc, weight, kws in matched_newbie]
        parts.append(f"命中{len(matched_newbie)}个小信号: {', '.join(signal_descs)}。")
    
    # 专业信号
    if matched_pro:
        signal_descs = [f"{name}({weight}分)" for name, desc, weight, kws in matched_pro]
        parts.append(f"命中{len(matched_pro)}个专业信号: {', '.join(signal_descs)}。")
    
    # 额外
    if extra_reasons:
        parts.extend(extra_reasons)
    
    # 结论
    parts.append(f"综合得分{result.newbie_score}分，")
    parts.append(f"判定为「{result.level}」")
    parts.append(f"(置信度: {result.newbie_confidence})。")
    
    return " ".join(parts)


def _analyze_sentiment(text: str) -> float:
    """情绪分析: -1(恐慌) ~ +1(贪婪)"""
    greed_words = ["冲", "梭哈", "稳赚", "必涨", "躺赚", "满仓", "抄底", "起飞", "暴涨", "翻倍", "赚了", "盈利"]
    fear_words = ["割肉", "止损", "亏", "跌惨", "暴跌", "崩盘", "完了", "套牢", "深套", "亏了", "赔了", "大跌"]
    
    greed = sum(1 for w in greed_words if w in text)
    fear = sum(1 for w in fear_words if w in text)
    
    total = greed + fear
    if total == 0:
        return 0.0
    return round((greed - fear) / total, 2)


def _is_storage_info_post(title: str, content: str, sector: str) -> bool:
    if sector != "storage":
        return False

    full_text = f"{title} {content}".strip()
    if not full_text:
        return False

    if any(hint in full_text for hint in QUESTION_HINTS):
        return False

    hit_count = sum(1 for pattern in STORAGE_INFO_PATTERNS if pattern in full_text)
    if hit_count >= 1:
        return True

    if any(token in full_text for token in ["HBM", "DDR", "DRAM", "NAND"]) and any(
        token in full_text for token in ["图", "梳理", "名单", "关系"]
    ):
        return True

    normalized = full_text.lower()
    commerce_hit = any(token in normalized for token in STORAGE_COMMERCE_HINTS)
    spec_hit = any(token in normalized for token in STORAGE_SPEC_HINTS)
    has_price = bool(re.search(r"(?<!\d)(\d{2,5})(?:元|块|rmb)?(?!\d)", full_text))
    has_capacity = bool(re.search(r"\b\d+\s?(tb|gb|mb)\b", normalized))
    has_compare = any(token in full_text for token in ["推荐", "怎么选", "选哪个", "值得买", "性价比", "到手", "入手"])

    # 过滤偏硬件导购/报价/参数贴，这类内容和“投资情绪”关联很弱。
    if commerce_hit and ((has_price and (spec_hit or has_capacity)) or (has_compare and (spec_hit or has_capacity))):
        return True

    return False


# ============================================================
# 批量分析
# ============================================================

def _has_llm_review_cue(post: Dict) -> bool:
    """Only spend an LLM call on a post that can affect investor sentiment."""
    text = f"{post.get('title', '')} {post.get('content', '')}".lower()
    cues = [
        "怕", "慌", "恐慌", "瑟瑟发抖", "凉凉", "杀", "崩", "套", "亏", "割", "跌", "回本", "站岗",
        "冲", "追", "上车", "梭哈", "发财", "麻袋装钱", "暴涨", "起飞", "牛市",
        "买", "卖", "加仓", "减仓", "清仓", "抄底", "定投", "持仓", "要不要", "能不能", "还能",
    ]
    return any(cue in text for cue in cues)


def _apply_llm_second_pass(result: AnalysisResult, post: Dict) -> None:
    try:
        llm_decision = analyze_sentiment_with_llm(
            title=result.title,
            content=post.get("content", "") or "",
            sector=result.sector,
            platform=result.platform,
            current_result=result,
        )
    except Exception as exc:
        result.llm_error = str(exc)[:180]
        return

    if not llm_decision:
        result.llm_error = "接口未返回可解析的 JSON 结果"
        return

    result.sentiment_score = llm_decision.sentiment_score
    result.sentiment_label = llm_decision.sentiment_label
    result.emotion_intensity = llm_decision.emotion_intensity
    result.sentiment_confidence = llm_decision.confidence
    result.sentiment_source = llm_decision.source
    result.analysis_profile = llm_profile()
    result.model_name = llm_model_name()
    result.prompt_version = llm_prompt_version()
    result.intent = llm_decision.intent
    result.intent_strength = llm_decision.intent_strength
    if llm_decision.reasoning:
        result.reasoning += f" 情绪二判: {llm_decision.reasoning}"


def _refresh_key_signals(result: AnalysisResult) -> None:
    if result.sentiment_source == "llm":
        result.key_signals.append(
            f"「LLM情绪」{result.sentiment_label} / 强度{result.emotion_intensity:.2f} / 意图{result.intent}"
        )
    elif result.llm_error:
        result.key_signals.append(f"「LLM二判失败」{result.llm_error[:100]}")


def analyze_sector(posts: List[Dict], sector: str, sort_results: bool = True) -> List[AnalysisResult]:
    """分析一个板块的所有帖子"""
    results = []
    for post in posts:
        result = analyze_post(post, sector, run_llm_second_pass=False)
        results.append(result)
    
    if sort_results:
        results.sort(key=lambda r: r.newbie_score, reverse=True)
    return results


def analyze_all(sector_data: Dict[str, List[Dict]]) -> Dict[str, List[AnalysisResult]]:
    """分析所有板块"""
    all_results = {}
    for sector, posts in sector_data.items():
        print(f"  分析 {sector}: {len(posts)} 条帖子...")
        # LLM 二判需保持与原帖一一对应，全部处理完再按分数排序。
        all_results[sector] = analyze_sector(posts, sector, sort_results=False)

    if not llm_ready():
        for results in all_results.values():
            results.sort(key=lambda r: r.newbie_score, reverse=True)
        return all_results

    candidates = []
    for sector, posts in sector_data.items():
        for post, result in zip(posts, all_results[sector]):
            if should_run_llm_second_pass(result) and (
                llm_mode() == "all" or _has_llm_review_cue(post)
            ):
                candidates.append((result.source_datetime or result.source_date or "", sector, post, result))

    candidates.sort(key=lambda item: item[0], reverse=True)
    budget = llm_max_posts_per_run()
    selected = candidates[:budget]
    success = 0
    failed = 0
    interval = llm_request_interval_seconds()
    for index, (_, _, post, result) in enumerate(selected):
        _apply_llm_second_pass(result, post)
        if result.sentiment_source == "llm":
            success += 1
        elif result.llm_error:
            failed += 1
        _refresh_key_signals(result)
        if interval and index < len(selected) - 1:
            time.sleep(interval)

    print(f"  [LLM二判] 候选 {len(candidates)} 条，执行 {len(selected)} 条，成功 {success} 条，失败 {failed} 条")
    for results in all_results.values():
        results.sort(key=lambda r: r.newbie_score, reverse=True)
    return all_results

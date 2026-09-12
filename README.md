# 市场情绪监控 (Mom Index)

采集雪球、小红书和微博的最新投资讨论，使用 LLM 区分个人观点与资讯，并提取情绪、仓位、交易意图和未来预期。项目重点观察不同平台、板块和时间周期的情绪变化。

## 核心指标

| 指标 | 范围 | 含义 |
|------|------|------|
| **市场情绪指数** | -100 至 +100 | 负值代表恐慌，正值代表贪婪，0 附近代表中性 |
| **未来预期指数** | -100 至 +100 | 看跌比例高时为负，看涨比例高时为正 |
| **仓位分布** | 百分比 | 空仓、持有、被套、已退出在已识别仓位样本中的占比 |
| **资讯数量** | 帖数 | 新闻、公告、研报和产业资料只计数，不参与情绪与预期计算 |

雪球和小红书不会先混成一个平台画像。看板分别展示各平台指标，再给出板块汇总和平台分歧，避免小红书的大众表达与雪球的专业讨论互相掩盖。

## 时效控制

情绪指标默认使用最近 7 天且已识别发布时间的帖子；时间未知或未来时间不进入当期分析。发布时间统一为北京时间，MySQL 的 post_datetime 为北京时间 DATETIME，collected_at 单独记录采集时间。

运行后访问 platform_trends.html，可按板块查看各平台每小时、每日、每周的情绪均值、变化、样本数和买卖帖数。历史来自 MySQL，同平台同板块同帖子采用最近一次分析，重复采集不重复计数；缺失时段不补零。旧数据的时间准确性取决于原采集记录，不能通过新代码自动还原。

五个板块独立计算：纳斯达克、黄金、CPO 通信、半导体和存储。

## 指数解读

| 区间 | 信号 | 建议 |
|------|------|------|
| -100 至 -50 | 明显恐慌 | 个人观点整体明显偏恐慌 |
| -50 至 -15 | 偏恐慌 | 负面情绪占优 |
| -15 至 +15 | 情绪中性 | 多空情绪接近平衡 |
| +15 至 +50 | 偏贪婪 | 乐观和追涨情绪占优 |
| +50 至 +100 | 明显贪婪 | 个人观点整体明显偏贪婪 |

这些指标用于描述社媒样本，不直接构成买卖建议。样本数、模型覆盖率和平台构成需要与指数一起阅读。

## 覆盖板块

| 板块 | 标识 | 典型范围 |
|------|------|----------|
| 纳斯达克 | `nasdaq` | 纳指、美股、相关 ETF |
| 黄金 | `gold` | 黄金、黄金 ETF |
| CPO通信 | `cpo` | CPO、光模块、通信 ETF |
| 半导体 | `semiconductor` | 芯片、半导体、AI 芯片 |
| 存储 | `storage` | HBM、存储芯片、相关公司 |

## 项目结构

```
mom-index/
├── pipeline.py                  # 主流程：采集→分析→指数→存储
├── sync_data.py                 # 数据同步脚本（data/ → frontend/data/）
├── collectors/
│   ├── anti_detection.py        # 反检测核心：UA轮换+隐身+延迟
│   ├── guba_collector.py        # 东方财富股吧采集（已停用，不进入主流程）
│   ├── weibo_collector.py       # 微博公开搜索采集（🧪 实验性）
│   ├── xhs_collector.py         # 小红书 rnote.dev API（⚠️ 需充值）
│   └── xhs_playwright.py        # 小红书 Playwright 方案（⚠️ 需登录态）
├── analyzer/
│   ├── llm_analyzer.py          # 分析流程与兼容规则
│   ├── llm_sentiment.py         # LLM 情绪、仓位和预期提取
│   ├── index_calculator.py      # 市场情绪与预期指数计算
│   └── platform_trends.py       # 分平台、分周期历史聚合
├── frontend/
│   ├── dashboard.html           # 看板页面（Chart.js 暗色主题）
│   └── data/                    # 前端数据（pipeline 自动同步）
├── data/
│   ├── dashboard_data.json      # 前端数据源
│   ├── history.json             # 完整历史记录
│   └── xhs_posts.json           # 小红书采集缓存
├── .gitignore
└── README.md
```

## 快速开始

```bash
cd ~/Desktop/mom-index

# 运行采集、LLM 分析、指标计算和存储
python pipeline.py

# 启动带关键词管理 API 的 Web 服务（默认 8081）
python3 scripts/web_server.py

# Mac mini 后台重启并检查 Web 服务
./scripts/restart_web.sh

# Mac mini 推荐安装为登录守护服务，重启或异常退出后自动恢复
chmod +x scripts/install_web_launchd.sh
./scripts/install_web_launchd.sh

# 浏览器打开
# http://localhost:8081/dashboard.html
```

## 双机运行架构

- Mac mini 是唯一采集节点：`[runtime] role=collector`、`allow_collection=true`，每天执行 `scripts/mom_index_job.sh`，使用 `mini-14b` 分析并写入 Mac mini MySQL。
- MacBook 是按需分析节点：`[runtime] role=analyst`、`allow_collection=false`，只从同一个 MySQL 读取帖子；`scripts/analyze_mysql_posts.py` 不导入、也不会调用任何采集器。
- 每条分析结果保存模型 profile、完整模型名、提示词版本、分析引擎、批次和正文哈希。14B 与 27B 不互相覆盖。
- 指标按帖子北京时间发布时间归属；模型分析时间仅用于审计。模型对比只展示各自批次和覆盖率，不把未分析当成零。
- `scripts/web_server.py` 会在页面请求时从共享 MySQL 合并最新模型批次；MacBook 完成 27B 分析后无需复制前端 JSON，刷新 Mac mini 页面即可看到模型对比和平台趋势。
- Mac mini 可启用 `[local_llm]` 按需模式：数据采集期间不加载 14B，进入 LLM 分析前自动启动 `llama-server`，分析完成、失败或中断时关闭本次启动的服务。

Mac mini 首次将配置关键词导入 MySQL：

```bash
python3 scripts/manage_keywords.py bootstrap
python3 scripts/manage_keywords.py list
```

MacBook 启动 27B 服务后按需分析最近 7 天数据：

```bash
export QWEN_BASE_URL=http://127.0.0.1:8080
export MOM_INDEX_LLM_MODEL='你的27B模型完整名称'
python3 scripts/analyze_mysql_posts.py --days 7 --limit 1000
```

生产网页建议使用带关键词管理 API 的服务替代 `http.server`：

```bash
python3 scripts/web_server.py
```

默认监听 `0.0.0.0:8081`，避免与本地 14B LLM 的 `8080` 端口冲突。

访问 `dashboard.html` 查看模型批次概览，访问 `platform_trends.html` 按模型、平台和小时/日/周查看变化。关键词管理默认只读；需要在 Mac mini 的 `conf/config.ini` 中启用写操作：

```ini
[keyword_admin]
enabled = true
token = 请设置一个随机管理密码
```

修改配置后重启 `scripts/web_server.py`，再在 `keywords.html` 输入相同 token。token 只保存在当前浏览器会话中。

## 连接 Mac mini 上的 Qwen

Qwen 地址优先从 `QWEN_BASE_URL` 读取。MacBook 通过 Tailscale 连接 Mac mini 时：

```bash
export QWEN_BASE_URL=http://100.83.225.31:8080
python3 scripts/test_qwen_connection.py
```

在 Mac mini 本机运行时可不设置，测试脚本默认使用 `http://127.0.0.1:8080`。脚本会先请求 `/v1/models`，不可达时明确报错并停止；通过后再发送一条关闭 thinking 的 Chat Completions 请求。

## 提交与部署建议

- 不要提交 `conf/config.ini`、`.weibo_session/`、`.xhs_session/`、`.xueqiu_session/`
- 仓库里提供了可分享模板：`conf/config.example.ini`
- 部署到 macOS / Mac mini 后，复制一份本机配置：

```bash
cp conf/config.example.ini conf/config.ini
```

编辑好 MySQL / 邮件 / 各平台登录态后，再跑主流程。

## macOS Launchd 定时运行

仓库已提供独立的 `launchd` 任务：

- `scripts/mom_index_job.sh`
- `deploy/com.mhy.mom_index.plist`
- `scripts/install_launchd.sh`

默认每天北京时间 `00:00` 跑一次 `pipeline.py`，采集前一自然日的社媒数据并完成 14B 分析，日志写到 `logs/`。当天早间邮件直接读取这次已完成的结果，不再临时重复抓取。

MacBook 可安装 `scripts/install_27b_launchd.sh`，每天 `02:30` 启动27B，只补最近14天内当前模型和提示词版本尚未成功分析的帖子，单次上限5000条。

安装：

```bash
chmod +x scripts/mom_index_job.sh scripts/install_launchd.sh
./scripts/install_launchd.sh
```

检查：

```bash
launchctl list | grep mom_index
```

## 小红书 Playwright 本地试跑

适合 `rnote.dev` 不方便充值、但你本机已经登录过小红书网页的场景。

先安装依赖：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

推荐先初始化一个项目自己的持久会话目录：

```bash
python3 scripts/setup_xhs_session.py
```

这一步会打开浏览器，请你手动登录小红书、完成可能出现的验证，然后回到终端按回车保存会话。

如果你不想单独建会话，也可以回退到复用本机 Chrome profile。若默认 profile 不是 `Default`，先指定：

```bash
export XHS_CHROME_PROFILE_DIR='Profile 1'
```

单独测试一个关键词：

```bash
python3 scripts/test_xhs_playwright.py "纳指还能买吗" 8
```

常用环境变量：

- `XHS_HEADLESS=1`：无头运行，默认是有头，方便先看登录和风控
- `XHS_SESSION_DIR=.xhs_session`：指定项目专用的小红书持久会话目录
- `XHS_CHROME_USER_DATA_DIR=...`：手动指定 Chrome 用户目录
- `XHS_CHROME_PROFILE_DIR=Default`：指定 Chrome profile 名
- `XHS_KEEP_TMP_PROFILE=1`：保留临时复制出来的 profile 便于排查
- `XHS_RENDER_WAIT_SECONDS=6`：页面渲染慢时可适当加大等待秒数
- `XHS_DEBUG_DIR=/tmp/xhs_debug`：保存搜索结果页的 HTML 和截图，方便判断是登录页还是验证码页

如果直接访问 `search_result` 卡住，脚本现在会自动回退到“打开首页 -> 输入关键词 -> 回车搜索”的 UI 模式。

## 数据源

| 数据源 | 状态 | 日采集量 | 说明 |
|--------|------|----------|------|
| 东方财富股吧 | ⏸ 已停用 | 0 | 帖子质量不满足当前情绪分析要求，不进入主流程 |
| 雪球 | 🧪 需 Cookie | 视关键词而定 | 搜索结果页/接口字段偶尔会变，建议带登录 Cookie |
| 微博公开搜索 | 🧪 实验性 | 未知 | 公开网页搜索结果，可能有频控/验证码 |
| 小红书 (rnote.dev) | ⚠️ 需充值 | 0 | 免费额度仅够一轮 |
| 小红书 (x-mcp) | ⚠️ 登录通/搜索风控 | 0 | 扩展已装，搜索被XHS风控 |
| 小红书 (Playwright) | ⚠️ 需登录态 | 0 | 隐身脚本已就绪，缺登录cookie |
| 微信群聊（本地导出） | ✅ 可选 | 视导出量 | 只读用户导出文件，不解密微信数据库；默认关闭 |

## 微信群聊本地数据源

出于稳定性和隐私考虑，本项目不会绕过微信本地数据库加密，而是读取你主动放入私密目录的聊天导出文件。支持 `.json`、`.jsonl`、`.csv` 和制表符分隔的 `.txt`；原始文件目录已加入 `.gitignore`，发送者在进入分析管线前会哈希脱敏，群名不会写入输出。

在 `conf/config.ini` 增加：

```ini
[wechat]
enabled = true
export_dir = private/wechat_exports
groups = 投资交流群, 理财交流群
lookback_days = 7
privacy_salt = 请改成一段仅保存在本机的随机字符串
```

也可用环境变量覆盖：`MOM_INDEX_ENABLE_WECHAT=1`、`WECHAT_EXPORT_DIR=...`、`WECHAT_GROUPS=群A,群B`、`WECHAT_LOOKBACK_DAYS=7`、`WECHAT_PRIVACY_SALT=...`。

CSV/TXT 的首行字段可使用英文或中文别名：

```csv
group,sender,content,published_at,type
投资交流群,张三,纳指还能上车吗,2026-07-14 10:30:00,text
```

JSON 可直接是消息数组，也可使用 `{"messages": [...]}`。单独验证导入结果：

```bash
python3 scripts/test_wechat_collector.py
```

只有命中现有五个板块关键词的文本消息会进入评价；图片、语音、视频、过期消息和群白名单之外的内容会被忽略。原始聊天文件不会复制到 `data/`、前端或 MySQL。

## 微博实验性数据源

默认关闭。启用后，主流程会额外抓取微博公开搜索结果页：

```bash
export MOM_INDEX_ENABLE_WEIBO=1
python3 pipeline.py
```

抓取结果会额外落盘到 `data/weibo_posts.json`。

说明：

- 当前实现支持四条实验路径：`playwright`、`public_web`、`mobile_api`、`cn_cookie`
- 默认 `WEIBO_SOURCE_MODE=auto`
- 如果本地存在 `.weibo_session/` 会话目录，`auto` 会优先走 `playwright`
- 没有浏览器会话时，才会依次回退到移动端接口、公开搜索页、`weibo.cn`
- 微博目前最稳定的方案是 `playwright`
- Playwright 结果会自动清洗 `...全文`、`展开/收起` 等尾部文案，并过滤明显非正文/低质量结果
- 如果你有 `weibo.cn` 的登录 Cookie，可设置：

```bash
export WEIBO_SOURCE_MODE=cn_cookie
export WEIBO_COOKIE='你的cookie'
```

- 如果想看每个关键词的原始返回，设置：

```bash
export WEIBO_DEBUG_DIR=/tmp/weibo_debug
```

如果你想走浏览器会话方案，再试这一套：

```bash
python3 scripts/setup_weibo_session.py
python3 scripts/test_weibo_playwright.py "纳指还能买吗" 8
```

也可以强制主流程直接走 Playwright：

```bash
export MOM_INDEX_ENABLE_WEIBO=1
export WEIBO_SOURCE_MODE=playwright
python3 pipeline.py
```

半导体相关关键词已经补充了 `存储`、`海力士`，会一起进入微博/小红书检索。

### 关键词管理

所有平台只维护本机 `conf/config.ini` 的一个 `[keywords]` 段落。每个板块一行、逗号分隔；
小红书、微博、雪球搜索，微信群文章归类和跨板块去重都会使用同一套词表。修改后下次运行
`pipeline.py` 自动生效，无需修改 Python 代码。

## 雪球数据源

如果你已经在 `conf/config.ini` 里配了 `[xueqiu]` 的 `cookie`，主流程会自动启用雪球采集。

也可以显式开启：

```bash
export MOM_INDEX_ENABLE_XUEQIU=1
python3 pipeline.py
```

单独测试一个关键词：

```bash
python3 scripts/test_xueqiu_collector.py "海力士"
```

如果你想直接走浏览器页面抓取，而不是接口：

```bash
python3 scripts/setup_xueqiu_session.py
export XUEQIU_SOURCE_MODE=playwright
export XUEQIU_HEADLESS=0
python3 scripts/test_xueqiu_collector.py "海力士"
```

可选环境变量：

- `XUEQIU_COOKIE=...`：覆盖 `config.ini`
- `XUEQIU_COUNT=10`：每页抓多少条
- `XUEQIU_PAGES=2`：抓多少页
- `XUEQIU_SOURCE_MODE=auto|api|playwright`：默认 `auto`
- `XUEQIU_HEADLESS=0`：浏览器可见，便于排查
- `XUEQIU_DEBUG_DIR=/tmp/xueqiu_debug`：保存接口原始返回，方便排查 Cookie 是否失效

## MySQL 落库

主流程现在支持可选写入本地 MySQL，不影响原有 `data/*.json` 和前端文件输出。

默认会优先读取 `conf/config.ini` 里的 `[mysql]` 配置。

推荐这样配置：

```bash
export MOM_INDEX_ENABLE_MYSQL=1
export MOM_INDEX_DB_HOST=127.0.0.1
export MOM_INDEX_DB_PORT=3306
export MOM_INDEX_DB_USER=你的用户
export MOM_INDEX_DB_PASSWORD='你的密码'
export MOM_INDEX_DB_NAME=stock
python3 pipeline.py
```

如果你已经在 `conf/config.ini` 里配好了数据库，通常只需要：

```bash
export MOM_INDEX_ENABLE_MYSQL=1
python3 pipeline.py
```

会自动创建三张表：

- `mom_index_runs`
- `mom_index_posts`
- `mom_index_analysis`

如果 MySQL 配置不通，主流程会打印错误并跳过写库，不会影响本地 JSON 和前端看板。

## 分析方法

### 单帖 LLM 分类

每条帖子先分类为个人观点 `opinion`、资讯 `news` 或垃圾内容 `spam`。只有个人观点参与后续指标，并提取：

- 情绪：`fear`、`greed`、`neutral`、`mixed`，附带 `-1` 至 `+1` 分数和置信度。
- 交易意图：`buy`、`sell`、`neutral`。
- 仓位状态：`none`、`holding`、`trapped`、`exited`、`unknown`。
- 未来预期：`bullish`、`bearish`、`sideways`、`unknown`。

新闻、公告、研报和产业资料归为资讯，只记录数量；其情绪分、情绪强度和交易意图归零，仓位与未来预期设为未知。历史兼容字段不参与当前核心指标，也不应作为产品结论使用。

### 指数公式

市场情绪指数仅使用成功完成 LLM 分析的个人观点：

```text
市场情绪指数 = Σ(情绪分 × 置信度) / Σ置信度 × 100
```

为避免零置信度样本异常，每条有效样本的计算权重下限为 `0.1`。未来预期指数只使用明确识别出看涨、看跌或震荡的样本：

```text
未来预期指数 = (看涨帖数 - 看跌帖数) / 已识别预期帖数 × 100
```

仓位比例也只在已识别仓位的样本中计算。`unknown` 不进入比例分母，页面同时展示有效样本数，避免少量样本产生误导。

## 前端看板

- 五板块市场情绪、未来预期和仓位卡片
- 雪球、小红书、微博分平台指标及平台分歧
- 每小时、每日、每周历史情绪曲线与样本量
- 个人观点、资讯数量和 LLM 模型覆盖率
- 14B 与 27B 分模型批次展示，不互相覆盖
- 帖子明细及 LLM 判定依据
- 暗色主题，响应式布局

## 反检测系统

`collectors/anti_detection.py` — 从 AIMail_Agent 项目搬过来的三件套：

| 能力 | 说明 |
|------|------|
| **请求头轮换** | 7个 Chrome/Edge UA 随机切换 + Sec-CH-UA 指纹头 |
| **人类延迟** | 高斯分布，每10次停5-15s，每50次停30-60s |
| **隐身脚本** | 8条 — webdriver/plugins/hardwareConcurrency 全部伪造 |
| **Playwright 参数** | 20+ 启动参数隐藏自动化痕迹 |

## 已知局限

- **平台样本不等质**：雪球、小红书和微博的用户结构与表达方式不同，应优先看分平台结果
- **采集风控**：小红书、雪球和微博可能要求登录或人工验证，失败时当期样本会减少
- **LLM 超时与覆盖率**：超时帖子不进入 LLM 核心指标，必须结合覆盖率判断结果质量
- **发布时间缺失**：无法识别北京时间的帖子不进入当期时间窗口
- **缺少回测**：尚未用历史行情数据验证指数与市场顶底的相关性
- **样本偏差**：关键词搜索只能代表被检索到的公开讨论，不代表全部投资者

## 待解决

- [ ] 提升各平台最新排序和发布时间识别的稳定性
- [ ] 完善 LLM 超时重试、批量分析和覆盖率告警
- [ ] 将每周情绪、仓位和预期变化与行业指数行情进行回测
- [ ] 增加长期数据质量监控和平台样本异常提醒

## 技术栈

- Python 3.9+、requests、playwright、PyMySQL
- Chart.js 4.x + 原生 HTML/CSS
- OpenAI 兼容 Chat Completions 接口 / llama.cpp 本地模型服务
- 小红书、雪球和微博网页采集

## License

MIT — 仅供学习研究，不构成投资建议。

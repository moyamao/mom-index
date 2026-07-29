# 👩‍👧 宝妈指数 (Mom Index)

追踪中文社交平台上散户/小白/宝妈的投资讨论热度。**指数越高，散户情绪越极端，市场越危险**——基于"擦鞋童理论"的行为金融学反向指标。

## 核心原理

```
当菜市场大妈和宝妈都在讨论股票，就是该离场的时候。
                         — Joseph Kennedy, 1929
```

四个板块独立计算，每个板块有三个指标：

| 指标 | 含义 |
|------|------|
| **宝妈指数** | 综合情绪热度 (0-100) |
| 🟢 **宝妈买入** | 追涨情绪强度 (0-100) |
| 🔴 **宝妈卖出** | 割肉恐慌强度 (0-100) |

## 指数解读

| 区间 | 信号 | 建议 |
|------|------|------|
| 0-20 | 🔵 极度冷清 | 小白沉默，可能是底部 |
| 20-40 | 🟢 正常区间 | 维持现有策略 |
| 40-60 | 🟡 开始升温 | 关注，准备减仓 |
| 60-75 | 🟠 高度警惕 | 大幅减仓 |
| 75-100 | 🔴 极度狂热 | 擦鞋童时刻，清仓 |

## 覆盖板块

| 板块 | 股吧代码 | ETF |
|------|----------|-----|
| 纳斯达克 | of159941 | 513100 |
| 黄金 | of518880 | 518880 |
| CPO通信 | of515880 | 515880 |
| 半导体 | of512480 | 512480 |

## 最近数据

> 2026-06-21 | 来源：东方财富股吧（310条）+ 小红书模拟数据（54条小白帖）
> 市场背景：科技牛市(CPO+半导体持续上涨) + 黄金阴跌(从高位回撤20%) + 纳指高位震荡

| 板块 | 指数 | 买入 | 卖出 | 买卖比 | 小白占比 | 信号 |
|------|------|------|------|--------|----------|------|
| CPO通信 | 60.4 | 51.9 | 20.9 | 5:1 | 30.2% | 🟠 高度警惕 — FOMO追涨 |
| 黄金 | 51.8 | 0.0 | 64.1 | 0:∞ | 32.6% | 🟡 开始升温 — 恐慌割肉 |
| 纳斯达克 | 50.4 | 46.2 | 0.0 | ∞:0 | 27.9% | 🟡 开始升温 — 温和追涨 |
| 半导体 | 43.5 | 59.6 | 0.0 | ∞:0 | 31.1% | 🟡 开始升温 — 跟风买入 |

> ⚠️ 当前小红书数据为模拟数据（真实 API 不可用）。接入真实小红书数据后指数可能进一步上升。

## 项目结构

```
mom-index/
├── pipeline.py                  # 主流程：采集→分析→指数→存储
├── sync_data.py                 # 数据同步脚本（data/ → frontend/data/）
├── collectors/
│   ├── anti_detection.py        # 反检测核心：UA轮换+隐身+延迟
│   ├── guba_collector.py        # 东方财富股吧采集（✅ 生产可用）
│   ├── weibo_collector.py       # 微博公开搜索采集（🧪 实验性）
│   ├── xhs_collector.py         # 小红书 rnote.dev API（⚠️ 需充值）
│   └── xhs_playwright.py        # 小红书 Playwright 方案（⚠️ 需登录态）
├── analyzer/
│   ├── llm_analyzer.py          # 多维度分类引擎（40+信号词库）
│   └── index_calculator.py      # 指数计算（含买入/卖出子指数）
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

# 运行数据采集 + 分析 + 指数计算（自动同步到 frontend/data/）
python pipeline.py

# 启动前端看板（默认 8765 端口）
cd frontend && python -m http.server 8765

# 浏览器打开
# http://localhost:8765/dashboard.html
```

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

默认每天 `19:20` 跑一次 `pipeline.py`，日志写到 `logs/`。

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
| 东方财富股吧 | ✅ 稳定 | ~307条 | 4个ETF吧，无需cookie，无风控 |
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
groups = 投资交流群, 宝妈理财群
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

### 小白判定（40+信号词库）

每条帖子匹配多维度信号，每条判定带可读推理：

```
帖子「黄金亏了20%了要不要割肉啊😭」
  命中: 决策依赖(+7) + 情绪恐慌(+5)
  意图: 🔴 卖出（命中"割肉""亏了"）
  得分: 58分 → 判定「纯小白」
```

信号维度：

| 维度 | 权重 | 示例 |
|------|------|------|
| 身份自述 | +8 | "小白"、"宝妈"、"新手" |
| 知识求助 | +6 | "怎么买"、"在哪看"、"请教" |
| 决策依赖 | +7 | "该不该"、"要不要"、"还能买吗" |
| 情绪恐慌 | +5 | "亏麻了"、"好慌"、"心态崩了" |
| 跟风行为 | +6 | "听博主说"、"朋友推荐" |
| 过度乐观 | +4 | "梭哈"、"满仓干"、"稳赚" |
| 专业术语 | -5 | PE/PB/估值/溢价率（扣分项） |

### 意图判定

60+ 关键词区分买入/卖出意图：

- 🟢 买入：上车、冲、加仓、买了、还能买吗、想买、心动...
- 🔴 卖出：割肉、止损、清仓、亏了、要不要走、跌麻了...

## 指数公式

```
宝妈指数 = 小白占比×0.40 + 小白强度×0.25 + 情绪极端度×0.20 + 信号纯度×0.15

宝妈买入 = 买入小白占比×50 + 小白热度×30 + 买入强度×20
宝妈卖出 = 卖出小白占比×50 + 小白热度×30 + 卖出强度×20
```

## 前端看板

- 四板块指数卡片（实时数值 + 买入/卖出子指标）
- 30天历史曲线（Chart.js）
- 今日典型小白帖（含推理过程）
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

- **股吧小白占比低**：5-20% 是正常的，真正的小白信号需要小红书数据
- **关键词规则限制**：非 LLM 语义理解，会漏掉隐含信号、误判部分 spam
- **缺少回测**：尚未用历史行情数据验证指数与市场顶底的相关性
- **单日快照**：一次采集只是一个数据点，需要持续运行积累

## 待解决

- [ ] 小红书稳定数据源（rnote.dev 充值 或 x-mcp 解风控）
- [ ] LLM 语义分类替换关键词规则
- [ ] 抖音/微博数据源扩展
- [ ] 定时自动采集（cron job）
- [ ] 每日宝妈指数自动推送（微信/Telegram）
- [ ] 回测验证：拿历史数据验证指数与市场顶底的相关性

## 技术栈

- Python 3.14 + requests + playwright
- Chart.js 4.x + 原生 HTML/CSS
- 东方财富股吧 HTML 解析
- 小红书 rnote.dev API / x-mcp MCP 协议

## License

MIT — 仅供学习研究，不构成投资建议。

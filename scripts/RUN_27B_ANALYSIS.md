# MacBook 按需27B计算

在项目根目录执行：

```bash
python3 scripts/run_27b_analysis.py --days 7 --limit 1000
```

Mac mini模型不需要在线；其历史结果直接从MySQL读取。本机27B由脚本自动启动和关闭。

启动 `/Users/mhy/models/qwen3.8-27b/Qwen3.8-27B-Q8_0.gguf`，默认监听 `127.0.0.1:8080`，上下文4096、单并发、GPU卸载99层、关闭reasoning preserve。等待 `/v1/models` 返回同一个模型后，只补跑缺少 `macbook-27b` 结果的帖子。

计算入口读取 MySQL 目录表中的已采集帖子，不调用采集器；同一帖子命中多个关键词或板块时只分析一次。按原帖发布时间选择最近7天、最多1000条；确需强制重算27B时才增加 `--reanalyze-all`。

27B补算完成后，从MySQL离线配对小模型和27B历史结果。只有内容类型冲突、情绪分差至少0.50、恐慌与贪婪相反、买卖方向相反、看涨与看跌相反、持仓与明确退出冲突时，才调用配置文件 `[llm]` 中的百炼模型。投资者成熟度、意图强度、`none/unknown` 和轻微标签边界差异不会触发云端。云端有效结果作为最终结果；否则采用27B。云端失败时暂用27B并标记待复核。

结果通过 `persist_standalone_analysis` 写入独立批次，profile为 `local-consensus-cloud`。`adjudication_source` 记录 `local_agreement_27b`、`cloud_adjudication` 等来源，`adjudication_json` 保存两个本地模型、云端裁决、分歧字段及各次调用状态。API key不会写入该字段。入库后刷新网页模型对比与平台趋势。

成功、计算失败、启动超时、Ctrl+C或SIGTERM退出时，脚本清理自己启动的模型进程。SIGKILL、断电等无法执行退出清理的情形除外。脚本不会使用pkill，也不会关闭现有的其他模型服务；8080占用时直接退出。可用 `--port 8082` 指定空闲端口，但不要同时加载两份27B导致内存不足。

先验证少量数据：

```bash
python3 scripts/run_27b_analysis.py --days 7 --limit 10
```

仅查看命令：

```bash
python3 scripts/run_27b_analysis.py --dry-run
```

可用 `--model`、`--llama-server`、`--python`、`--port`、`--context`、`--startup-timeout` 调整参数。启动超时默认300秒，计算本身不设总时长上限。单次请求超时默认300秒，失败自动重试2次。

14B不会被实时调用：脚本按同一帖子从MySQL读取已经入库的14B结果，因此Mac mini的14B服务无需在线。历史结果配置默认是 `mini-14b`，可用 `CONSENSUS_14B_PROFILE` 或分析脚本参数 `--reference-profile` 覆盖。没有对应14B结果时保留27B判断并标记待复核，不调用云端；只有14B与27B存在实质分歧时才调用云端裁决。

27B地址由启动脚本自动传入。云端地址、模型和密钥默认读取 `[llm]`，也可通过 `CONSENSUS_CLOUD_BASE_URL`、`CONSENSUS_CLOUD_MODEL`、`CONSENSUS_CLOUD_API_KEY` 覆盖。

模型日志保存到项目 `logs/qwen27b_时间_进程号.log`；计算进度和入库批次号实时输出到终端。MySQL连接复用当前项目配置。锁阻止两个本脚本并行执行。

MacBook 每日定时任务默认在 `02:30` 查看最近14天，单次最多补5000条，并在运行期间使用 `caffeinate` 防止休眠：

```bash
./scripts/install_27b_launchd.sh
```

如果提示没有符合条件的帖子，确认目录表及原帖发布时间；必要时调整 `--days`。完成任务的退出码不等于逐条语义均正确，检查批次中的LLM失败数和分析引擎字段。

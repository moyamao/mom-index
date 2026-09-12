# MacBook 按需27B计算

在项目根目录执行：

```bash
python3 scripts/run_27b_analysis.py --days 7 --limit 1000
```

启动 `/Users/mhy/models/qwen3.8-27b/Qwen3.8-27B-Q8_0.gguf`，默认监听 `127.0.0.1:8080`，上下文4096、单并发、GPU卸载99层、关闭reasoning preserve。等待 `/v1/models` 返回同一个模型，才调用现有 `scripts/analyze_mysql_posts.py`。

计算入口读取 MySQL 目录表中的已采集帖子，不调用采集器；默认只分析当前 `macbook-27b` 与提示词版本尚未成功分析的帖子。按原帖发布时间选择最近7天、最多1000条；需要强制重算时增加 `--reanalyze-all`。沿用当前生产分析器和提示词，不是qwen-benchmark的v3.5影子分析。结果通过既有 `persist_standalone_analysis` 写入独立分析批次及分析结果表，profile固定为 `macbook-27b`，保留完整模型名。入库后沿用现有入口刷新网页模型对比与平台趋势。

成功、计算失败、启动超时、Ctrl+C或SIGTERM退出时，脚本清理自己启动的模型进程。SIGKILL、断电等无法执行退出清理的情形除外。脚本不会使用pkill，也不会关闭现有的其他模型服务；8080占用时直接退出。可用 `--port 8082` 指定空闲端口，但不要同时加载两份27B导致内存不足。

先验证少量数据：

```bash
python3 scripts/run_27b_analysis.py --days 7 --limit 10
```

仅查看命令：

```bash
python3 scripts/run_27b_analysis.py --dry-run
```

可用 `--model`、`--llama-server`、`--python`、`--port`、`--context`、`--startup-timeout` 调整参数。启动超时默认300秒，计算本身不设总时长上限。API请求超时默认300秒，可通过 `MOM_INDEX_LLM_TIMEOUT_SECONDS` 覆盖。逐帖错误和重试行为沿用现有生产分析器。

模型日志保存到项目 `logs/qwen27b_时间_进程号.log`；计算进度和入库批次号实时输出到终端。MySQL连接复用当前项目配置。锁阻止两个本脚本并行执行。

MacBook 每日定时任务默认在 `02:30` 查看最近14天，单次最多补5000条，并在运行期间使用 `caffeinate` 防止休眠：

```bash
./scripts/install_27b_launchd.sh
```

如果提示没有符合条件的帖子，确认目录表及原帖发布时间；必要时调整 `--days`。完成任务的退出码不等于逐条语义均正确，检查批次中的LLM失败数和分析引擎字段。

# 本地情绪模型对照评测

从项目目录执行。工具从输入文件或 MySQL 只读抽样，顺序调用本地模型，不调用百炼、不写入 MySQL，也不改变生产默认提示词。默认连接已启动的模型；加 `--start-local-model` 可按需启动本机模型。生成请求前先检查 `/v1/models`；不可达、模型不匹配或地址属于公网时停止。使用远程 Mac mini 时填写 Tailscale IP；不要填写公网云端地址。

v7 提示词需主动选择，暂不自动替换生产 v6；严格输出校验、短句与完整标题处理、重试保留原输入等修复在部署后也适用于 v6。已有入库记录不会被重写；历史终态 `llm_failed` 即使后来修复了原因，仍需显式重新分析。

## Mac mini 独立运行：启动模型、评测、关闭

先确认 Mac mini 项目自身的 `conf/config.ini` 中 `[local_llm]` 已配置正确的 `model_path`、`llama_server`、`port`、`context`、`startup_timeout_seconds` 和 `request_timeout_seconds`，项目 `.venv` 已安装依赖。不要把 MacBook 的整份配置覆盖到 Mac mini，也不要沿用旧模型路径。

```bash
cd /Users/mhy/python/mom-index
.venv/bin/python -u scripts/evaluate_local_sentiment.py \
  --start-local-model --smoke --variant baseline --variant prompt
```

该选项只在本次调用中启用启动功能，不修改配置；正常完成、部分样本失败、其他异常或 Ctrl+C 中断都会关闭本次启动的模型。端口若已被其他服务占用则停止，不会关闭已有进程。开始前先校验输入和输出目录，避免无效任务加载模型。

自动启动时连接配置端口的 `127.0.0.1`，覆盖继承的 `QWEN_BASE_URL`，并使用实际发现的模型身份；显式指定的冲突 `--base-url` 或 `--model` 会报错。请求超时默认使用 `[local_llm].request_timeout_seconds`，可用 `--timeout` 覆盖。未加启动选项时仍需自行启动模型，请求超时默认120秒。

## 从 MySQL 随机抽取100条，比较两个提示词

在 Mac mini 上执行，数据库连接读取项目已有配置；模型路径仍读取本机 `[local_llm]`：

```bash
cd /Users/mhy/python/mom-index
.venv/bin/python -u scripts/evaluate_local_sentiment.py \
  --mysql --days 30 --sample-size 100 --sample-seed 20261111 \
  --start-local-model --variant baseline --variant prompt
```

`--mysql`、`--input`、`--smoke` 三选一。MySQL 模式默认参数就是最近30天、100条、抽样种子20261111，只读取雪球、微博、小红书（`xueqiu`、`weibo`、`xiaohongshu`、`xhs`）帖子。按实际送入模型的文本去重，并排除与提示词示例完全相同的文本，再从整个合格候选池随机抽样；不是直接取最新100条。合格样本不足指定数量时会报错，不会静默缩小测试集。

启动模型之前，脚本先将抽样结果冻结为独立运行目录内的 `samples.jsonl`，并保存 `sampling.json` 抽样记录。baseline 和 prompt 使用同一批冻结文本。即使模型随后无法启动或不可达，这两个文件也会保留，尚未开始的模型请求会停止。

这一批真实帖子没有人工 gold，因此只比较两个提示词输出的差异和失败，不报告准确率，也不会把其中一组自动判为正确；增加种子或重复次数后才统计重复稳定性。完成后打开 `comparison.html` 进行中文复核；差异和失败记录都会保留。

以后要复跑同一批样本，以冻结文件为准，例如把下方路径替换为实际运行目录：

```bash
.venv/bin/python -u scripts/evaluate_local_sentiment.py \
  --input outputs/sentiment-eval/RUN_ID/samples.jsonl \
  --start-local-model --variant baseline --variant prompt
```

再次从 MySQL 抽样时，即使用相同 `--sample-seed`，候选池变化也可能导致样本变化。复跑冻结文件时还应保持相同的 `--max-chars` 等输入处理参数。`--sample-seed` 控制抽样，`--seeds` 控制模型生成，两者用途不同。

## 先检查连通与短句输出

```bash
cd /Users/mhy/python/mom-index
/usr/bin/python3 scripts/evaluate_local_sentiment.py \
  --base-url http://100.83.225.31:8080 \
  --smoke --variant baseline --variant prompt
```

内置三个合成例子为“今天清仓了”“存储还没跌完”“长期看好，但套得我睡不着”。它们没有人工 gold，只用于检查请求与输出；成功不代表准确率提升。

地址优先使用 `--base-url`，其次 `QWEN_BASE_URL`，未设置时使用 `http://127.0.0.1:8080`。模型默认从服务发现；可加 `--model` 核对身份。微信内容继续受项目的本机端点限制，不能经此工具发送到远程 Mac mini。

## 输入格式

支持 UTF-8 JSONL（每行一个对象）或 JSON 数组。必须有唯一 `id`；其余文本字段为 `title`、`content`、`platform`、`sector`。建议加 `split`，区分 `dev`、`test`、`challenge`。例如以下是合成样本格式，不是人工评测集：

```json
{"id":"example-1","title":"今天清仓了","content":"","platform":"xueqiu","sector":"storage","split":"dev"}
{"id":"example-2","title":"存储还没跌完","content":"","platform":"xueqiu","sector":"storage","split":"dev"}
```

只有人工确认的字段才放入 `gold`，允许只标一部分，例如 `"gold":{"intent":"clear"}`。支持 `content_type`、`sentiment_label`、`sentiment_score`、`emotion_tags`、`intent`、`position_status`、`market_outlook`。情绪分数存 `-1` 到 `1`，不是 `-100` 到 `100`。缺少的标签不自动补齐，不用于计算准确率。云端或另一个模型的结果不能直接充当人工 gold。

## 运行开发集对照

```bash
/usr/bin/python3 scripts/evaluate_local_sentiment.py \
  --base-url http://100.83.225.31:8080 \
  --input outputs/sentiment-dev.jsonl --split dev \
  --variant baseline --variant prompt \
  --seeds 42 43 44 --repeats 1
```

| 参数组 | 提示词 | 生成参数 |
| --- | --- | --- |
| baseline | sentiment-v6 | temperature=0.1，其余使用服务默认值 |
| prompt | sentiment-v7 | temperature=0.1，其余使用服务默认值 |
| greedy | sentiment-v7 | temperature=0，其余使用服务默认值 |
| official | sentiment-v7 | temperature=0.7, top_p=0.8, top_k=20, min_p=0, repeat_penalty=1 |

所有组使用相同输入、严格 JSON 契约、默认768输出token、非思考模式。baseline 是 v6 提示词控制组，不是对历史生产流程的逐字重放。先比 baseline/prompt；选好提示词后再比 prompt/greedy/official。official 仅是 Qwen3 非思考模式的参考采样组，不是所有新模型的通用最佳参数，实际效果仍须人工标注集 A/B 验证。它同时改变多个采样参数，不能据此将差异单独归因于温度。每次比较期间保持同一模型和服务启动参数。

`--seeds` 指定采样种子，范围为 `-1` 到 `4294967295`，重复种子自动去重；`-1` 表示随机种子，不适用于固定种子复现。`--repeats` 指定每个种子的重复次数。固定种子有助复现，不保证语义正确。文件输入可用 `--limit 10` 做小批检查，MySQL模式则使用 `--sample-size 10`；`--max-chars 1200`、`--max-tokens 768`、`--timeout 120` 控制预算。失败自动重试两次，三次请求的文本和参数完全相同。

## 输出与解释

每次在 `outputs/sentiment-eval/` 创建独立目录，也可通过 `--output-dir` 指定尚不存在的目录。已有目录会被拒绝，避免覆盖之前的对照结果。

- `metadata.json`：输入摘要、模型、提示词版本、生成配置、种子、数量与运行状态。
- `samples.jsonl`、`sampling.json`：MySQL 模式在模型启动前保存的冻结样本和抽样记录，可用于审计与复跑。
- `results.jsonl`：每条完成后立即刷新到磁盘，保存实际输入、输入/提示词/请求哈希、模型返回、每次尝试的错误、截断原因、token统计与耗时。中断后已完成条目仍在，状态保持 running。
- `summary.json`：各组有效输出率、字段准确率、情绪宏F1、情绪分差、重复一致性与耗时。完整跑完但有失败时退出码为2，仍保存报告。
- `comparison.json`、`comparison.html`：按同一条样本配对展示各组输出，中文页面便于检查所有差异和失败；它是人工复核材料，不是自动正确性判定。

完整运行结束后自动打包为 `outputs/sentiment-eval/<runid>.zip`，包括完整跑完但有失败样本的情况，方便传回结果复核。中断或启动失败时保留已有文件，不代表完整评测完成。

无人工标签时标记 `unscored_no_human_gold`。字段准确率分母包含该字段已标注但请求失败的样本。情绪 MAE 仅能在有效结果上计算，旁边明确列出标注量、有效量和失败量；“20分内比例”将失败计为未达标。不可只看成功样本的 MAE。所有重复运行分别计入统计，并不构成更多独立人工样本。

调提示词和参数只用开发集。随机独立测试集应冻结，最后验证时再使用 `--split test`；困难挑战集单独报告，不能用它估计全量帖子准确率。上线决定还需人工检查正负判反、错误买卖方向、无法判断比例和新日期数据表现。

# 版本迁移

## 从 0.4 alpha.3 到 alpha.4

保留 `fp-summation-canary-a3` 的 blocked 状态与 invalid 留档，不人工补 `evidence_ids`，也不要把 alpha.3 的 `.research/`、hypothesis ledger 或 provenance 复制到 alpha.4。alpha.4 修改了所有 Schema 版本，并新增 hypotheses 的结构引用修复、grounded revision、revision diff 与失败分类，因此旧状态不能续跑。

在 alpha.4 中创建同目标新项目，只复制人工确认的 goal、constraints 与预算。若 hypotheses 使用 Claim ID，只有能从冻结 Evidence Ledger 的 `claim_ids` 确定推出的 Evidence Record ID 才会被映射；空引用不会自动补证据，只能由无网络 reviser 删除或基于既有 Evidence Record 重写。真实 Canary 应继续跑到 experiment-plan，以同时验证 alpha.3 的计划修复通道，并在 `plan-approval` 停止。

## 从 0.4 alpha.2 到 alpha.3

alpha.2 项目严格保留原状态，尤其不要人工复位 `fp-summation-canary-a2`。alpha.3 修改了所有 Schema 版本，并为 experiment-plan 增加阶段专属 bounded repair、聚合校验和预算跨字段约束，因此不能续跑 alpha.2 的 state、产物或 provenance。

在 alpha.3 新建项目，只复制人工确认的 goal 和 constraints。注意当前最低完整计划是 4 tasks × 2 conditions；当 repetitions=1 时，research contract 的 `max_sessions` 至少为 8。系统不会通过 experiment-plan 自动扩大 framing 已确定的预算；如果原始契约只有 2 sessions，应重新明确研究范围或由用户创建新的预算约束，而不是手改 JSON。

## 从 0.4 alpha.1 到 alpha.2

项目状态严格绑定 workflow version，因此 alpha.1 项目不能直接由 alpha.2 续跑。特别是已经 blocked 的 `fp-summation-canary-v04a` 应保留为失败证据，不应手改 state、ledger 或 provenance。

在 alpha.2 中创建同目标的新 Canary。首次 evidence 结构失败会被留档，第二次尝试只能由受限 repair Agent 修复结构；若修改科学内容、既有快照或其他文件，项目仍会进入 blocked。不要复制旧项目的 `.research/` 或 evidence ledger。

## 从 0.3 到 0.4 alpha

0.4 改变了执行 manifest、正式结果、冻结清单和实验入口协议。0.3 项目不能原地进入 0.4 的正式阶段，也不能沿用旧审批或执行结果。

推荐保留 0.3 项目作为审计记录，在 0.4 alpha 下创建新项目，只人工带入 goal、约束、预算和已经人工核验的证据。不要复制 `.research/`、`configs/execution-manifest.json`、`scripts/execute-approved.py` 或 `runs/`。

0.4 的入口必须执行**一个** trial：接收 `--trial-spec <json>` 与 `--result <json>`，并回写含 execution ID、trial ID 和幂等键的 trial-result。循环、重试、checkpoint 和汇总属于 `LocalExecutor`，不能再写进入口脚本。先运行 `python3 researchctl.py doctor` 与 `tests/run_all.sh`，再创建真实小预算项目。

## 从 0.2 到 0.3

0.2 项目没有 `schema_version`、可回放 query snapshot 和 controller provenance，不能原地恢复。保留旧目录，在 0.3 中创建新项目；只人工带入 goal、约束、确认过的来源与预算，不复制 JSON、state、approval、脚本或结果。

0.3 已锁定八项 K-Dense Skills，并把 `paper-lookup` 与 `experimental-design` 接入对应阶段。迁移后先运行 `doctor` 与完整离线测试；真实 DeepSeek 对照试验必须创建新的 v0.3 项目，不复用 alpha 的状态与 provenance。

## 从 0.1 到 0.2

## 结论

不要在 0.1 项目目录上覆盖安装，也不要让已停在 `plan-approval` 的项目继续进入执行。0.2 增加了结构化产物、第二审批门、冻结清单和受控执行器，旧项目不具备这些安全契约。

## 推荐步骤

1. 保留旧 `research-mode` 和 `projects/deepseek-benchmark`，作为第一次真实运行的审计记录。
2. 将 0.2 解压到新的 `~/agent-workspace/research-mode-v02`。
3. 运行 `python3 researchctl.py doctor` 和 `tests/run_all.sh`。
4. 新建 `deepseek-benchmark-v02`，复制原研究目标、预算意图和经过人工确认的约束。
5. 让 0.2 从 framing 重新生成结构化产物，不复制旧 `state.json`、脚本、manifest 或 phase result。
6. 在两道审批门分别审阅研究设计和冻结后的执行清单。

```bash
cd ~/agent-workspace/research-mode-v02
python3 researchctl.py new deepseek-benchmark-v02 \
  --goal "系统比较 DeepSeek V4 Pro 与 V4 Flash 作为 OpenCode 主模型时，在科研代码任务上的质量、速度和成本"
python3 researchctl.py run deepseek-benchmark-v02
```

可以人工带入原始研究问题、已确认的模型名称和官方定价来源、预算与停止条件，以及对旧任务覆盖和统计功效的审查意见。

不要复制 `.research/state.json`、旧版结构化产物、执行脚本、grader、任务数据或审批状态。这样会增加一次重新规划的 API 成本，但能确保正式执行输入受 0.2 的校验、第二审批和冻结机制约束。

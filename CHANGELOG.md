# Changelog

## 0.4.0-alpha.4 — 2026-09-09

- 根据真实 `fp-summation-canary-a3` 的 hypotheses 引用失败，补全 Hypothesis Ledger Schema、最小合法模板与一次性聚合语义校验，明确 `evidence_ids` 只能引用 Evidence Record ID，禁止 Claim ID。
- 将 hypotheses 恢复拆成两条互斥通道：可证明等价的 Claim→Evidence 映射进入 `research-repairer`；空引用、未知引用或科学内容缺陷进入受冻结 Evidence Ledger 约束的 `research-reviser`。
- Grounded revision 无网络、无 Skills、不可新增假设或修改上游；只允许删除或重写未批准假设，并强制生成与实际 before/after 一致的机器可验 revision diff。
- 新增 `phase_revision_started/completed/blocked/refused` 事件，以及 timeout、进程退出、缺失/无效 phase result、语义失败、边界违规和 provenance 失败分类。
- 明确 `.research/phase-result.json` 是可覆盖邮箱而非科研产物，禁止模型将其列入 provenance；长期 provenance 只绑定正式阶段产物。
- 新增引用修复、受证据约束修订、伪造 Evidence ID、上游篡改、越界写入、错误 diff、诚实阻塞、缺失 phase result 和邮箱 provenance 回归测试。

## 0.4.0-alpha.3 — 2026-09-09

- 根据真实 `fp-summation-canary-a2` 的 experiment-plan 两次结构失败，将 evidence 专用修复生命周期抽象为带阶段策略的 bounded phase repair。
- `experiment-plan` 增加完整 Schema/最小合法模板、一次性聚合错误和隔离 `research-repairer` 通道。
- 允许 `task_id→id` 与可证明等价的 repetitions 表示规范化；修复前后强制保持假设、条件、任务、指标、设计、预算、停止条件和分析语义不变。
- 新增 `tasks × conditions × repetitions ≤ max_sessions` 及计划预算不得超过 research contract 的跨字段校验，拒绝无法落地或擅自扩大的实验计划。
- 新增计划首过/成功修复、无效修复、诚实阻塞、科学内容篡改、上游篡改、越界写入和预算不一致测试。

## 0.4.0-alpha.2 — 2026-09-06

- 根据真实 `fp-summation-canary-v04a` 的 evidence 失败，补全 Evidence Ledger Schema、枚举表与最小合法 JSON 模板。
- Evidence validator 从首错退出改为一次汇报全部结构问题，使单次修复能覆盖缺键、错误枚举和缺失快照。
- 第二次 evidence 尝试改为显式 repair，不再重复检索；新增禁用网络、Skills、外部目录和任意 Shell 的 `research-repairer`，并在临时隔离工作区运行。
- 每次无效产物与 phase result 保存到 `.research/invalid/`，保留真实失败样本。
- 修复前后强制保持 claims、queries、sources、evidence links、unresolved 和既有快照不变；仅校验通过的白名单文件原子回写，失败或越界修复不会污染项目。
- 允许为原始响应确实缺失的失败查询创建受控 failure record；该记录明确不是检索结果或证据。
- 新增成功修复、修复仍无效、篡改科学内容、篡改快照及越界写入测试。

## 0.4.0-alpha.1 — 2026-09-06

- 新增统一 `Executor` 抽象与首个串行 `LocalExecutor`；正式实验由框架逐 trial 调度，而非把全研究循环交给生成脚本。
- 定义单 trial 入口 ABI、稳定 trial ID、幂等键、attempt 输入/日志/结果和聚合 formal result。
- 增加原子 `executor-state.json`、文件锁、已完成 trial 跳过、活跃 PID 防重、死进程恢复和有效孤儿结果收养。
- 按 `timeout`、`process_exit`、`invalid_result` 分类失败；仅批准类别按预算重试，协议错误立即终止。
- 执行审批新增环境锁和执行授权，绑定实际 Python 解释器与运行时、依赖锁、代码/data 哈希、manifest、冻结输入和批准预算。
- Guard 在正式执行前校验环境与冻结文件，并在每个 trial 后按会话、成本和墙钟预算终止。
- 新增 trial result、executor state、environment lock Schema，以及幂等、恢复、预算、篡改和完整端到端回归测试。
- 当前只支持本地串行执行；Slurm、容器 clean-room reproduction 和跨外部系统 exactly-once 尚未实现。

## 0.3.0 — 2026-09-04

- 为九类科研结构化产物增加版本化 JSON Schema 与 Schema Registry。
- Evidence Ledger 升级为可回放查询、原始快照哈希、稳定 claim/evidence ID 和来源身份记录。
- 假设、实验计划、分析与决策强制引用上游稳定 ID。
- Controller 为每个阶段产物生成包含模型、Agent、Prompt hash、Skill snapshot 和输入哈希的 provenance sidecar。
- Workflow 显式声明每阶段输入；输入或任一关键上游变化会阻止审批与后续执行。
- 新增 `researchctl audit`，检查结构语义、证据快照、Skill lock、provenance 链和冻结输入。
- 新增 `skills.lock.json`；八项 K-Dense Skills 使用完整目录树哈希和固定上游提交。
- 准入 `paper-lookup` 2.1、`experimental-design` 1.2 和 `peer-review` 2.2，并增加离线 admission/negative-trigger 回归测试。
- Evidence 阶段自动使用 `paper-lookup`，项目初始化时复制四个已审查解析器到项目内运行目录；实验规划阶段自动使用 `experimental-design`。
- `peer-review` 与内部 critique 明确隔离，只有独立授权与 intake gate 完成后才允许进入未来的正式评审流程。
- 增加正常、矛盾、不可访问、错误快照、上游篡改和 Skill 篡改测试。

## 0.2.0 — 2026-09-04

- 增加第二道 `execution-approval` 审批门，在正式实验前复核 smoke、准确调用量和预计成本。
- 增加 Markdown + JSON 双产物及确定性语义校验，替代仅检查文件存在和模型自报成功。
- 分离 `research-worker`、`research-builder`、`research-executor` 与 `research-critic` 权限。
- 移除通用 `bash scripts/*` 放行；正式实验仅能通过受控执行器启动。
- 审批后对脚本、配置、任务、Agent 和 Guard 做 SHA-256 冻结，正式执行前后复核。
- 增加会话、成本和墙钟预算 Guard，以及预算预估超限拒批。
- 增加单项目文件锁、运行 ID、中断记录与 stale-running 恢复。
- 强化实验设计约束：至少两个条件、四个任务、三个类别、确定性 grader 与隐藏测试。
- 增加离线回归、语义失败、预算、冻结篡改、恢复/并发锁及 Guard 测试。

## 0.1.0

- 建立 goal-driven 科研状态机。
- 串联 framing、evidence、hypotheses、experiment-plan、implementation、smoke、formal experiment、analysis、critique、decision 和 report。
- 建立首个计划审批门、阶段重试和状态持久化。
- 集成项目级科学方法 Skills 与个人 Skills。

# Research Mode v0.4.0-alpha.3

发布日期：2026-09-09

## 目标

alpha.3 回应真实 `fp-summation-canary-a2` 暴露的问题：Evidence 已能稳定满足 ABI，但 experiment-plan 又出现 `repetitions` 类型漂移和 `task_id`/`id` 字段漂移。该版本把修复生命周期从 evidence 特例升级为“通用调度 + 阶段专属不变量”的首个实现。

## 主要变化

- Evidence 与 experiment-plan 共用：失败留档、隔离临时工作区、无网络 Repair Agent、有限尝试、白名单原子回写和 provenance。
- 每个可修复阶段拥有独立 `repair_policy`；Controller 不再假设待修文件必然是 evidence ledger。
- Experiment Plan 获得完整结构契约、最小合法示例和聚合语义校验。
- 只允许无歧义的 ABI 规范化；任何任务、指标、假设、实验设计、预算、停止条件或分析语义变化都会拒绝回写。
- 计划总 trial 数不得超过 `max_sessions`，且计划预算只能收紧、不能突破 research contract。当前最低覆盖要求为 4 tasks × 2 conditions，因此 repetitions=1 时至少需要 8 sessions；更小的初始会话预算会被诚实阻塞，必须由用户重新创建/授权研究契约。

## 迁移与验证

旧项目不能原地续跑。保留 alpha.2 的 blocked 项目作为审计样本，在 alpha.3 下创建新项目。先运行 `python3 researchctl.py doctor` 与 `./tests/run_all.sh`，再运行新的 DeepSeek Canary，并在 `plan-approval` 停止等待人工审阅。

## 仍未实现

alpha.3 仍只提供本地串行执行；没有 Slurm/SSH adapter、容器隔离、跨项目调度、资源 lease/heartbeat 或跨外部系统 exactly-once。

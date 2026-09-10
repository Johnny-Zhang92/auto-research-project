# Research Mode v0.4.0-alpha.4

发布日期：2026-09-09

## 目标

alpha.4 回应真实 `fp-summation-canary-a3` 在 hypotheses 阶段暴露的 ID 绑定与空证据问题。核心判断是：Claim ID 误填可以按冻结账本确定性修复；空引用代表科学支持不足，不能由结构修复器伪造。因此本版本首次将恢复拆成结构修复与 grounded revision 两条安全边界不同的通道。

## 主要变化

- Hypothesis Ledger 获得完整 Schema、最小合法示例和聚合 validator；`evidence_ids` 只接受 Evidence Record ID。
- `research-repairer` 只允许把 Claim ID 展开为账本中链接的 Evidence Record ID，保留所有假设内容、ID 与顺序。
- 新增 `research-reviser`：无网络、无 Skills、上游只读，只能删除或基于既有 Evidence Record 重写尚未批准的假设，不能新增假设。
- 每次 grounded revision 必须生成机器可验 diff；Controller 对真实 before/after、使用的 Evidence ID、删除/修订动作和理由逐项对账。
- 阶段失败增加明确分类；`.research/phase-result.json` 被定义为易变邮箱并禁止进入 provenance，避免后续阶段覆盖造成假阳性审计失败。
- 新增九类离线成功与故障注入覆盖，完整测试套件保持通过。

## 迁移与真实验证

alpha.3 项目不能原地续跑。保留真实 blocked 项目，在 alpha.4 创建全新 Canary。目标是让 framing→evidence→hypotheses→experiment-plan 连续推进并稳定停在 `plan-approval`，同时核对 hypotheses recovery 与 experiment-plan repair 的事件、invalid/revision 留档及 provenance。

## 仍未实现

alpha.4 仍只提供本地串行执行；没有 Slurm/SSH adapter、容器隔离、跨项目调度、资源 lease/heartbeat 或跨外部系统 exactly-once。

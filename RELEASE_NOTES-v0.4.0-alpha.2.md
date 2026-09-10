# Research Mode v0.4.0-alpha.2

这个版本回应第一次真实 DeepSeek Canary：科研内容和检索质量合格，但 Evidence Ledger 因字段名、自由枚举和缺失快照不符合严格协议，被确定性 Validator 正确阻断。

alpha.2 不放宽 Schema，而是在第一次语义失败后进入一次受限结构修复：

- Validator 一次返回全部 evidence 结构错误和允许枚举；
- 无效 ledger 与 phase result 留档，不会被第二次尝试覆盖；
- 修复使用独立 `research-repairer`，在临时隔离工作区运行，并禁用网络、Skills、外部目录和通用 Shell；
- Prompt 提供完整 JSON Schema、最小模板、精确错误和有限枚举映射；
- claim/query/source/evidence identity、科学内容、unresolved 和既有原始快照必须保持不变；
- 原始响应缺失的失败查询只能增加明确标识的空 failure record，它不是证据；
- 只有通过检查的白名单文件才原子回写；修改既有快照、引入新来源或写入允许路径之外都会再次阻断且不会污染原项目。

本版本的验收目标是让同类 Canary 自动推进到 `plan-approval`，同时证明修复权限不能成为改变证据的旁路。它尚未改变 LocalExecutor、Slurm 或远程执行边界。

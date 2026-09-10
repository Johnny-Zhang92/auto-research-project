# OpenCode 科研模式 0.3.0 发布说明

这是“证据与可复现内核”的稳定里程碑。旧项目仍应保留为审计记录，并在 v0.3 下新建项目，不要原地升级状态文件。

## 已实现

- 九类科研对象使用版本化 JSON Schema 和注册表；
- 检索查询保存 backend、endpoint、parameters、状态、结果数和原始快照 SHA-256；
- claim、evidence、hypothesis、trial 通过稳定 ID 连接；
- 每个模型产物由 Controller 生成 provenance sidecar；
- 上游变化会使审批或下游执行失败；
- `researchctl audit` 可检查语义、快照、Skill lock、provenance 和冻结输入；
- 实验设计契约新增分析单位、复制单位、随机化、区组、seed、数据划分和泄漏控制。
- 从 K-Dense v2.66.0 官方归档准入 `paper-lookup` 2.1、`experimental-design` 1.2 和 `peer-review` 2.2，锁定完整目录树哈希；
- evidence 和 experiment-plan 分别路由到检索与实验设计 Skill，正式同行评审保留独立 intake gate，不与内部科研审计混用；
- 项目初始化时复制 `paper-lookup` 的已审查解析器到项目内运行边界。

## 已通过的故障测试

- 正常、相互矛盾和来源不可访问的证据记录；
- 错误 query snapshot hash；
- 上游 evidence 被结构合法地篡改；
- 已启用 Skill 内容被篡改；
- 原 v0.2 双审批、预算、冻结、恢复和 Guard 回归测试。

## 仍然明确不做

- 不开放 SSH/Slurm 或无人监管的远程写操作；
- 不自动安装 `pyDOE3` 等依赖；
- 不把内部 critique 冒充正式同行评审；
- 不承诺同一检索在外部数据库变化后返回相同结果，系统保证的是查询与原始快照可回放。

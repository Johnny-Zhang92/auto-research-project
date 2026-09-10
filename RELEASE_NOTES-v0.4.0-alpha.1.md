# Research Mode v0.4.0-alpha.1

这是“可复现实验运行时”的第一个 alpha。它把 v0.3 已冻结的实验，从一个由生成脚本自行循环的黑盒，改成由控制器逐 trial 调度、校验、记账和恢复的本地执行协议。

## 核心变化

- `LocalExecutor` 串行执行 manifest 中的 trial，并为每次 attempt 保存输入、日志和结构化结果。
- 稳定 trial ID 与幂等键用于重启去重；已完成 trial 不再运行。
- `executor-state.json` 原子更新；死进程可重试或收养已经落盘且校验通过的结果，活进程会阻止第二执行者。
- 执行审批生成 environment lock 与 authorization，绑定实际 Python 解释器、运行时、依赖锁、代码、数据、配置、预算和冻结文件哈希；trial 复用同一解释器。
- timeout、process exit 与 invalid result 分开处理；协议错误不盲目重试。
- 正式汇总结果引用每个 trial result 的路径和哈希，Controller 会重新核对 checkpoint 覆盖与身份。

## 安全与可靠性边界

- 仅支持本地串行执行，不支持 Slurm、SSH 或多 Worker。
- 这不是容器沙箱；环境锁检测变化，但不会自动构建隔离环境。
- 对外部 API、数据库或调度器的真正 exactly-once 仍需外部系统配合。trial 入口必须传递或持久化框架提供的幂等键。
- 单次已经发出的计费 API 请求无法撤回；预算在 trial 边界检查。
- 本 alpha 面向小预算 canary 和故障注入，不建议直接用于长时间高成本实验。

## 升级

v0.3 项目不能原地进入此版本的执行阶段。请保留旧项目作为审计记录，在 v0.4 alpha 新建项目，只人工迁移 goal、约束、预算与核验后的事实。入口脚本必须采用单 trial ABI，详见 `README.md` 与 `MIGRATION.md`。

## 验证

离线测试覆盖 Schema、Skill lock、双审批、provenance、冻结篡改、环境篡改、预算拒批、LocalExecutor 幂等、timeout 重试、协议错误、活 PID 防重、死进程恢复与完整端到端流程。

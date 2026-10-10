# BareLoop Roadmap

> 最后更新：2026-09-11

## 状态说明

- idea：有想法，尚未确认
- planned：已确认，等待排期
- in_progress：正在开发
- done：已完成

## 社区任务列表 (Community Issues & Roadmap)

### 🌟 新手友好 (Good First Issue)
- [ ] `planned` **CLI 交互指令扩展**：新增 `/stats`（展示会话耗时与 Token 用量）、`/compact`（手动触发上下文压缩）及 `/export`（导出对话记录）。
- [ ] `planned` **文件工具支持 `.bareloopignore`**：在 `run_glob` 与 `run_read` 中增加模式匹配，自动忽略 `node_modules`、`__pycache__`、`.git` 等无关噪音。
- [ ] `planned` **模型定价配置补充**：扩充 `evals/model-pricing.example.yaml`，补全主流商业与开源模型的定价预设。

### 🛠️ 进阶工程优化 (Help Wanted)
- [ ] `planned` **Worktree 垃圾回收与自动清理 (GC)**：在会话结束或异常退出时，自动安全修剪过期的 Git Worktree 与临时分支。
- [ ] `planned` **Task 系统 DAG 成环检测**：在子任务依赖添加时执行拓扑环路校验，防止死锁。
- [ ] `planned` **Windows / 跨平台终端兼容**：优化不同操作系统下的路径规范化与 PowerShell 终端交互兼容性。

### 🚀 核心架构演进 (Core Features)
- [ ] `planned` **Goal Loop 的 Worker/Evaluator 闭环落地**：基于已有的状态模型实现独立的验收评估器、预算上限控制与长程任务自动续跑。
- [ ] `planned` **MCP 远程连接心跳与超时重连**：增强 Streamable HTTP 远程 MCP 服务在断网或波动下的自动重试与弹性恢复。
- [x] `done` **官方 SWE-bench 评测适配器**：支持标准 Issue 数据集执行、工作区检出与 predictions.jsonl 生成。

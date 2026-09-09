# BareLoop 实现真相、优化清单与面试记忆手册

> 核对基线：2026-09-06，`main` 分支，HEAD `c764e46`。本文以当前工作区源码和测试为准，不以 README 或简历中的规划性描述为准。当前 `src/bareloop/tools/shell.py` 有未提交修改，涉及 `shouldBack` 参数，相关影响会单独标记。

## 如何使用本文

每个模块都记录五件事：解决什么问题、当前代码怎样执行、已验证的行为、不能对外宣称的能力、面试必须记住的实现点。状态含义：

- **已实现**：当前调用链真实可达，并有源码或测试支撑。
- **部分实现**：核心结构存在，但能力范围小于简历表述，或缺少关键闭环。
- **未实现**：只有数据结构、注释、适配器或设计意图，没有可用执行链。
- **工作区回归**：由当前未提交修改产生，不代表 HEAD 基线。

## 0. 项目真实定位

BareLoop 是一个从基本原理实现的、兼容 OpenAI Chat Completions API 的 Coding Agent Runtime。它解决的不是具体业务问题，而是 Agent 编排过程难以阅读、修改和控制的问题。核心价值是把 `model → tool → result` 循环以及 Tool、Hook、Context、Memory、Task、Cron、Multi-Agent、MCP 等机制拆成可直接阅读的 Python 模块。

它目前是 pre-alpha 学习与实验项目，不是生产级框架。最准确的介绍是“代码路径透明、Host 侧控制明确”，不能说“已经具备完整可观测性、可靠调度和生产级安全”。

## 1. Runtime 入口与 Agent Loop

**状态：核心循环已实现；公开 Session 生命周期未实现。**

入口是 `src/bareloop/mian.py:init_agent()`：

1. 注册默认 Hooks。
2. 执行 `mcp_init()`，发现 MCP Tools 并动态注册。
3. 扫描 `.bareloop/skills/*/SKILL.md`。
4. 创建 `TraceWriter` 和共享 `messages`，首条消息为 system prompt。
5. 启动 Cron poller 与 queue processor，两者复用同一份 `messages` 和 Trace。
6. CLI 等待用户输入；等待期间每 250ms 检查 Lead 邮箱，有 Team 事件时取消当前输入等待并唤醒 Agent。
7. CLI 和 Cron 都必须拿到全局 `agent_lock` 后调用 `agent_loop()`，避免同时修改共享会话。

`agent_loop()` 的单轮真实顺序：

```text
消费 Cron Queue并注入 [Scheduled]
  → 按最近用户消息筛选 Memory
  → while True
      → 注入已完成的 Background Result
      → 每 3 个 Tool Round 插入 Todo Reminder
      → Tool Output Budget
      → Micro Compact
      → 每轮重新读取 Tool Schemas
      → Tokenizer 估算上下文
      → 超阈值则 Transcript + LLM Summary
      → 将 Memory 临时拼到当前用户消息副本
      → 调用 OpenAI Chat Completions
      → 无 Tool Call：Stop Hook → 输出 → 提取/整理 Memory → 返回
      → 有 Tool Call：PreToolUse → Dispatcher → 追加 Tool Result → 下一轮
```

当前限制与优化：

- `create_session()` 是空函数，`AgentSession` 也只有构造器字段，公开 Session 抽象尚未形成。
- 主循环没有最大轮数、取消、Provider Retry、Streaming 或 Fallback Model 路由，模型持续调用 Tool 时可能无限循环。
- `FALLBACK_MODEL`、`CODE_MODEL`、`MLX_MODEL` 只读取配置，没有进入路由。
- `mian.py` 文件名存在拼写问题；对外运行命令也必须使用 `bareloop.mian`。
- Context 压缩后原先记录的 `altitude_index` 可能失效，导致本轮 Memory 没有注入摘要后的消息。

**面试必须记住：** BareLoop 的主循环是同步 Chat Completions 循环；CLI、Cron 共享会话并通过一个进程内 Lock 串行进入；动态 Tool Schema 在每次模型调用前刷新，而不是启动时缓存。

## 2. Tool Model、Registry 与 Dispatcher

**状态：中央注册和 Scope 调度已实现；调用参数校验未完整实现。**

`ToolDefinition` 是冻结 dataclass，包含 `name`、`description`、`parameters`、`handler`、`allow_subagent` 和 `supports_background`。构造时通过 `Draft202012Validator.check_schema()` 校验 Schema 本身，并用白名单递归拒绝未知 JSON Schema Keyword；`as_openai_tool()` 转成 OpenAI Function Tool 格式。

Registry 分为静态 `_TOOL_DEFINITIONS` 和动态 `_DYNAMIC_TOOLS_BY_NAME`。`get_tool_definitions(scope)` 在 main scope 返回全部静态和动态工具，在 subagent scope 只返回 `allow_subagent=True` 的工具。MCP 通过 `register_dynamic_tools()` 原子加入动态区：先检查与内置工具、当前批次和已注册动态工具的命名冲突，整批通过后才 `update()`。

`dispatch_tool()` 执行四步：查找 Tool → 校验 Scope → 若支持后台且 `shouldBack=True` 则交给 BackgroundManager → 否则调用 handler；所有 handler 异常被转换成字符串返回模型。

当前内置 Registry 有：`bash/edit/write/read/glob/load_skill/spaw_subagent`、五个 Task Tools、六个 Team 协作 Tools 和 `create_worktree`。Cron 的三个 Adapter 当前**没有注册**，模型无法通过中央 Tool Registry 新建、查看或取消 Cron。

当前限制与优化：

- 代码只校验“Schema 是否合法”，没有在 Dispatcher 中使用 Schema 校验模型传入的实际 Arguments。
- Tool 名 `spaw_subagent` 拼写错误，已经成为模型可见接口，修正时需要兼容旧名称。
- 如果模型显式传入 `shouldBack=False`，Dispatcher 会把它原样传给同步 handler。HEAD 版本 `run_bash` 曾接收并丢弃该参数；当前未提交修改删除了这个参数，此时会出现 `unexpected keyword argument 'shouldBack'`。应在 Dispatcher 消费控制字段，避免 Runtime 元数据泄漏到业务 Handler。
- Tool 错误全部变成普通字符串，没有结构化错误码、可重试标志或 Trace 关联。

**面试必须记住：** Registry 解决 Tool 定义、Schema 暴露和 Scope；Dispatcher 解决实际路由与后台分流；二者职责分开。动态 Tool 不缓存到 Loop 常量，所以 MCP 初始化后下一轮即可被模型看到。

## 3. Filesystem、Shell 与安全边界

**状态：Filesystem 路径边界已实现；Shell 安全只依赖基础 Hook。**

Filesystem Tool 把传入 `cwd` 作为安全根目录。`_resolve_path()` 对相对或绝对路径做 `resolve()`，并要求结果仍在根目录下，因此 `../`、绝对路径逃逸和符号链接解析后的越界会被拒绝。`edit` 只替换第一个匹配；`read` 支持行数上限；`write` 自动创建父目录；`glob` 只返回根目录内结果。

Shell 使用 `subprocess.Popen(shell=True)`，创建独立进程组，默认超时 120 秒，合并 stdout/stderr，并把输出截到 50000 字符。超时时 finally 会先 SIGTERM，再尝试 SIGKILL。进程集合由 RLock 保护，但目前没有公开的批量取消接口。

安全边界的关键区别：Filesystem 自身有强制根目录检查；Shell 自身没有 Workspace 限制，只依赖 PreToolUse Hook。用户批准外部 `cwd` 后 Filesystem 仍被限制在该外部根目录中。

当前限制与优化：

- `shell=True` 配合字符串黑名单不是可靠沙盒，命令拆分、别名、编码和间接执行都可能绕过。
- Shell 输出按字符截断且只保留开头，可能丢失真正错误所在的尾部。
- 没有环境变量白名单、网络策略、资源配额或 OS Sandbox。
- 主 Registry 使用 `run_bash`，没有使用可根据 Task Lease 路由 CWD 的 `run_agent_bash`，因此主 Agent 领取带 Worktree 的 Task 后不会自动进入该 Worktree。

**面试必须记住：** 路径安全来自解析后的 containment check，不是字符串前缀；Shell 只是“有审批的本机执行”，不能称为沙盒。

## 4. Hook 与权限管线

**状态：基础生命周期注册和三种结果效果已实现；通用 Policy Pipeline 未实现。**

Hook Registry 预定义 `PreUserPromptInput`、`PreToolUse`、`PostToolUse`、`Stop` 四类事件。默认初始化只注册：

- `PreUserPromptInput → context_inject_hook`：打印当前工作目录。
- `PreToolUse → permission_hook`：处理主 Agent 的 Shell 和文件工具。
- `Stop → summary_hook`：统计当前 Messages 中 Tool 消息数量。

主 Agent 的 Shell 命令命中 `DENY_LIST` 时直接拒绝；其余 Shell 命令都会询问用户。命中 `DESTRUCTIVE` 只额外打印提示，并不会改变审批流程。Workspace 内文件访问直接允许，Workspace 外访问询问用户。因此行为上存在 allow/deny/ask 三种结果，但代码中没有显式的 Policy Decision 类型或可配置规则链。

`trigger_hook()` 按注册顺序执行，首个非 `None` 返回值会短路；Callback 异常会被捕获并转成字符串。Agent Team 主要使用自己的 Plan Gate 与 `check_permission()`，只在工具执行后触发 `PostToolUse`，并未完整复用主 Agent 的 PreToolUse 流程。

当前限制与优化：

- 多次调用 `hook()` 会重复注册 Callback，没有幂等初始化或注销机制。
- Hook 异常在 PreToolUse 中会作为阻断字符串返回，但其他事件的错误语义不清晰。
- Permission 使用子串规则，容易误报和绕过；没有按 Tool、参数、调用者、工作区或风险级别组合策略。
- PostToolUse 默认没有 Callback；Model Call、Compaction、Memory、Background 等也不是统一生命周期事件。
- 所以简历可写“基于 Hook 的基础权限拦截”，不宜写成成熟的统一安全控制面。

**面试必须记住：** Pre Hook 返回非空即短路；主 Agent 与 Agent Team 的权限实现并不统一；allow/deny/ask 是当前行为结果，不是完善的策略模型。

## 5. Context Compaction

**状态：多层处理已实现；Reactive Compaction 未实现。**

当前代码没有 `messages[-N:]` 形式的直接消息裁剪。可以将实现解释成四种机制，但源码没有显式 Level 抽象：

1. **大型 Tool Output 落盘。** 内容达到 1000 字符时写入 `.bareloop/.task_outputs/tool-results/`，文件名包含安全化 Tool Call ID 和内容 SHA-256 前 16 位，Context 只保留路径与最多 256 字符预览。
2. **当前 Tool Batch Budget。** 从消息末尾向前扫描，遇到 user/assistant 就停止；本批 Tool Result 总长度超过 20000 时，优先落盘最大的结果，直到回到预算内。变量名叫 `max_bytes`，实际计算的是 `len(str)`，并非字节数。
3. **旧 Tool Result 微压缩。** Tool 消息达到 50 条时，保留最近 50 条；更早且超过 120 字符的 Tool 内容替换为固定占位符。
4. **全历史语义压缩。** Tokenizer 用 Messages 加当前 Tool Schemas 渲染；超过 `CONTEXT_LIMIT=1000` 后，先把完整 Messages 写入 JSONL Transcript，再调用主模型生成最多 2000 Token 的摘要，最后只保留全部 system/developer 消息和一条 `[Compacted]` user 摘要。

为什么不只做全量摘要：大型 Tool Output 是主要噪声源，先用确定性落盘和占位可降低 LLM 摘要成本；Transcript 保留恢复入口；system/developer 指令单独保留，避免被摘要改写。

当前限制与优化：

- Provider 因 Context Overflow 失败后的 `reactive_compact` 只有注释，实际会打印错误并结束本轮。
- `summarize_history()` 只向摘要模型传入序列化后的前 80000 字符，可能恰好丢掉最新进展。
- Micro Compact 可能删除尚未落盘的旧 Tool Result；也可能进一步抹掉已落盘结果在 Context 中的路径。
- Transcript 文件名只精确到秒，同一秒两次压缩可能覆盖。
- 阈值硬编码且与真实模型 Context Window 无关；摘要模型与主模型相同，没有独立配置和失败降级。
- 没有验证压缩后 Token 一定低于限制，也没有保存结构化 Current Goal/Todo 状态。

**面试必须记住：** 每轮先做 Tool Budget 和 Micro Compact，再计算包含 Tool Schema 的 Token；超限时先保存 Transcript，再摘要。不能说已经支持模型失败后的恢复压缩。

## 6. Memory：筛选、提取、整理

**状态：三阶段闭环已实现；真实性、去重和污染控制主要依赖模型 Prompt。**

### 筛选 Selection

每次进入 `agent_loop()` 时执行一次。系统读取全部 Memory 文件的 `name + description` 目录，收集最近三条 user 消息，让模型只返回相关 Memory 的索引数组，最多选五条。选中的 Markdown 全文包在 `<relevant_memories>` 中，仅临时拼到当前用户消息的请求副本，不写回共享 Messages。

### 提取 Extraction

当本轮模型返回最终文本、没有 Tool Call 时执行。系统截取本轮新增的 user/assistant 对话，最多取前 4000 字符，并把现有 Memory 的名称与描述交给模型；模型返回 `{name,type,description,body}` 数组后逐条写成带 YAML Front Matter 的 Markdown，再重建 `MEMORY.md` 索引。

### 整理 Consolidation

每轮提取后检查 Memory 文件数；达到 10 个时调用模型合并重复项、删除过期或冲突项，要求总数不超过 30。输出需经过非空字段、类型枚举、数量和文件名唯一性校验。通过后先写 Staging Directory，再把旧目录改名为 Backup、用 `os.replace` 切换新目录；失败时恢复 Backup。启动时也能恢复中断的目录交换。

当前限制与优化：

- 提取阶段没有复用 `_validated_memory_items()`，因此未严格验证 `type`、字段类型、数量和重复 Slug。
- “避免重复”和“识别错误事实”主要靠 Prompt，没有来源、置信度、时间、版本、用户确认或冲突规则。
- 同名 Slug 会覆盖原文件，可能把已有正确记忆静默替换。
- Selection 使用最近三条 user 消息且直接拼接，顺序是从新到旧，没有分隔符；没有 Embedding 或确定性关键词回退。
- Extraction 只看前 4000 字符，Consolidation 只看 Catalog 前 16000 字符，长内容可能被静默忽略。
- 整理是“全量替换 Memory Set”，虽有原子目录交换，但错误且格式合法的模型输出仍可能造成语义数据丢失。

**面试必须记住：** Selection 发生在调用前，Extraction/Consolidation 发生在 Stop 后；跨会话复用来自 `.bareloop/.memory` 磁盘文件，不是模型原生记忆；一致性保障是 Staging + Backup + `os.replace`，不是数据库事务。

## 7. Skill 按需加载

**状态：本地发现和按需加载已实现；信任与生命周期管理未实现。**

启动时 `_scan_skills()` 只扫描 `.bareloop/skills/` 的一级子目录。存在 `SKILL.md` 时，用 `yaml.safe_load` 解析 Front Matter；名称默认取目录名，描述默认取 Markdown 第一行。Registry 在内存保存 name、description 和完整正文。

System Prompt 只放入 Skill 的名称和描述，并要求模型先判断是否匹配，再调用 `load_skill(name)` 获取完整正文。这降低了初始 Prompt 体积，构成“发现元数据 → 模型选择 → 按需加载全文”的流程。

当前限制与优化：

- 只在启动时扫描一次，没有热更新、版本、依赖、冲突报错或 Registry 清理；同名 Skill 后扫描者会覆盖前者。
- Skill 内容直接进入模型上下文，没有来源信任、签名、权限声明或 Prompt Injection 隔离。
- 只加载文本说明，不提供资源路由、脚本执行规范或渐进式依赖加载。
- 没有独立测试覆盖 Skill 扫描、坏 YAML、重复名称和运行时加载。

**面试必须记住：** 按需加载指“System Prompt 只暴露元数据，模型通过 Tool 再取正文”，不是 Python 的 Lazy Import，也不是远程 Skill Marketplace。

## 8. Background Task

**状态：进程内后台 Shell 已实现；完成后主动唤醒 Agent 未实现。**

当 Dispatcher 发现 Tool 支持后台且参数 `shouldBack=True` 时，不执行同步 Handler，而是调用 `BackgroundManager.start_task()`。Manager 创建 daemon Thread，在线程中复用 `run_shell_process(command, cwd)`；任务状态、结果和 Ready Queue 都保存在内存字典中，并由 Lock 保护。

Agent Loop 每次 while 迭代开头调用 `inject_background_results()`。已完成任务被原子收集，转换成 `<task_notification>` user 消息，包含 task_id、status、command 和最多 500 字符摘要，随后参与下一次模型调用。

当前限制与优化：

- 如果后台任务在 Agent 已输出最终答案后才完成，没有线程主动唤醒主 Agent；结果只能等下一次用户、Cron 或 Team 事件触发新一轮 Loop。
- 任务、结果和计数器都不持久化，进程退出后无法恢复；ID 形如 `task_0os`，重启会重复。
- 没有查询、取消、超时配置、并发上限、队列背压或完成回调。
- Daemon Thread 在进程退出时可能被直接终止。

**面试必须记住：** 当前“自动通知”实际是 Loop 轮询后注入，不是 Background Manager 主动发送事件；只适合当前 Session 内的 Shell 长任务。

## 9. Cron Scheduler

**状态：持久化调度与投递恢复已实现；模型侧创建入口和完整容错未闭环。**

`CronJob` 保存 id、五段 cron、prompt、recurring、pending_delivery 和 last_fired。Job Set 持久化到 `.bareloop/.schedule_task.json`，所有写入先写 PID/Thread ID 临时文件，再 `os.replace()`。

运行时有两个 daemon Thread：Poller 每秒检查到期任务；Queue Processor 每 200ms 检查 Queue，并尝试非阻塞获取共享 `agent_lock`。到期任务先设置 `pending_delivery=True` 并持久化，再放入内存 Queue，因此进程在持久化之后崩溃，重启时可根据 Pending 标记重新入队。

投递语义：

- Agent Loop 消费 Queue 后，把 Job 包装成 `[Scheduled]` user 消息。
- 第一次 Provider 调用尚未返回就失败：移除 Scheduled 消息并重新入队。
- Provider 已经返回过一次，即使后续 Tool Round 失败，也确认任务，避免重复执行已经开始的副作用。
- Recurring Job 确认后清除 Pending；One-shot Job 确认后从持久化集合删除。
- Acknowledge 持久化失败时，会恢复 Job 状态并重新入队。

Cron 支持 `*`、`*/step`、逗号列表、闭区间和单值；当 day-of-month 与 day-of-week 都受限时采用标准 Cron 的 OR 语义。星期日使用 0。

当前限制与优化：

- `run_cron_scheduler/run_cron_list/run_cancel_cron` Adapter 存在，但没有注册到中央 Registry，Agent 当前不能自行创建或管理 Cron。
- 简历中的“失败恢复”只能限定为 Pending Delivery 和持久化写失败恢复，不包含线程崩溃重启、Provider 重试或 Dead Letter Queue。
- Queue Processor 内没有顶层异常恢复；一次未捕获异常可能让线程永久退出。
- 没有时区配置、秒级调度、错过执行补偿、并发策略、最大重试次数或失败告警。
- Parser 不支持名称、`L/W/#`、范围步长等完整 Cron 语法，`*/step` 也未限制 step 上界。

**面试必须记住：** 可靠性的关键顺序是“先持久化 Pending，再入队”；确认点是 Provider 首次接受并返回，而不是整个 Agent 任务业务验收完成，因此语义不是严格 Exactly Once。

## 10. Todo 与持久化 Task DAG

**状态：持久化依赖、跨线程/进程锁和原子认领已实现；完整 DAG 校验和恢复未实现。**

每个 Task 是独立 JSON 文件，字段包括 id、subject、description、owner、blocked_by、status 和可选 worktree。状态仅有 `pending → in_process → completed`。ID 使用 4 字节随机十六进制，并通过排他创建模式 `open("x")` 避免碰撞。

并发控制由两层组成：进程内 `threading.RLock`，以及 Unix `fcntl.flock` 文件锁。Thread-local depth 让同一线程中的嵌套 Task 操作只获取一次文件锁。保存 Task 时先写临时文件，再 `os.replace()`，避免半写 JSON。

创建时会去重 `blocked_by` 并要求依赖已存在；Claim 时重新加载所有依赖并要求全部 `completed`，随后在同一个 Task Lock 内检查状态、Owner 和 Assignment Lease。一个 Owner 同时只能有一个进行中的 Task。竞争领取同一个 Task 时只有一个 Owner 成功。Complete 要求调用者与 Owner 一致，并把状态改成 `completed`。

Assignment Lease 保存在内存 `teammate_assignment_info` 中，记录 task_id 和解析后的 cwd。任务完成后 Lease 不立即释放，要等 Model Turn 边界，保证该轮剩余工具仍在原工作目录执行。

当前限制与优化：

- Task 只是带依赖边的持久化记录，没有拓扑排序、环检测、反向依赖、优先级、超时、重试和失败/取消状态。
- Assignment Lease 不持久化；进程重启后 `in_process` Task 仍保留 Owner，但 CWD Lease 丢失，可能形成孤儿任务。
- Task JSON 与 Lease 分属磁盘和内存，不能构成跨进程一致事务。
- `save_task()` 是公开写入口，可绕过 Create 的依赖存在性检查并构造环。
- 只支持 `fcntl`，因此当前锁实现不跨平台到 Windows。

**面试必须记住：** 原子认领来自“同一 Task Lock 内完成依赖检查、状态检查和保存”；文件原子替换只防半写，不单独解决并发；Lease 保留到 Turn Boundary 是为了 CWD 一致性。

## 11. Subagent

**状态：隔离消息上下文的同步子循环已实现；并行协作和持久化未实现。**

主 Agent 调用 `spaw_subagent` 后，会在当前调用栈同步执行 `spawn_subagent(query)`。Subagent 使用独立的 Messages，只含一条 user query；最多进行 30 次模型调用。它获得在模块导入时缓存的 subagent scope schemas，目前是 `bash/read/write/edit/glob`。每个 Tool Call 同样经过 PreToolUse Hook 和 Dispatcher；无 Tool Call 时触发 Stop Hook并返回文本给主 Agent。

当前限制与优化：

- 它不是线程、进程或远程 Worker，会阻塞主 Agent，不能并行。
- `SUBAGENT_SYSTEM_PROMPT` 虽已定义，但没有加入 Messages，工作目录和“禁止继续委派”等约束实际没有生效。
- Tool Schemas 在 import 时缓存，之后动态注册或配置变化不会刷新。
- 没有 Memory、Compaction、Task 认领、消息通信、Trace 或取消机制。
- 达到 30 轮后只返回最后一个非空 Assistant 文本，否则返回错误；没有结构化终止原因。

**面试必须记住：** Subagent 的“独立上下文”是真的，但“并行 Agent”不是真的。它本质是主调用栈内的受限 Agent Loop。

## 12. Agent Team

**状态：长期线程、独立上下文、邮箱通信、Plan Gate 与 Task 自动领取已实现；状态持久化和完整可靠性未实现。**

每个 Teammate 是一个 daemon Thread 和独立 `TeammateRuntime.messages`。Runtime 在 `continue/idle/stop` 状态间循环：Working 时调用模型并执行 Tool；没有 Tool Call 时向 Lead 发送 Result，然后进入 Idle；Idle 时通过 Condition 等待邮箱，并每两秒尝试领取下一个 Ready Task。

MessageBus 使用 `.bareloop/.mailboxes/{agent}.jsonl`。Send 在 RLock/Condition 内追加 JSONL 并 `notify_all()`；Read 会解析全部行后删除邮箱文件。Lead 的 CLI Prompt 等待会轮询 Lead Mailbox，发现消息后取消用户输入等待，把 Team Events 格式化成 user message 并启动新 Agent Turn。

Teammate 可使用文件、Shell、消息和 Task Tools。文件操作固定路由到 Assignment Lease 的 CWD。Plan Gate 对 `bash/write/edit` 生效：required 或 pending 时拒绝修改；Teammate 提交 Plan 后生成 Request ID，Lead 审批，再由 Teammate 校验 Request ID、发送者、接收者、Assignment Version 和 Task ID，防止旧审批应用到新任务。

Shutdown 同样使用带 Request ID 的请求/响应协议。线程异常退出时会释放 Assignment：未完成 Task 回到 pending、Owner 清空、Lease 删除，并清理无法再消费的协议状态。

当前限制与优化：

- Teammate Thread 是长期运行的，但 Active State、Pending Requests、Plan Gate、Assignment Version 和 Thread Registry 都只在内存中，进程重启后不会恢复。
- Mailbox 仅有同一进程内锁，没有跨进程锁、消息 ID、消费确认、去重或损坏行隔离；Read 后直接删除整个文件。
- Model Call 没有 Retry、Context Compaction、Memory、最大轮数或 Token Budget。
- 当前工作区的 `TeammateRuntime.bash()` 仍传 `shouldBack`，但未提交的 `run_bash()` 修改删除了此参数，会导致真正执行时 TypeError；现有测试通过是因为测试替换了一个兼容旧签名的 Fake。
- Python Thread 能让网络等待并发，但不等于独立故障域；共享全局 OpenAI Client 和大量内存字典仍是耦合点。

**面试必须记住：** Agent Team 与 Subagent 的核心区别是：Team 有长期 Thread、独立消息历史、Mailbox、Task/Lease、Idle 自动认领和协议状态；Subagent 是同步短生命周期子循环。

## 13. Git Worktree 隔离

**状态：创建、绑定、注册校验、CWD Lease 与失败回滚已实现；清理和集成未实现。**

Worktree 固定创建在 `.bareloop/.worktrees/{name}`，分支名是 `wt/{name}`。Name 必须匹配安全正则，禁止 `..`；解析后路径必须位于 Worktree Root 内。

创建前在 Task Lock 内验证：Task 存在且 pending/unowned、没有已有 Worktree、Name 未绑定其他 Task、路径和 Branch 不存在、当前 WORKDIR 是 Git Repo Root、Worktree Registry 中无冲突。然后执行 `git worktree add -b wt/name path HEAD`，成功后把 Worktree Name 写入 Task JSON。

如果 Git 返回失败但留下 Branch、目录或注册项，函数返回 Partial Operation 并要求人工恢复；如果 Git 成功但 Task 保存失败，则强制删除 Worktree 和 Branch。领取或执行期间会再次读取 `git worktree list --porcelain`，验证路径仍被注册且位于预期 Branch，防止仅靠目录存在产生错误路由。

当前限制与优化：

- 没有删除 Worktree、合并 Branch、冲突处理、垃圾回收和启动恢复流程。
- Branch 总是从当前 `HEAD` 创建，没有可选 Base Ref，也没有 Dirty Working Tree 策略说明。
- 主 Agent 的中央 Bash/File Tools 没有统一接入 Lease CWD；完整路由目前主要作用于 Agent Team。
- 回滚使用强制 Worktree Remove 和 Branch Delete，若未来允许用户在 Worktree 中产生未提交修改，需要更严格的保护。

**面试必须记住：** Worktree 不是安全沙盒，它只隔离 Git Checkout 和修改面；真正的正确性来自 Task Binding、Git Registry 再校验和 Lease CWD 三者一起工作。

## 14. MCP Client 接入

**状态：Streamable HTTP 发现、Namespace、动态注册和本地/远程降级已实现；超时重连未实现。**

启动时构造一个名为 `demo` 的 MCPConfig，先尝试本地 `MCP_LOCAL_URL`，不可达或初始化失败再尝试 `MCP_REMOTE_URL`。连接过程包括 OPTIONS 可达性探测、Streamable HTTP Transport、ClientSession 初始化握手和 `tools/list`。工具暴露名统一转换为 `mcp__{client}__{tool}`，避免与内置 Tool 冲突。

发现结果转换为 `ToolDefinition` 并动态加入中央 Registry。启动发现用的连接随后关闭。每个动态 Handler 被调用时会新建 MCPClientManager，再次按本地到远程顺序连接、调用一次 Tool、关闭资源；同步 Agent Loop 通过 `asyncio.run()` 执行，若调用方已有 Event Loop，则临时创建单线程 Executor 承载新的 Async Loop。

返回值优先序列化 `structured_content`，否则拼接 Text Block 或其他 Block 的 JSON；`is_error` 时加 `MCP error:` 前缀。带 Bearer Token 的 Endpoint 必须使用 HTTPS，只有 localhost/loopback 允许 HTTP；Token 不出现在错误日志中。

当前限制与优化：

- “每次调用重新连接”可以恢复陈旧连接，但代码没有在一次调用超时或断连后自动 Retry/Reconnect，因此简历不能写“加入超时重连”。
- Timeout 只是配置：Reachability 2 秒、HTTP Connect/Write/Pool 10 秒、HTTP Read 300 秒、MCP Session Read 60 秒；没有统一 Deadline、Retry 次数或退避。
- 目前只从环境变量组装一个固定名为 demo 的 Server，不是任意数量 Server 的通用配置层。
- `mcp_init()` 任一阶段失败会整体降级为空列表；没有 Required/Optional Server、局部成功保留或健康状态。
- 动态 Tool 默认不允许 Subagent 使用，也没有独立的 MCP 权限校验；最终仅经过主 Loop 的通用 PreToolUse，而默认 Permission Hook 不认识 MCP Tool。

**面试必须记住：** Discovery Connection 与 Invocation Connection 分离；Namespace Route 保存 Client Name 与 Server 原始 Tool Name；当前具备 Local→Remote Fallback 和 per-call reconnect，不具备失败后的 retry reconnect。

## 15. Goal Loop 与 AgentSession

**状态：未实现。**

`GoalState` 只有 condition、iterations、set_at、tokens_at_start 和 last_reason。`GoalController.__init__()` 只保存 evaluator、最大阻断次数、events、active、last_status 和 consecutive_blocks，并校验 `block_num >= 1`。`AgentSession.__init__()` 只保存 prompt 和 client，其他参数没有形成行为。

当前不存在 Worker/Evaluator 循环、Stop Hook 验收、自动续跑、Token/Iteration Budget、异常恢复、Goal 持久化、Resume 或公开 Session 生命周期，也没有 Goal 相关测试。

**面试必须记住：** 这个模块目前只能作为设计方向讨论，不能作为已落地经历写进简历。准确表述是“正在设计 Worker/Evaluator 分离的 Goal Loop，已定义初始状态模型”。

## 16. Trace

**状态：线程安全 JSONL Writer 已实现；完整运行时埋点未实现。**

每个 `TraceWriter` 创建独立 Trace ID 和时间戳文件。`write()` 在实例 Lock 内递增 Sequence，记录 trace_id、sequence、本地时区毫秒时间、type 和自由 Data；非 JSON 值通过 `default=str` 转换。每条事件单独以 append 模式打开文件写入。写失败只打印日志，不影响 Agent 主流程。

测试验证了单线程顺序、20 个并发写入的连续 Sequence、非 JSON 序列化和失败隔离。当前实际接入事件只有 CLI 用户输入、停止对话、收集 Cron、提取相关 Memory；Model Request/Response、Tool Call/Result、Hook Decision、Compaction、Background、Task、Team 和 MCP 都未系统接入。

当前限制与优化：

- 不能据此声称“完整可观测性”；它目前是可靠 Writer 加少量 Call Site。
- 没有 Schema Version、Span/Parent ID、耗时、Token、错误级别、脱敏、Rotation 或查询工具。
- 全局 `trace_lock` 没有使用；多个 Writer 写各自文件，不形成统一 Session Trace。

**面试必须记住：** Writer 的线程安全和 Runtime 的埋点覆盖率是两件事；前者已有测试，后者仍不足。

## 17. 测试与当前质量基线

2026-09-06 实际执行结果：

```text
uv run pytest -q       87 passed in 9.70s
uv run ruff check .    All checks passed!
```

已覆盖重点包括：Filesystem CWD 边界、基础 Permission、Cron 表达式与投递确认/恢复、Background 并发收集、Tool Output 落盘、Compact 指令保留、Memory 原子目录替换、Task 原子认领、Assignment Lease、真实 Git Worktree 创建与回滚、Agent Team 协议状态、MCP 动态注册，以及 Trace 并发顺序。

没有或明显不足的覆盖包括：真实 Provider 端到端、完整 CLI Session、Skill、Subagent、Goal、主 Loop 最大轮数/压缩失败、Memory Selection/Extraction 质量、Cron Tool Registry 接入、Cron Thread 崩溃恢复、MCP 调用超时重试、主 Agent Worktree 路由，以及当前 `shouldBack` 签名回归。

测试全绿只代表现有断言全部满足，不代表简历列出的全部能力已经实现。尤其 Agent Team 的 Bash 测试使用了兼容旧签名的 Fake，因此没有发现当前工作区的真实签名不匹配。

## 18. 简历陈述真实性矩阵

| 简历主题 | 当前真实性 | 可以说 | 暂时不能说 |
| --- | --- | --- | --- |
| Skill 按需加载 | 部分实现 | 启动扫描元数据，模型按名称加载完整正文 | 热加载、依赖解析、安全隔离、远程 Skill |
| 四级 Context Compaction | 部分实现 | 大输出落盘、Batch Budget、旧 Tool 占位、Transcript + Summary | 明确状态机式四级、Provider 失败后 Reactive Recovery |
| 三阶段 Memory | 已有闭环但实验性 | LLM Selection、Stop 后 Extraction、阈值 Consolidation、原子目录切换 | 可靠事实校验、确定性去重、向量检索、完整污染防护 |
| Subagent | 部分实现 | 独立 Messages、受限 Tool Scope、最多 30 轮 | 并行、长期运行、Task/Message/Memory 协作 |
| Agent Team | 实验性实现 | Thread、独立 Messages、JSONL Mailbox、Plan Gate、Task 自动领取 | 跨重启恢复、可靠消息队列、独立故障域 |
| Git Worktree | 已实现核心 | Task 绑定、Registry 校验、Lease CWD、创建失败回滚 | 自动合并/清理、主 Agent 全工具统一路由、安全沙盒 |
| Background Task | 部分实现 | 后台 Thread 执行 Shell，后续 Loop 注入结果 | 完成后立即主动唤醒、持久化恢复、取消与并发治理 |
| Cron Scheduler | 部分实现 | 五段表达式、原子持久化、Pending Delivery、共享 Session 投递 | 模型当前可创建 Cron、线程自动重启、通用失败恢复 |
| Tool Registry/Hook | 部分实现 | Schema 合法性检查、动态注册、Scope、Pre/Stop Hook、基础审批 | Tool Arguments 全量校验、成熟 allow/deny/ask Policy Engine |
| Task DAG | 核心已实现 | 持久化依赖、依赖 Gate、嵌套跨进程锁、原子认领、Owner Lease | 环检测、失败/取消/重试、Lease 跨重启恢复、完整 DAG 调度 |
| MCP | 部分实现 | Streamable HTTP、tools/list、Namespace、动态注册、Local→Remote Fallback | 超时后自动重连重试、多 Server 通用配置、MCP 专属权限 |
| Goal Loop | 未实现 | 状态模型和设计方向 | Worker/Evaluator、Stop Hook 验收、自动续跑、状态保留 |
| Trace | Writer 已实现 | 有序线程安全 JSONL Writer 和少量运行时事件 | 完整链路 Trace、Span、指标、脱敏和查询 |

## 19. 优化路线图

### P0：先修复“简历描述与代码不一致”以及真实执行故障

1. 修复 `shouldBack` 控制参数边界：由 Dispatcher 消费，或恢复 Handler 兼容签名；增加使用真实 Handler 的 Dispatcher/Agent Team 集成测试。
2. 把 Cron Tools 注册进 Registry，并补模型可见 Schema 与端到端创建/取消测试。
3. 将 Goal Loop 从简历“已实现”移除，或者真正实现 Worker/Evaluator、Stop Hook、Budget、续跑和持久化。
4. MCP 若保留“超时重连”表述，必须实现可分类的 Retry、指数退避、最大次数和幂等风险控制；否则修改简历为 Local/Remote Fallback 与 per-call reconnect。
5. Background 若保留“完成后自动通知”，需要完成事件唤醒共享 Session；否则改为“在后续 Agent Loop 自动注入完成结果”。

### P1：补可靠性闭环

1. 统一主 Agent、Subagent、Agent Team 的 Tool Pipeline：Schema Arguments 校验、Policy Decision、Pre/Post Hook、Trace 和结构化错误走同一条路径。
2. 给主 Loop 和 Team Loop增加最大轮数、取消、Provider Retry、Compaction Failure Fallback 和 Token Budget。
3. 持久化 Assignment Lease、Plan Request 和 Team State；启动时恢复或回收孤儿 `in_process` Task。
4. Cron Queue Processor 增加顶层异常保护、健康状态和线程重启；明确 At-most-once/At-least-once 边界。
5. Memory Extraction 复用严格 Validator，引入 provenance、confidence、updated_at、冲突状态和用户确认通道。
6. 修复 Summary 输入策略，优先保留最新消息；压缩后重新计算 Token 并验证低于限制。

### P2：再扩展工程能力

1. Task 增加 cycle detection、failed/cancelled、retry、priority、deadline 和反向依赖查询。
2. Worktree 增加安全清理、合并流程、Base Ref、Dirty State 检查和 Main Agent CWD 路由。
3. MCP 支持多 Server 配置、Required/Optional、局部降级、健康检查和细粒度权限。
4. Trace 增加统一 Event Schema、Span、耗时/Token、脱敏、Rotation 和回放工具。
5. Skill 增加刷新、冲突检测、信任来源、资源依赖和权限声明。

## 20. 面试速记：每个模块一句话

**Agent Loop：** 共享 Messages 上循环调用 Chat Completions；有 Tool Call 就经过 PreToolUse 和 Dispatcher 回填 Tool Result，没有 Tool Call 就触发 Stop 并提取 Memory。

**Tool：** ToolDefinition 管 Schema，Registry 管静态/动态定义和 Scope，Dispatcher 管运行时路由、后台分流和错误封装。

**安全：** Filesystem 有真实路径 containment；Shell 没有沙盒，只靠 Hook 的基础拒绝与人工审批。

**Context：** 先减少 Tool Output，再计算含 Tool Schema 的 Token；超限前保存 Transcript，再将历史替换成指令加摘要。

**Memory：** 调用前用最近用户消息筛选，Stop 后从本轮对话提取，达到十条后整理并原子替换 Memory 目录。

**Skill：** System Prompt 只暴露名称和描述，模型命中后调用 `load_skill` 获取全文。

**Background：** Shell 在线程执行，结果进入内存 Ready Queue，并在后续 Agent Loop 开头作为 user notification 注入。

**Cron：** 到期时先持久化 Pending 再入队；首次模型调用前失败就恢复，模型已返回过则确认，避免重复副作用。

**Task：** RLock + fcntl 在一个临界区完成依赖检查、Owner 检查和状态保存，保证竞争领取只有一个成功者。

**Lease：** Claim 时绑定 task_id 与 cwd；Complete 后保留到模型轮次边界，防止该轮剩余工具切换目录。

**Subagent：** 独立 Messages 的同步受限子循环，不是并行 Worker。

**Agent Team：** 每个 Teammate 有长期线程、独立 Messages、JSONL Mailbox、Task Lease 和带版本校验的 Plan/Shutdown 协议。

**Worktree：** Git Checkout 隔离依赖 Task Binding、Registry 再校验和 Lease CWD，不等于安全沙盒。

**MCP：** 启动发现 Tool 并动态注册，每次调用新建连接；有 Local→Remote Fallback，但没有失败重试。

**Goal：** 当前只有状态骨架，Worker/Evaluator Loop 尚未落地。

**Trace：** Writer 能保证单文件事件顺序，但实际埋点只覆盖少数入口，尚非完整链路观测。

## 21. 建议的简历改写

以下版本与当前代码更一致：

> **上下文与长期记忆：** 实现大型 Tool Output 落盘、当前 Tool Batch 预算、旧 Tool Result 微压缩，以及带 Transcript 的 LLM 摘要压缩；通过“相关性筛选、对话提取、阈值整理”三阶段 Markdown Memory 支持跨会话复用。

> **Multi-Agent 协作：** 实现同步短生命周期 Subagent，以及基于 Thread、独立 Messages 和 JSONL Mailbox 的长期 Agent Team；通过持久化 Task、Assignment Lease 与 Git Worktree 为 Teammate 路由独立工作目录。

> **后台任务与定时调度：** 支持在线程中执行长时间 Shell，并在后续 Agent Loop 注入完成结果；实现五段 Cron、原子持久化、Pending Delivery 恢复和共享 Session 串行投递。

> **执行内核与安全控制：** 基于中央 Tool Registry 和生命周期 Hook 实现可扩展 `model → tool → result` 循环，支持动态 Tool 注册、Subagent Scope、Filesystem 路径边界和基础 allow/deny/ask 审批行为。

> **任务规划与状态管理：** 使用独立 JSON Task 表达依赖关系，通过进程内 RLock、`fcntl` 文件锁、原子文件替换、依赖检查和 Owner Lease 实现并发安全的任务认领与完成。

> **MCP 扩展：** 实现 Streamable HTTP MCP Client，支持 `tools/list`、工具 Namespace、动态 Registry 注册、本地到远程 Endpoint Fallback，以及每次 Tool 调用重新建立连接；认证远程地址强制 HTTPS。

Goal Loop 暂时不要作为已完成项目经历。待真正实现后再加入：Worker/Evaluator 分离、Stop Hook 独立验收、自动续跑、预算上限和持久化恢复。

## 22. 最容易被追问的风险点

1. “四级”是否是代码中的明确抽象？不是，目前是四种处理机制的归纳。
2. Memory 如何保证不污染？当前不能完全保证，只能说明格式验证、原子替换和现有 Prompt；真实性仍待增强。
3. Agent Team 是否进程级隔离？不是，是同一 Python 进程内的 daemon Threads。
4. Subagent 是否并行？不是，同步阻塞主调用栈。
5. Cron 是否 Exactly Once？不是；首次 Provider 返回后确认，以避免副作用重放。
6. Background 完成是否立即唤醒？不是，只在后续 Loop 迭代或新 Turn 注入。
7. MCP 是否支持断线自动重试？不是；每次调用新连接，但单次失败不重试。
8. Permission 是否安全沙盒？不是；Filesystem 有路径边界，Shell 仍是本机 `shell=True`。
9. Task Lease 是否持久化？不是；Task 持久化，Lease 在内存。
10. Goal Loop 是否完成？没有，目前只有骨架。

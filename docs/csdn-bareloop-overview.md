# 最近，我做了一件大事：BareLoop

> 项目地址：[https://github.com/OpenLucasKaka/bareloop](https://github.com/OpenLucasKaka/bareloop)
>
> 当前版本：`0.1.0`（Pre-Alpha）
>
> 技术栈：Python 3.12+、OpenAI SDK、MCP、prompt_toolkit

## 好久没更新了，因为我一直在捣鼓这件事

最近确实有一段时间没有更新文章了。

不是消失了，也不是没东西可写，而是这段时间我几乎把空闲时间都投入到了一个新项目里：**BareLoop**。

简单来说，BareLoop 是一个从基本原理出发实现的、兼容 OpenAI API 的 Coding Agent Runtime。它现在还不是什么成熟框架，也远没有到“开箱即用解决一切”的程度，但它已经跑通了一个 Agent 从接收任务、调用模型、选择工具、执行工具、回传结果，到管理上下文、记忆、任务和多 Agent 协作的核心链路。

这篇文章是 BareLoop 系列的第一篇。我不会在这里钻进某个模块的所有实现细节，而是先把整个项目的设计思路、运行流程和模块边界讲清楚。后面我会继续更新文章，逐个分析 Agent Loop、Tool、Memory、Context、MCP、Task、Agent Team、Worktree、Cron 和 Trace 等模块。

## 阅读前先叠个甲：为什么要了解 Agent 的底层原理？

现在做 AI Agent，市面上已经有很多优秀框架。它们封装完善、功能丰富，用几行代码就能接模型、挂工具、建工作流，确实能大幅提高开发效率。

但封装程度越高，另一个问题也越明显：**你很容易只知道 Agent 能跑，却不知道它到底为什么能跑。**

模型在什么时候决定调用工具？Tool Schema 是怎么传给模型的？工具结果为什么还要重新塞回上下文？一次任务为什么会循环调用十几次模型？上下文快满时删掉了什么？长期记忆究竟是在检索事实，还是又让模型猜了一遍？子 Agent 如何领取任务？多个 Agent 同时修改代码时怎样避免互相覆盖？定时任务又如何重新进入同一段会话？

当框架把这些逻辑全部藏起来以后，正常场景下当然很舒服；可一旦遇到模型重复调用工具、上下文突然失忆、权限边界失效、任务状态混乱、并发修改冲突等问题，调试就很容易变成“对着黑盒猜原因”。

所以 BareLoop 不是想证明“现有框架都不好”，也不是为了重新造一个更大的轮子。它更像一个可以拆开看的 Agent Runtime：把那些通常藏在框架内部的关键流程，用尽量直接、清晰、可修改的 Python 代码重新实现一遍。

如果你的目标只是快速上线一个 AI 应用，成熟框架通常仍然是更高效的选择；但如果你想真正理解 Coding Agent 的运行机制，或者准备开发自己的 Agent、工作流引擎和工具系统，那么亲手看懂一次完整的 `model → tool → result` 闭环，我认为非常值得。

## BareLoop 到底是什么？

BareLoop 的核心定位可以概括成一句话：

> 一个流程透明、Host 可控、兼容 OpenAI API 的轻量级 Coding Agent Runtime。

这里有三个关键词。

第一个是**流程透明**。BareLoop 没有把最关键的编排逻辑分散到层层抽象中，核心循环集中在 `loop.py`。从消息进入，到模型返回 Tool Call，再到工具执行结果进入下一轮模型调用，整条链路都可以直接读到。

第二个是**Host 可控**。模型负责推理和选择动作，但工具注册、参数校验、权限 Hook、工作目录、上下文预算、记忆持久化、后台任务和多 Agent 协作，都由本地 Runtime 控制。换句话说，模型可以提出要做什么，但真正允许它做什么、在哪里做、如何记录，决定权仍然在 Host 手里。

第三个是**OpenAI-compatible**。模型调用基于 OpenAI SDK，只要服务提供兼容接口，就可以通过 `BASE_URL` 和 `PRIMARY_MODEL` 接入，不把整个 Runtime 绑定到某一家模型服务。

## 从一条用户消息开始，看懂完整 Agent Loop

理解 BareLoop 最好的方式，不是先背模块列表，而是跟着一条用户消息走完它的生命周期。

它的核心逻辑可以简化成下面这段伪代码：

```python
while True:
    memories = load_relevant_memories(messages)
    messages = control_tool_output_budget(messages)
    messages = compact_if_needed(messages)

    response = model.chat(
        messages=messages,
        tools=current_tool_schemas,
    )

    if not response.tool_calls:
        save_durable_memories_in_background()
        return response.content

    for tool_call in response.tool_calls:
        check_permission(tool_call)
        result = dispatch_tool(tool_call)
        messages.append(result)
```

代码看起来不长，但真正的 Agent Runtime 就藏在每一步的边界处理里。

### 1. Runtime 启动：先准备 Agent 的运行环境

BareLoop 启动时，会先完成几件事：注册生命周期 Hooks、发现 MCP Tools、扫描本地 Skills、创建 Trace Writer、初始化共享消息 Session，并启动 Cron Scheduler。

这一步不是在“问模型问题”，而是在搭建模型即将运行的 Host 环境。模型能看到哪些工具、哪些操作需要权限确认、Skill 从哪里加载、定时任务如何进入会话，都是在这里决定的。

BareLoop 当前的 CLI 使用 `prompt_toolkit` 实现，并提供普通模式和 Goal 模式的切换入口；完整的 Goal 生命周期仍在开发中。用户输入、Teammate 发给 Lead 的邮箱事件，以及到期的 Cron Prompt，最终都会进入同一个 Runtime Session。

### 2. 消息进入：它不只是一个字符串

用户输入会先经过 `PreUserPromptInput` Hook，然后以 `user` 消息加入会话历史。与此同时，Trace 模块会记录已经接入的用户事件和 Runtime 事件。

这里有一个很重要的认识：Agent 并不是每次只把“当前问题”发给模型。真正送入模型的内容通常包含 System Prompt、历史消息、之前的 Tool Call、Tool Result、后台任务通知、定时任务消息以及检索到的记忆。

所以一个 Agent 是否稳定，很大程度上取决于 Runtime 能不能管理好这份不断增长的消息列表。

### 3. Memory：在调用模型前，先找回可能有用的信息

每个新回合开始时，BareLoop 会检索与当前对话相关的持久化记忆，并将结果临时注入最近一条用户消息，再交给主模型处理。

当前 Memory 使用 Markdown 文件持久化，并将记忆区分为 `user`、`feedback`、`project` 和 `reference` 等类型。记忆写入不是把所有对话无脑保存下来，而是通过结构化 Schema 约束模型，只允许提取有持久价值、能够引用对话证据的内容，并限制单次产生的记忆数量。

在主回复完成后，Memory 提取和合并会放到单独的线程池中执行，避免记忆维护阻塞用户看到最终答案。

这部分目前仍然是实验性实现。相关性选择现在仍依赖 LLM，未来还会继续优化成本、召回速度、去重和本地检索能力。

### 4. Context：不能让上下文无限增长

Agent 会不断调用工具，而工具输出可能非常大。一次测试日志、一个长文件或者一段命令结果，都可能迅速吃掉上下文窗口。

BareLoop 目前设计了三层处理：首先限制当前一组 Tool Result 的总预算；对于超出阈值的大结果，将完整内容保存到 `.bareloop/.task_outputs/tool-results/`，只把路径和预览留在上下文里；较早的工具结果会做 Micro Compact；当整体 Token 超过限制时，再把完整 Transcript 落盘并通过 LLM 生成可继续工作的摘要。

这里的目标不是简单“截断字符串”，而是在控制 Token 的同时，尽量保留当前目标、关键结论、已读写文件、用户约束和剩余工作。

### 5. Model：负责推理，但不直接执行

准备好 Messages 和 Tool Schemas 后，BareLoop 才会真正调用模型：

```python
response = client.chat.completions.create(
    model=PRIMARY_MODEL,
    messages=request_messages,
    tools=tool_schemas,
)
```

模型的返回结果有两种：一种是普通文本，代表它认为当前回合可以结束；另一种是 Tool Call，代表它希望 Runtime 帮它执行某个动作。

需要特别注意：**Tool Call 只是模型生成的一段结构化请求，并不等于工具已经执行。**真正的执行权仍然掌握在 Runtime 手中。

### 6. Tool：从 Schema 到 Dispatcher

BareLoop 将内置工具集中注册为 `ToolDefinition`，其中包含工具名称、描述、JSON Schema、处理函数、是否允许子 Agent 使用，以及是否支持后台执行等信息。

每次调用模型前，Runtime 都会从中央 Registry 获取当前 Tool Schema。这意味着 MCP 等外部工具被动态发现并注册以后，不需要重启整套工具系统，下一轮模型请求就能看到更新后的工具列表。

当模型返回 Tool Call 后，Runtime 会先统一解析名称和参数，再经过 `PreToolUse` Hook，最后交给 Dispatcher。Dispatcher 负责检查工具是否存在、当前 Scope 是否允许调用、是否需要转为后台任务，并把异常统一转换成模型可以理解的文本结果。

目前内置能力包括文件读取、写入、编辑和 Glob 查找，Shell 命令执行，Task 管理，Skill 加载，Subagent，以及 Teammate 协作等。文件工具会解析真实路径并检查是否逃逸工作目录，Shell 和工作区外文件访问则会进入权限控制流程。

### 7. Result：工具执行完，为什么还要再调用一次模型？

这是很多人第一次接触 Agent Loop 时容易忽略的一点。

工具执行以后，Runtime 不会直接把原始结果当成最终答案，而是将它追加为一条带有 `tool_call_id` 的 `tool` 消息，再进入下一轮模型调用。模型需要结合原始任务、自己刚才的 Tool Call 和工具返回结果，判断下一步应该继续调用工具，还是整理成最终回复。

因此，一次 Agent 任务通常不是“调用一次模型”，而是下面这个循环：

```text
用户问题
   ↓
模型判断
   ↓
Tool Call → 权限检查 → 工具执行
   ↑                       ↓
   └──── 带着 Tool Result 再次调用模型
                           ↓
                        最终回答
```

只要模型仍然返回 Tool Call，这个闭环就会继续。直到模型返回不带 Tool Call 的普通消息，Runtime 才认为本轮任务已经得到最终结果。

### 8. 后台任务：长命令不应该卡住整个 Agent

对于耗时较长的 Shell 命令，BareLoop 支持将任务放到后台线程执行。Dispatcher 会立即返回一个后台任务 ID，让 Agent 可以继续处理其他工作。

后台命令完成后，结果不会凭空丢失。Runtime 会在后续循环中收集状态和输出，并以新的用户事件重新注入 Messages。这样模型能够继续根据后台任务的成功或失败做出判断。

## 一个 Agent 不够时：Task、Teammate 与 Worktree

单 Agent Loop 解决的是“一个模型怎样持续使用工具”，但复杂 Coding 任务还会遇到另一个问题：怎样拆分任务并让多个 Agent 安全协作？

BareLoop 把这部分拆成了三个互相配合、但职责不同的模块。

**Task System** 负责记录任务本身。Task 会持久化为 JSON，包含 `pending`、`in_process`、`completed` 状态，还可以通过 `blocked_by` 表达依赖关系。任务领取使用线程锁和 `fcntl` 文件锁保护，并绑定 Owner，避免同一任务被多个执行者重复领取。

**Agent Team** 负责协作协议。Lead 可以创建 Teammate，Teammate 之间通过 JSONL Mailbox 传递消息，并支持 Plan Request、Plan Review、Shutdown 等流程。它不是简单地并发调用几次模型，而是尝试为长生命周期的协作 Agent 建立明确的身份和协议状态。

**Worktree** 负责代码隔离。Task 可以绑定独立的 Git Worktree，Agent 领取任务以后，Shell 操作会路由到对应目录。Worktree 创建过程会校验名称、Git 注册信息和任务绑定关系，失败时执行回滚，尽量避免多个 Agent 同时在一个工作目录中互相覆盖修改。

它们的关系可以概括为：Task 告诉 Agent“要做什么”，Teammate 决定“谁来做、如何沟通”，Worktree 保证“在哪里做、怎样隔离”。

## MCP：把外部工具接进同一个循环

BareLoop 内置了 MCP Client，并使用 Streamable HTTP 与 MCP Server 通信。Runtime 启动时会连接端点、完成初始化握手、调用 `tools/list` 发现工具，然后把外部 Tool Schema 转换成内部 `ToolDefinition`，动态注册到中央 Tool Registry。

为了避免不同 Server 的工具重名，暴露给模型的名称会带上命名空间，例如：

```text
mcp__demo__add
mcp__demo__current_time
```

目前项目还提供了一个简单的 MCP Demo Server，包含加法和获取当前时间两个工具。Runtime 会优先尝试本地地址，再尝试配置的远程地址；如果 MCP 不可用，则降级启动，不会因此阻塞整个 Agent。带 Token 的远程 MCP 地址还会强制要求 HTTPS，本地 Loopback 地址除外。

## Cron：让 Prompt 在未来重新进入 Agent

Cron 模块实现了五段 Cron 表达式校验、任务持久化、到期扫描、队列投递以及确认与重试处理。

这里我没有把定时任务做成完全独立的脚本执行器，而是让到期 Prompt 重新进入共享 Session。这样定时任务可以继续使用原有上下文、工具和 Runtime 能力。

为了避免任务在异常中丢失，BareLoop 会跟踪投递状态：模型接受任务后再确认；如果主循环在接受前失败，则从 Messages 中移除临时注入的消息并把任务放回队列。这个模块目前仍是实验性能力，后续还会继续完善生命周期管理和边界场景。

## Hooks 与 Trace：让行为可控制，也可追踪

Hooks 是 Runtime 的行为切入点。BareLoop 当前定义了 `PreUserPromptInput`、`PreToolUse`、`PostToolUse`、`StopGoalGate` 和 `Stop` 等事件，其中已经接入的权限检查会在 Tool 真正执行前运行。

Trace 则把已接入事件按顺序写入本地 JSONL 文件。每条记录带有 `trace_id`、递增 `sequence`、本地时区时间、事件类型和数据。它现在还不是完整的可观测平台，但已经为后续分析一次 Agent 到底经历了什么、在哪一步出错，建立了基础数据层。

## Skills：不把所有知识都塞进 System Prompt

BareLoop 会扫描 `.bareloop/skills/` 下的 `SKILL.md`，读取名称、描述和完整内容。启动时只把可用 Skill 的摘要告诉模型；当任务真的匹配某个 Skill 时，再通过 `load_skill` 加载全文。

这种按需加载的方式可以减少 System Prompt 的体积，也让特定领域的工作协议、操作流程和知识说明能够独立维护。

## 当前模块全景

为了方便快速了解，下面是 BareLoop `0.1.0` 的模块状态：

| 模块 | 当前状态 | 主要职责 |
| --- | --- | --- |
| Agent Loop | 已实现 | 驱动 `model → tool → result` 多轮循环 |
| Tools | 已实现 | Schema、注册表、Scope、动态工具与统一调度 |
| Workspace | 已实现 | 约束文件访问范围，阻止路径逃逸 |
| Task System | 已实现 | 任务依赖、持久化、原子领取和 Owner 绑定 |
| Worktree | 已实现 | Task 与 Git Worktree 绑定及执行目录路由 |
| MCP | 已实现 | 工具发现、命名空间、调用和本地/远程降级 |
| Trace | 已实现 | 线程安全、有序的 JSONL 事件记录 |
| Hooks | 实验性 | 权限和生命周期回调 |
| Context | 实验性 | Tool 预算、结果落盘、微压缩和摘要压缩 |
| Memory | 实验性 | Markdown 记忆、相关性选择、提取和合并 |
| Background | 实验性 | 后台执行 Shell，并在后续循环注入结果 |
| Agent Team | 实验性 | Teammate、Mailbox、Plan Review 和关闭协议 |
| Skills | 实验性 | 本地 Skill 发现与按需加载 |
| Cron | 实验性 | 持久化定时 Prompt、投递确认和失败恢复 |
| Goal/Session | 开发中 | 已有状态模型，完整生命周期仍在完善 |

## 如何运行 BareLoop？

BareLoop 当前要求 Python 3.12+ 和 `uv`。可以按下面的方式启动：

```bash
git clone https://github.com/OpenLucasKaka/bareloop.git
cd bareloop
uv sync
cp .env.example .env
```

然后在 `.env` 中填写兼容 OpenAI API 的模型配置：

```dotenv
API_KEY=your-api-key
BASE_URL=https://your-openai-compatible-endpoint/v1
PRIMARY_MODEL=your-chat-model
TOKENIZER_MODEL=your-transformers-tokenizer
```

启动 CLI：

```bash
uv run python -m bareloop.mian
```

如果想验证项目本身，也可以运行：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

## 这个项目适合谁？

如果你只是希望快速做一个业务 Demo，BareLoop 目前未必适合你，成熟框架会更加省时。

但如果你正在学习 LLM Agent、想弄懂 Tool Calling 和上下文管理、准备自己实现 Agent Runtime，或者对多 Agent 协作、MCP、Memory、Worktree 隔离这些问题感兴趣，那么 BareLoop 可以作为一个体量相对可控、流程可以追下去的学习项目。

代码目前仍在持续迭代，我也会不断修复问题、调整模块边界、补充测试和完善文档。`0.1.0` 仍处于 Pre-Alpha 阶段，API 和本地持久化格式都可能变化，不建议直接用于生产环境。

## 接下来会写什么？

这篇文章只是项目总览。后续我会沿着 BareLoop 的真实源码，继续拆解这些问题：Agent Loop 为什么必须循环；Tool Schema 如何注册、校验与调度；上下文超限时怎样压缩而不丢目标；Memory 如何判断什么值得长期保存；Task、Teammate 和 Worktree 怎样共同支撑多 Agent 协作；MCP 工具如何动态接入；Cron 如何保证消息投递不丢失；Trace 又如何帮助我们还原一次 Agent 的完整执行过程。

如果你也在学习 AI Agent，欢迎来看看源码、运行 Demo、提出 Issue，或者一起讨论设计。这个项目还在成长，我也会把实现过程中的思考、踩坑和改进持续记录下来。

项目地址：[https://github.com/OpenLucasKaka/bareloop](https://github.com/OpenLucasKaka/bareloop)

如果 BareLoop 对你理解 Agent 底层原理有一点帮助，也欢迎点一个 Star。我们后续的模块拆解文章见。

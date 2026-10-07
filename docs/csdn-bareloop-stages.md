# BareLoop 分阶段拆解：从消息到多 Agent 协作，一条主线走完

上一篇文章《最近，我做了一件大事：BareLoop》是对 BareLoop 的整体速览，把设计思路、模块全景和大致运行流程串了一遍。这篇开始正式分阶段拆解。

这个项目功能很多，如果一次性全部铺开，很容易陷入"每个模块都讲一遍，但什么都没讲透"的状态。所以我换了一种方式：把 BareLoop 拆成六个阶段，一条主线走到底，每进入一个新阶段，都是在之前的基础上增加一块能力。

六个阶段分别是：

1. 基础运行时：Agent Loop 与上下文管理
2. 工具系统：Schema、权限与 Workspace 边界
3. 记忆与持久化
4. 任务、工作树与异步执行
5. 多 Agent 协作
6. 计划与外设：Goal、Cron 与未来

下面逐一展开。

## 前置阅读与代码入口

整个项目我们从主入口 `mian.py`（没错，这是原始命名，后续会改名）开始，然后顺着 `loop.py` 往下走。

- 入口：`mian.py` —— CLI 入口，负责初始化配置、加载环境变量、实例化 Agent Runtime
- 核心循环：`loop.py` —— 也就是 `model → tool → result` 的迭代过程
- 会话与消息：`session.py` —— 消息组织与共享会话逻辑
- 生命周期钩子：`hook/hook.py` —— 各类事件回调的挂载点

好，进入正题。

### 阶段一：Agent Loop（核心循环）

核心循环是整个项目里最重要的一段代码，理解了它，后续所有阶段本质上都只是往这个循环里挂功能。

BareLoop 的核心逻辑可以简化为下面这段伪代码：

```python
while True:
    memories = load_relevant_memories(messages)     # 检索相关记忆
    messages = apply_budget_and_compact(messages)   # 工具输出预算 + 微压缩

    response = model.chat(
        messages=messages,
        tools=get_current_tool_schemas(),
    )

    if no tool call:
        save_durable_memories_in_background()
        return response.content

    for call in response.tool_calls:
        check_permission(call)                       # 权限钩子（PreToolUse）
        result = dispatch_tool(call)                 # 工具分发
        messages.append(tool_result(call, result))   # 结果回填上下文

    messages.append(response_as_assistant_message())
```

这个循环看起来很短，但真正的复杂度全部藏在每一行的边界处理里。

#### 1.1 消息进入与循环条件

一个回合从用户消息（或会话里的其他事件）进入 `messages` 开始。模型基于消息列表和当前可用的工具 Schema 做一次推理。如果它返回的是可执行的动作，就进入工具调用分支；如果它返回的不是 Tool Call，就把最终回复格式化后返回。

注意："没有 Tool Call"是一个非常关键的退出条件。在 Coding Agent 里，模型往往先调用工具（搜代码、读文件、执行命令），再基于工具结果继续推理，因此一个回合往往包含多轮模型调用。**只有连续多轮都没有新的工具请求，回合才真正结束。**

#### 1.2 为什么工具结果要重新塞回上下文

大模型本身没有"记忆"能记住上一步工具的执行结果，除非你把它塞回会话上下文。所以工具被调用后，结果会被格式化成 `tool` 角色的消息追加到 `messages`，再作为下一轮模型调用的输入。这一步正是 Agent 从"只能聊天"变成"能动手做事"的分水岭。

#### 1.3 上下文预算与压缩

上下文不是无限的。`compact/index.py` 里定义了 `CONTEXT_LIMIT` 常量，控制消息总长度。每轮调用前会做两件事：

- **Tool 输出预算**：工具结果往往很大（比如读整个文件），BareLoop 对超大结果做预算控制，超过阈值先落盘保存，上下文里只留摘要和路径。
- **微压缩（micro_compact）**：消息接近上限时，删除历史消息，只保留最近 N 条和关键摘要，牺牲旧细节保住目标。

如果这两步还不够，会触发 `compact_history` 做更彻底的压缩，后续还可以接入 LLM summary。

**阶段一验收**：读完 `loop.py`，能画出消息从"进入"到"返回"的完整生命周期；能一口气说清楚"为什么 Agent 回合是多轮模型调用"。

### 阶段二：工具系统（Tools）

第二阶段，把模型从"只能聊天"变成"能动手"的关键模块拆开。

#### 2.1 工具注册与 Schema

工具不是硬编码在模型调用里的，而是通过注册机制管理。每个工具用 JSON Schema 描述输入参数，注册到全局注册表中；模型每轮调用前都会拿到"当前可用工具列表"的 Schema。

代码分布在：

- `tools/registry.py`：Schema 注册表、动态注册
- `tools/dispatcher.py`：按工具名分发到具体执行函数
- `tools/models.py`：工具描述与参数的模型定义

#### 2.2 Workspace 边界

工具执行必须受 Host 控制。BareLoop 用 Workspace 约束文件系统路径必须在当前工作目录内，防止模型读写到无关路径。对应 `tools/filesystem.py`（路径校验）和 `tools/shell.py`（执行命令时的目录/权限约束）。

#### 2.3 权限钩子

工具真正执行前，`hook/hook.py` 里的 `PreToolUse` 钩子做权限检查。你可以在这里控制某类工具是否允许执行、参数是否需人工确认。BareLoop 目前定义了 `PreUserPromptInput`、`PreToolUse`、`PostToolUse`、`Stop` 等钩子点位，目前整体还在实验阶段，权限策略后续会做成可配置。

**阶段二验收**：读一遍 `tools/` 下的代码，能在项目里注册一个新的自定义工具，并通过 Schema 让它被模型正确调用。

### 阶段三：记忆与持久化（Memory）

第三阶段，给 Agent 加上"记性"。

模型本身是无状态的，所以 BareLoop 提供 Markdown 记忆机制：

- **提取（Extract）**：每轮回合结束后，从对话中提取值得长期保存的事项，写成 Markdown 笔记。
- **合并（Consolidate）**：定期把新笔记与既有记忆合并、去重，避免碎片化。
- **检索（Load）**：每轮开始前，根据相关性检索已有记忆，塞回上下文，让模型知道"之前发生了什么"。

关键文件：

- `memory/index.py`：记忆的存储、检索与生命周期管理
- `memory/schema.py`：记忆条目的 Schema 定义
- `memory/prompt_version.py`：提取与合并的提示词版本管理

值得一提的设计：记忆不直接塞进 system prompt，而是按相关性筛选后注入会话上下文，从而减少对上下文窗口的占用。每次回合结束时，提取与合并放到后台线程异步执行（`loop.py` 里有 `_maintain_memories`），避免阻塞主循环。

**阶段三验收**：理解"提取 → 检索 → 合并"三段式记忆链路，能在自己的 Agent 里复现一个简化版。

### 阶段四：任务、工作树与异步执行

第四阶段，把"工具调用"升级为"结构化任务"。

单个工具调用是原子的，而真实开发任务是复合的：要执行命令、读文件、改代码、再验证。BareLoop 引入三类结构化能力：

- **Task System（`task_system/`）**：任务依赖（先 A 后 B）、状态机与原子领取（并发下不会重复领取同一任务）、Owner 绑定（任务归属于哪个 Agent/工作树）。
- **Worktree（`worktree/index.py`）**：任务与 Git Worktree 绑定，每个任务在独立工作树中执行，避免多个 Agent 同时改同一目录造成冲突；执行目录也由任务的工作树决定，防止模型逃出授权范围。
- **Background（`background_system/index.py`）**：支持后台执行 Shell 和长任务，结果注入后续循环，Agent 不必每步都同步等待。

**阶段四验收**：跑通一条"创建任务 → 领取任务 → 在独立工作树执行 → 结果回填"的完整链路，并解释并发场景下如何保证任务不被重复执行。

### 阶段五：多 Agent 协作

第五阶段，从单个 Agent 走向一个"团队"。

`agent_team/` 实现了多 Agent 协作的基础能力：

- **Teammate**：代表一个独立的子 Agent，拥有自己的上下文和工具集。
- **Mailbox（邮箱）**：Agent 之间通过 mailbox 收发消息，异步解耦，不必互相阻塞。
- **Plan Review**：请其他 Agent 对计划做评审，形成多角色协作闭环。
- **Shutdown Protocol**：团队关闭时先通知所有成员再退出，保证消息不丢失。

与之配套的还有 `subagent/index.py` 的子 Agent 执行机制。`tools/adapters/` 下有对应的 tool adapter（`teamate.py`、`subagent.py` 等），把它们暴露成 Agent 可以调用的"工具"。

**阶段五验收**：用 Agent Team 复现一个最简单的"Leader 下发任务 → Teammate 领取 → 结果回传"协作场景。

### 阶段六：计划与外设（Goal / Cron / 未来）

最后一个阶段，把 BareLoop 从"一次会话语境"扩展到"有时间概念的主动系统"。

- **Goal / Session（`goal/`、`session.py`）**：Session 表示一次运行，Goal 表示一个目标；目标允许跨会话继续执行。当前状态模型存在，但公开的会话生命周期尚未完整打通。
- **Cron Scheduler（`cron_scheduler/`）**：实现五字段 Cron 表达式解析、持久化定时任务、队列投递、消息确认（ack）与失败重试。目前仍是实验性能力，但已经打通"定时 → 队列 → 会话 → 执行 → 回执"这条链路。

这是目前 BareLoop 中最薄、也最有想象空间的部分——它把 Agent 从"被动等待输入"变成了"到点主动醒来执行"。

**阶段六验收**：理解 Goal / Session / Cron 的边界，并设计一个"跨会话的长期目标执行"方案。

## 学习路线建议

如果你想在自己项目里复现类似的东西，建议严格按阶段顺序走：

1. 先数清楚 Agent 循环的退出条件
2. 再接工具系统，体会"模型说要做什么 + Host 决定能不能做/在哪做"的分工
3. 最后再碰任务、多 Agent 和计划

每个阶段都有独立验收标准，跑通验收再进下一阶段，比一口气读完所有源码要稳得多。

## 该系列后续计划

后续计划按这个顺序持续输出每部分的单独拆解：

1. Agent Loop 为什么必须循环
2. Tool Schema 的注册、校验与调度细节
3. 上下文预算与微压缩的实现
4. Memory 如何判断什么值得长期保存
5. Task / Worktree / Background 如何组成结构化执行
6. Teammate + Mailbox 如何做到异步协作
7. Cron + Goal 如何把 Agent 变成"有计划的系统"

如果你也在学 AI Agent，欢迎来看源码、跑 Demo、提 Issue，或一起讨论设计。这个项目还在持续打磨，每修一个问题我都会把思考过程写下来。

如果 BareLoop 对你理解 Agent 底层原理有帮助，也欢迎给项目一个 Star。

项目地址：[https://github.com/OpenLucasKaka/bareloop](https://github.com/OpenLucasKaka/bareloop)

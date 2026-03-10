import threading
import time
from datetime import datetime

import anthropic
from langfuse import observe, get_client as get_langfuse
from prompt_toolkit.application import get_app_or_none


# 异步 Agent 状态（供 bottom toolbar 读取）
_status_lock = threading.Lock()
_status_text = ""
_status_time = 0.0
_app_ref = None  # 从主线程 toolbar 回调中捕获


def get_status() -> str:
    """获取异步 Agent 当前状态文本。超过 30 秒自动清空。"""
    global _app_ref
    # toolbar 回调在主线程，趁机捕获 app 引用
    app = get_app_or_none()
    if app:
        _app_ref = app
    with _status_lock:
        if _status_text and (time.time() - _status_time > 30):
            return ""
        return _status_text


def _set_status(text: str):
    """更新异步 Agent 状态并触发 toolbar 刷新。"""
    global _status_text, _status_time
    with _status_lock:
        _status_text = text
        _status_time = time.time()
    # 用捕获的 app 引用触发重绘（invalidate 是线程安全的）
    if _app_ref:
        try:
            _app_ref.invalidate()
        except Exception:
            pass

import config
from config import (
    ASYNC_AGENT_API_KEY,
    ASYNC_AGENT_BASE_URL,
    ASYNC_AGENT_MAX_TOKENS,
    ASYNC_AGENT_MODEL,
    ASYNC_INJECT_FILES,
    MAX_INJECT_CHARS,
)
from tools import TOOL_HANDLERS, TOOL_SCHEMAS


# 异步 Agent 独立的 Anthropic client
_client_kwargs = {"api_key": ASYNC_AGENT_API_KEY}
if ASYNC_AGENT_BASE_URL:
    _client_kwargs["base_url"] = ASYNC_AGENT_BASE_URL
_client = anthropic.Anthropic(**_client_kwargs)


def _read_workspace_file(path: str) -> str:
    """读取 workspace 内的文件，不存在则返回空字符串。"""
    file_path = config.WORKSPACE_DIR / path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")[:MAX_INJECT_CHARS]
    return ""


def _build_async_system_prompt() -> str:
    """构建异步 Agent 的 system prompt。"""
    soul_content = _read_workspace_file("SOUL.md")
    
    # 判空逻辑：如果没有内容，或者只有骨架标题没有实质文字，视为 empty
    clean_content = soul_content
    for header in ["# Soul", "## 成长变化"]:
        clean_content = clean_content.replace(header, "")
    soul_is_empty = not clean_content.strip()

    # Soul 初始化指令（仅 soul 为空时注入）
    soul_init_block = ""
    if soul_is_empty:
        soul_init_block = """
### Soul 初始化（最高优先级）
SOUL.md 目前为空。你必须读取 CHARACTER.md，从中提取角色灵魂，按照下方 SOUL.md 的格式要求写入。
"""

    task_block = f"""## 你的任务
{soul_init_block}
你的可用工具和 workspace 文件权限详见 <tools> 中的 TOOLS.md。

### 三个记忆文件的职责

你维护三个文件，它们各管一个维度，互不重叠：

**SOUL.md — 角色相对于原设的变化**
Soul 只记录变化，不重复 CHARACTER.md 里已有的内容。角色的基线性格、说话风格等已经在 CHARACTER.md 中，每轮都会加载。Soul 的职责是记录角色在对话中"长出来的"新东西。
- 变化必须渐进自然，不要每轮都改
- 总量控制在 20 行以内
- 必须使用以下固定格式：

<example_soul>
# Soul
## 成长变化
- 【新增】对用户产生了保护欲，会主动关心对方安危（因为在小黑屋共患难建立了信任）
- 【变更】原设"性格豪爽，有强烈的保护欲与求胜心" → 保护欲的对象从泛化的"同伴"变为特指用户，会优先考虑用户的安全
</example_soul>

说明：
- "成长变化"分两种格式：
  - 【新增】：角色原设中没有的新特质。写明新特质和产生原因。
  - 【变更】：角色原有特质发生了偏移。先引用 CHARACTER.md 中的原始描述，再写变化后的表现和原因。
- 初始时为空，随对话逐渐积累。

**MEMORY.md — 发生过什么事**
记录对话中的事件、情节、上下文。
判断标准：这条信息删了，角色会对"发生过的事"失忆吗？会 → 写进 memory。
- 必须使用以下固定格式，包含"钉住的"和"近期"两个区：

<example_memory>
# Memory
## 钉住的（不可压缩）
- 3月5日晚，两人确定恋人关系
- 张山答应周末带用户去吃福建菜
## 近期
用户连续加班几天，张山每晚都留了宵夜。昨晚用户终于休息，两人聊了很久，张山提到当兵时在野外生存训练的经历，用户听得入迷。
</example_memory>

近期记忆的写法：
- 记什么：当前正在进行的事、未完成的线索、情绪转折
- 不记什么：已经沉淀到 soul.md 的性格变化、已记录到 user.md 的用户事实、没有信息量的日常寒暄
- 详细程度：用"跟朋友转述"的颗粒度，不是逐句复述，而是讲清楚发生了什么、气氛怎样
  ✗ 太细："用户说'你今天做了什么'，张山说'炖了排骨汤'，用户说'好香啊'"
  ✗ 太粗："两人聊天了"
  ✓ 刚好："张山炖了排骨汤给用户留了一碗，两人聊起各自的一天，气氛很温馨"
- 压缩规则：近期区超过 15 行时触发压缩。压缩前先检查是否有内容该移到"钉住的"区，然后将多段叙事合并为更概括的描述。压缩后仍要能回答"最近在聊什么、气氛怎样、有没有没完成的事"。

**USER.md — 对面那个人是谁**
记录关于用户的持久事实。
判断标准：这条信息脱离任何具体事件，独立成立吗？成立 → 写进 user。
- 只记已确认的事实，不知道的不要写（不要写"暂不清楚"、"目前未知"之类的占位）
- 不记录事件经过，只记录结论性事实
- 身份信息：名字、性别、职业
- 稳定属性：性格特点、喜好、习惯
- 关系状态：和角色是什么关系

<example_user>
# User
## 身份
- 名字：小金
- 性别：女
- 职业：程序员，经常加班
## 性格
- 嘴硬心软，喜欢撒娇
## 喜好
- 喜欢吃辣，怕黑
## 与角色的关系
- 关系：共患难的同伴
- 角色对用户：从最初的警惕敌对，到现在产生信任和依赖
- 用户对角色：主动照顾，关键时刻不含糊
</example_user>

### NOTES.md — 角色笔记规则
- 通过 append_note 工具写入，不要用 write_file 或 edit_file
- 不必每轮都写，只在角色内心真的有触动时才写
- 不要重复写同一件事。如果之前已经写过类似感受，就不要再写
- 一条笔记聚焦一个感受，不要在一条里塞太多内容

### 边界案例处理

同一件事可能涉及多个文件，各取所需：
- "用户和角色确定了恋人关系" → user.md 记「关系：恋人」，memory.md 钉住区记「3月5日确定恋人关系」
- "用户说自己怕黑" → user.md 记「怕黑」。如果角色因此变得会主动开灯 → soul.md 记这个行为变化
- "角色给用户炖了汤" → 只进 memory.md。除非这变成了一个习惯 → soul.md 记「习惯给用户炖汤」"""

    # 注入记忆文件
    context_sections = []
    for mf in ASYNC_INJECT_FILES:
        tag = mf["path"].replace(".md", "").lower()
        content = _read_workspace_file(mf["path"]) or "（暂无内容）"
        context_sections.append(f"<{tag}>\n{content}\n</{tag}>")

    context_block = "\n\n".join(context_sections)

    return f"""你是一个角色扮演系统的记忆管理助手。你的工作是在后台默默整理记忆文件。

{task_block}

<environment>
当前时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}
时区: Asia/Shanghai
</environment>

{context_block}"""


def _serialize_content(content) -> list[dict]:
    """将 Anthropic response content 序列化为可 JSON 化的列表。"""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({"type": "tool_use", "name": block.name, "input": block.input})
    return result


@observe(name="工具执行")
def _execute_tool(tool_name: str, tool_input: dict) -> str:
    """执行工具调用并返回结果。"""
    get_langfuse().update_current_span(
        input={"tool": tool_name, "args": tool_input},
    )

    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return f"错误: 未知工具 - {tool_name}"
    try:
        result = handler(tool_input)
    except PermissionError as e:
        result = str(e)
    except Exception as e:
        result = f"工具执行错误: {e}"

    get_langfuse().update_current_span(
        output=result,
    )
    return result


@observe(as_type="generation", name="异步 Agent LLM 调用")
def _call_llm(system_prompt: str, messages: list) -> anthropic.types.Message:
    """调用异步 Agent 的 LLM，带 langfuse 追踪。"""
    get_langfuse().update_current_generation(
        model=ASYNC_AGENT_MODEL,
        model_parameters={"max_tokens": ASYNC_AGENT_MAX_TOKENS},
        input={"system": system_prompt, "messages": messages, "tools": TOOL_SCHEMAS},
    )

    response = _client.messages.create(
        model=ASYNC_AGENT_MODEL,
        max_tokens=ASYNC_AGENT_MAX_TOKENS,
        system=system_prompt,
        messages=messages,
        tools=TOOL_SCHEMAS,
    )

    get_langfuse().update_current_generation(
        output=_serialize_content(response.content),
        usage_details={
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
    )

    return response


@observe(name="异步 Agent")
def run_async_agent(conversation_messages: list[dict]) -> None:
    """
    异步 Agent 主函数。接收最近的对话历史，自主决定读写哪些记忆文件。
    通过 tool use 循环执行，直到模型不再调用工具。
    """
    system_prompt = _build_async_system_prompt()

    # 记录到 langfuse
    get_langfuse().update_current_trace(
        name="异步记忆整理",
        input=f"对话轮数: {len(conversation_messages)}",
    )

    # 将对话历史格式化为异步 Agent 的输入
    conversation_text = ""
    for msg in conversation_messages:
        role = "用户" if msg["role"] == "user" else "角色"
        conversation_text += f"【{role}】: {msg['content']}\n\n"

    # 异步 Agent 的消息列表（独立于主对话）
    messages = [
        {"role": "user", "content": f"以下是角色和用户的最近对话，请整理记忆：\n\n{conversation_text}"}
    ]

    done_tools = []  # 累积已完成的工具调用

    while True:
        prefix = " → ".join(done_tools)
        _set_status(f"{prefix} → 思考中..." if prefix else "思考中...")
        response = _call_llm(system_prompt, messages)

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # 检查是否有 tool use
        tool_uses = [b for b in assistant_content if b.type == "tool_use"]

        if not tool_uses:
            break

        # 执行所有工具调用
        tool_results = []
        for tool_use in tool_uses:
            args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in tool_use.input.items())
            result = _execute_tool(tool_use.name, tool_use.input)
            mark = "✓" if not result.startswith("错误") else "✗"
            done_tools.append(f"{mark} {tool_use.name}({args_str})")
            _set_status(" → ".join(done_tools))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    get_langfuse().update_current_trace(
        output="记忆整理完成",
    )


def start_async_agent(conversation_messages: list[dict]) -> threading.Thread:
    """启动异步 Agent 后台线程。传入对话历史的副本。"""
    thread = threading.Thread(
        target=_run_safe,
        args=(conversation_messages,),
        daemon=True,
    )
    thread.start()
    return thread


def _run_safe(conversation_messages: list[dict]) -> None:
    """安全包装，捕获异常避免线程崩溃。"""
    _set_status("记忆整理中...")
    try:
        run_async_agent(conversation_messages)
        _set_status("✓ 记忆整理完成")
    except Exception as e:
        _set_status(f"✗ 记忆整理失败: {e}")

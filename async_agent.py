import threading
from datetime import datetime

import anthropic
from langfuse import observe, get_client as get_langfuse

from config import (
    ASYNC_AGENT_API_KEY,
    ASYNC_AGENT_BASE_URL,
    ASYNC_AGENT_MAX_TOKENS,
    ASYNC_AGENT_MODEL,
    MAX_INJECT_CHARS,
    MEMORY_FILES,
    WORKSPACE_DIR,
)
from tools import TOOL_HANDLERS, TOOL_SCHEMAS


# 异步 Agent 独立的 Anthropic client
_client_kwargs = {"api_key": ASYNC_AGENT_API_KEY}
if ASYNC_AGENT_BASE_URL:
    _client_kwargs["base_url"] = ASYNC_AGENT_BASE_URL
_client = anthropic.Anthropic(**_client_kwargs)


def _read_workspace_file(path: str) -> str:
    """读取 workspace 内的文件，不存在则返回空字符串。"""
    file_path = WORKSPACE_DIR / path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")[:MAX_INJECT_CHARS]
    return ""


def _build_async_system_prompt() -> str:
    """构建异步 Agent 的 system prompt。"""

    # 注入记忆文件，让异步 Agent 了解当前角色状态
    context_sections = []
    for mf in MEMORY_FILES:
        if not mf["inject"]:
            continue
        content = _read_workspace_file(mf["path"]) or "（暂无内容）"
        context_sections.append(f"## {mf['path']}\n\n{content}")

    project_context = "\n\n".join(context_sections)

    return f"""你是一个角色扮演系统的记忆管理助手。你的工作是在后台默默整理角色的记忆。

你会收到角色和用户之间的最近对话记录。请以角色的第一人称视角，完成以下任务：

1. **更新对用户的印象**：如果对话中出现了关于用户的新信息（名字、喜好、性格特点等），用 edit_file 或 write_file 更新 USER.md。
2. **写日记**：记录今天和用户之间发生的事、角色的感受和想法，用 write_file 写入或 edit_file 追加到今天的日期文件（如 {datetime.now().strftime("%Y-%m-%d")}.md）。
3. **更新灵魂**：如果角色在对话中有了新的自我认知或成长，用 edit_file 更新 SOUL.md 的相关部分。
4. **长期记忆**：如果有值得长期记住的重要事件或感悟，用 edit_file 追加到 LONG_TERM_MEMORY.md。

## 工具

- **read_file**: 读取文件内容
- **write_file**: 创建或覆盖文件
- **edit_file**: 精确替换文件中的一段文本

## 规则

- 用角色自己的语气写，像写日记一样自然
- 如果对话只是闲聊、没有值得记录的内容，可以什么都不做
- 不要输出任何给用户看的文字，只调用工具
- 先用 read_file 读取要修改的文件，再用 edit_file 精确修改

## 当前时间

{datetime.now().strftime("%Y-%m-%d %H:%M")}

## 角色当前状态

{project_context}"""


def _serialize_content(content) -> list[dict]:
    """将 Anthropic response content 序列化为可 JSON 化的列表。"""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({"type": "tool_use", "name": block.name, "input": block.input})
    return result


@observe(name="异步 LLM 调用", as_type="generation")
def _call_llm(system_prompt: str, messages: list[dict]) -> anthropic.types.Message:
    """异步 Agent 的 LLM 调用，完整记录到 langfuse。"""

    # 序列化 messages（可能包含 Anthropic 对象）
    serialized_messages = []
    for msg in messages:
        if msg["role"] == "assistant" and isinstance(msg.get("content"), list) and not isinstance(msg["content"][0], dict):
            serialized_messages.append({"role": "assistant", "content": _serialize_content(msg["content"])})
        else:
            serialized_messages.append(msg)

    get_langfuse().update_current_generation(
        input={
            "system": system_prompt,
            "messages": serialized_messages,
            "tools": TOOL_SCHEMAS,
        },
        model=ASYNC_AGENT_MODEL,
        model_parameters={"max_tokens": ASYNC_AGENT_MAX_TOKENS},
    )

    response = _client.messages.create(
        model=ASYNC_AGENT_MODEL,
        max_tokens=ASYNC_AGENT_MAX_TOKENS,
        system=system_prompt,
        tools=TOOL_SCHEMAS,
        messages=messages,
    )

    get_langfuse().update_current_generation(
        output=_serialize_content(response.content),
        usage_details={
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
    )

    return response


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

    while True:
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
            result = _execute_tool(tool_use.name, tool_use.input)
            args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in tool_use.input.items())
            status = "✓" if not result.startswith("错误") else "✗"
            print(f"  [异步] {status} {tool_use.name}({args_str})")
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
    try:
        run_async_agent(conversation_messages)
    except Exception as e:
        print(f"  [异步] 记忆整理失败: {e}")

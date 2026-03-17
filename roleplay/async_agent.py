import json
import threading
import time
from datetime import datetime
from pathlib import Path

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

from roleplay import config
from roleplay.config import (
    ASYNC_AGENT_API_KEY,
    ASYNC_AGENT_BASE_URL,
    ASYNC_AGENT_MAX_TOKENS,
    ASYNC_AGENT_MODEL,
    ASYNC_AGENT_SDK,
    ASYNC_INJECT_FILES,
    MAX_INJECT_CHARS,
)
from roleplay.tools import TOOL_HANDLERS, TOOL_SCHEMAS


# ---------- SDK 初始化 ----------

if ASYNC_AGENT_SDK == "anthropic":
    import anthropic
    _client_kwargs = {"api_key": ASYNC_AGENT_API_KEY}
    if ASYNC_AGENT_BASE_URL:
        _client_kwargs["base_url"] = ASYNC_AGENT_BASE_URL
    _client = anthropic.Anthropic(**_client_kwargs)
else:
    from openai import OpenAI
    _client_kwargs = {"api_key": ASYNC_AGENT_API_KEY}
    if ASYNC_AGENT_BASE_URL:
        _client_kwargs["base_url"] = ASYNC_AGENT_BASE_URL
    _client = OpenAI(**_client_kwargs)


# ---------- 工具 schema 转换 ----------

def _to_openai_tools(anthropic_schemas: list) -> list:
    """将 Anthropic 格式的 tool schema 转换为 OpenAI 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_schemas
    ]


_OPENAI_TOOL_SCHEMAS = _to_openai_tools(TOOL_SCHEMAS)


# ---------- 公共工具函数 ----------

def _read_workspace_file(path: str) -> str:
    """读取 workspace 内的文件，不存在则返回空字符串。"""
    file_path = config.WORKSPACE_DIR / path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")[:MAX_INJECT_CHARS]
    return ""


def _extract_last_note(content: str) -> str:
    """从 NOTES.md 内容中提取最后一条笔记的时间和内容。"""
    if not content or not content.strip():
        return "（暂无笔记）"

    # 按 --- 分割，过滤空字符串
    sections = [s.strip() for s in content.split("---") if s.strip()]

    if not sections:
        return "（暂无笔记）"

    # 取最后一条
    last_section = sections[-1]
    lines = last_section.split("\n", 1)

    if len(lines) < 2:
        return last_section

    time_str = lines[0].strip()
    note_content = lines[1].strip()

    return f"上一条笔记时间: {time_str}\n\n上一条笔记内容:\n{note_content}"


def _build_async_system_prompt() -> str:
    """构建异步 Agent 的 system prompt。"""
    soul_is_empty = config.is_soul_empty()

    # 读取 prompt 模板
    agent_md_path = config.WORKSPACE_DIR / "ASYNC_AGENTS.md"
    agent_template = agent_md_path.read_text(encoding="utf-8")

    # Soul 初始化指令（仅 soul 为空时注入）
    soul_init_block = ""
    if soul_is_empty:
        soul_init_block = """### CHARACTER.md 提取（最高优先级）
SOUL.md 目前为空，说明 CHARACTER.md 尚未被提取。你必须将 CHARACTER.md 的全部内容分发到对应文件：
1. 身份、性格、说话风格 → SOUL.md 对应分区
2. 输出格式、行为约束、情绪表达等系统级指令 → SOUL.md 回复规则分区
3. 背景故事、经历 → MEMORY.md 钉住区
4. 关于对方的预设信息（如有）→ USER.md
5. 不属于以上任何类别的内容 → SOUL.md 回复规则分区（兜底）

**重要：提取时必须使用原词原句，直接平移原文内容，不要概括或改写。**
**重要：写入文件时禁止使用"用户"一词，用对方的称呼或"对方"代替。**
提取完成后，CHARACTER.md 将不再注入 prompt，所以务必确保所有信息都已分发，不可遗漏。

"""

    # 注入上一条笔记到模板
    notes_content = _read_workspace_file("NOTES.md")
    last_note = _extract_last_note(notes_content)

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    task_block = agent_template.replace("{{SOUL_INIT_BLOCK}}", soul_init_block)
    task_block = task_block.replace("{{CURRENT_TIME}}", current_time)
    task_block = task_block.replace("{{LAST_NOTE}}", last_note)

    # 注入记忆文件（NOTES.md 已通过模板占位符注入，跳过；CHARACTER.md 提取完成后跳过）
    context_sections = []
    for mf in ASYNC_INJECT_FILES:
        if mf["path"].lower() == "notes.md":
            continue
        if mf["path"].lower() == "character.md" and not soul_is_empty:
            continue
        tag = mf["path"].replace(".md", "").lower()
        content = _read_workspace_file(mf["path"]) or "（暂无内容）"
        context_sections.append(f"<{tag}>\n{content}\n</{tag}>")

    context_block = "\n\n".join(context_sections)

    return f"""{task_block}

<environment>
当前时间: {datetime.now().strftime("%Y-%m-%d %H:%M %A")}
时区: Asia/Shanghai
</environment>

{context_block}"""


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


# ========== Anthropic SDK 路径 ==========

def _serialize_content_anthropic(content) -> list[dict]:
    """将 Anthropic response content 序列化为可 JSON 化的列表。"""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({"type": "tool_use", "name": block.name, "input": block.input})
    return result


@observe(as_type="generation", name="异步 Agent LLM 调用")
def _call_llm_anthropic(system_prompt: str, messages: list):
    """Anthropic SDK 调用。"""
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
        output=_serialize_content_anthropic(response.content),
        usage_details={
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
    )

    return response


def _run_tool_loop_anthropic(system_prompt: str, messages: list, done_tools: list):
    """Anthropic SDK 的工具循环。"""
    while True:
        prefix = " → ".join(done_tools)
        _set_status(f"{prefix} → 思考中..." if prefix else "思考中...")
        response = _call_llm_anthropic(system_prompt, messages)

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


# ========== OpenAI SDK 路径 ==========

def _serialize_content_openai(message) -> list[dict]:
    """将 OpenAI response message 序列化为可 JSON 化的列表。"""
    result = []
    if getattr(message, "reasoning_content", None):
        result.append({"type": "thinking", "text": message.reasoning_content})
    if message.content:
        result.append({"type": "text", "text": message.content})
    if message.tool_calls:
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": tc.function.arguments}
            result.append({
                "type": "tool_use",
                "name": tc.function.name,
                "input": args,
            })
    return result


@observe(as_type="generation", name="异步 Agent LLM 调用")
def _call_llm_openai(system_prompt: str, messages: list):
    """OpenAI SDK 调用，OpenRouter Kimi K2.5。"""
    get_langfuse().update_current_generation(
        model=ASYNC_AGENT_MODEL,
        model_parameters={"max_tokens": ASYNC_AGENT_MAX_TOKENS},
        input={"system": system_prompt, "messages": messages, "tools": _OPENAI_TOOL_SCHEMAS},
    )

    api_messages = [{"role": "system", "content": system_prompt}] + messages

    response = _client.chat.completions.create(
        model=ASYNC_AGENT_MODEL,
        max_tokens=ASYNC_AGENT_MAX_TOKENS,
        messages=api_messages,
        tools=_OPENAI_TOOL_SCHEMAS if TOOL_SCHEMAS else None,
    )

    message = response.choices[0].message

    get_langfuse().update_current_generation(
        output=_serialize_content_openai(message),
        usage_details={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        },
    )

    return response


def _run_tool_loop_openai(system_prompt: str, messages: list, done_tools: list):
    """OpenAI SDK 的工具循环。"""
    _empty_reply_retries = 0  # 模型返回空回复（无工具调用）的连续次数
    _MAX_EMPTY_RETRIES = 2    # 最多重试 2 次

    while True:
        prefix = " → ".join(done_tools)
        _set_status(f"{prefix} → 思考中..." if prefix else "思考中...")
        response = _call_llm_openai(system_prompt, messages)

        message = response.choices[0].message

        # 将 assistant 消息追加到历史（保留 tool_calls 和 reasoning_content）
        assistant_msg = {"role": "assistant", "content": message.content or ""}
        # thinking 模式下必须保留 reasoning_content，否则下一轮调用会报错或生成空参数
        if getattr(message, "reasoning_content", None):
            assistant_msg["reasoning_content"] = message.reasoning_content
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(assistant_msg)

        if not message.tool_calls:
            # 检查是否是工具错误后模型放弃了，尝试提醒继续
            last_tool_had_error = (
                len(messages) >= 2
                and messages[-2].get("role") == "tool"
                and messages[-2].get("content", "").startswith("错误")
            )
            if last_tool_had_error and _empty_reply_retries < _MAX_EMPTY_RETRIES:
                _empty_reply_retries += 1
                messages.append({
                    "role": "user",
                    "content": "上一次工具调用失败了，请重新调用工具并提供完整的参数继续完成任务。",
                })
                continue
            break

        # 执行所有工具调用，每个结果作为单独的 tool message
        for tc in message.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[DEBUG] JSON 解析失败: {e}")
                print(f"[DEBUG] 原始 arguments: {repr(tc.function.arguments[:400])}")
                tool_input = {}

            # 空参数检测：不执行工具，直接返回明确的错误提示
            if not tool_input:
                result = (
                    f"错误: {tc.function.name} 调用参数为空。"
                    f"请重新调用并提供完整参数。"
                )
                done_tools.append(f"✗ {tc.function.name}(空参数)")
            else:
                args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in tool_input.items())
                result = _execute_tool(tc.function.name, tool_input)
                mark = "✓" if not result.startswith("错误") else "✗"
                done_tools.append(f"{mark} {tc.function.name}({args_str})")

            _set_status(" → ".join(done_tools))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })


# ========== 公共入口 ==========

@observe(name="异步 Agent")
def run_async_agent(conversation_messages: list[dict], workspace_dir: Path = None) -> None:
    """
    异步 Agent 主函数。接收最近的对话历史，自主决定读写哪些记忆文件。
    通过 tool use 循环执行，直到模型不再调用工具。

    workspace_dir: 指定工作目录，默认使用 config.WORKSPACE_DIR。
    """
    if workspace_dir is not None:
        config.WORKSPACE_DIR = workspace_dir

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

    if ASYNC_AGENT_SDK == "anthropic":
        _run_tool_loop_anthropic(system_prompt, messages, done_tools)
    else:
        _run_tool_loop_openai(system_prompt, messages, done_tools)

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
        import traceback
        traceback.print_exc()
        _set_status(f"✗ 记忆整理失败: {e}")

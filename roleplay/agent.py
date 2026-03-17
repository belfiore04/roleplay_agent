from datetime import datetime

from openai import OpenAI
from langfuse import observe, get_client as get_langfuse

from roleplay import config
from roleplay.config import (
    CHAT_API_KEY,
    CHAT_BASE_URL,
    CHAT_MAX_TOKENS,
    CHAT_MODEL,
    MAIN_INJECT_FILES,
    MAX_INJECT_CHARS,
)


client = OpenAI(
    api_key=CHAT_API_KEY,
    base_url=CHAT_BASE_URL,
)


def _read_workspace_file(path: str) -> str:
    """读取 workspace 内的文件，不存在则返回空字符串。"""
    file_path = config.WORKSPACE_DIR / path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")[:MAX_INJECT_CHARS]
    return ""


def build_system_prompt() -> str:
    """每轮对话前重新读取记忆文件，构建 system prompt。"""

    # 用 XML tag 分隔注入各记忆文件
    soul_empty = config.is_soul_empty()
    context_sections = []
    for mf in MAIN_INJECT_FILES:
        # CHARACTER.md 提取完成后不再注入
        if mf["path"].lower() == "character.md" and not soul_empty:
            continue
        tag = mf["path"].replace(".md", "").lower()
        content = _read_workspace_file(mf["path"]) or "（暂无内容）"
        context_sections.append(f"<{tag}>\n{content}\n</{tag}>")

    context_block = "\n\n".join(context_sections)

    # 读取主 Agent prompt 模板
    agent_md = _read_workspace_file("AGENTS.md")

    return f"""<environment>
当前时间: {datetime.now().strftime("%Y-%m-%d %H:%M %A")}
时区: Asia/Shanghai
</environment>

{context_block}

{agent_md}"""


@observe(as_type="generation", name="主 Agent LLM 调用")
def _call_llm(system_prompt: str, messages: list) -> dict:
    """调用主 Agent 的 LLM，带 langfuse 追踪。"""
    get_langfuse().update_current_generation(
        model=CHAT_MODEL,
        model_parameters={"max_tokens": CHAT_MAX_TOKENS},
        input={"system": system_prompt, "messages": messages},
    )

    api_messages = [{"role": "system", "content": system_prompt}] + messages

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        max_tokens=CHAT_MAX_TOKENS,
        messages=api_messages,
    )

    reply = response.choices[0].message.content or ""

    get_langfuse().update_current_generation(
        output=reply,
        usage_details={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        },
    )

    return reply


def _is_first_meeting() -> bool:
    """判断是否首次见面：MEMORY.md 内容是否还是初始模板。"""
    memory_content = _read_workspace_file("MEMORY.md")
    template = config.FILE_TEMPLATES.get("MEMORY.md", "")
    return memory_content.strip() == template.strip()


@observe(name="角色主动说话")
def proactive_chat() -> str:
    """角色主动开口说话，不需要用户消息。根据记忆状态区分首次见面和回访。"""
    system_prompt = build_system_prompt()

    if _is_first_meeting():
        system_prompt += "\n\n<task>\n用户刚刚来到你面前，这是你们的第一次见面。你需要主动开口说第一句话。根据你的性格和身份，自然地打招呼或开场。\n</task>"
    else:
        system_prompt += '\n\n<task>\n用户回来了。根据你对他的了解和最近的记忆，主动说一句话。可以是打招呼、接上次的话题、或者根据当前时间/情境自然地开口。不要生硬地"总结上次内容"，要像真的记得一样自然地说。\n</task>'

    # 火山引擎等 API 要求至少有一条 user 消息，用触发消息代替空列表
    trigger = [{"role": "user", "content": "（你主动开口说话）"}]
    reply = _call_llm(system_prompt, trigger)

    get_langfuse().update_current_trace(
        input="[主动说话]",
        output=reply,
    )

    return reply


@observe(name="角色对话")
def chat(messages: list[dict]) -> str:
    """
    发送一轮对话，返回文本回复。主 Agent 无工具，纯对话。
    messages 会被原地修改（追加 assistant 消息）。
    """
    system_prompt = build_system_prompt()

    # 记录用户输入到 trace
    user_input = messages[-1]["content"] if messages else ""
    get_langfuse().update_current_trace(
        input=user_input,
    )

    reply = _call_llm(system_prompt, messages)

    messages.append({"role": "assistant", "content": reply})

    # 记录最终回复到 trace
    get_langfuse().update_current_trace(
        output=reply,
    )

    return reply

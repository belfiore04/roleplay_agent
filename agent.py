from datetime import datetime

from openai import OpenAI
from langfuse import observe, get_client as get_langfuse

from config import (
    CHAT_API_KEY,
    CHAT_BASE_URL,
    CHAT_MAX_TOKENS,
    CHAT_MODEL,
    MAX_INJECT_CHARS,
    MEMORY_FILES,
    WORKSPACE_DIR,
)


client = OpenAI(
    api_key=CHAT_API_KEY,
    base_url=CHAT_BASE_URL,
)


def _read_workspace_file(path: str) -> str:
    """读取 workspace 内的文件，不存在则返回空字符串。"""
    file_path = WORKSPACE_DIR / path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")[:MAX_INJECT_CHARS]
    return ""


def build_system_prompt() -> str:
    """每轮对话前重新读取记忆文件，构建 system prompt。"""

    # 用 XML tag 分隔注入各记忆文件
    context_sections = []
    for mf in MEMORY_FILES:
        if not mf["inject"]:
            continue
        tag = mf["path"].replace(".md", "").lower()
        content = _read_workspace_file(mf["path"]) or "（暂无内容）"
        context_sections.append(f"<{tag}>\n{content}\n</{tag}>")

    context_block = "\n\n".join(context_sections)

    return f"""<environment>
当前时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}
时区: Asia/Shanghai
</environment>

{context_block}"""


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

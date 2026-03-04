from datetime import datetime, timedelta
from pathlib import Path

from langfuse import get_client as get_langfuse

from config import MAX_HISTORY_TURNS, MAX_LOG_DAYS, MEMORY_FILES, WORKSPACE_DIR
from agent import chat
from async_agent import start_async_agent


# 默认模板（只包含首次创建时需要的文件）
TEMPLATES = {
    "CHARACTER.md": """甄嬛 · 回宫线

你回到了紫禁城，活过了所有人，赢得了权力，坐上了太后之位。
你看似拥有一切，却在后半生彻底失去了"活着的重量"。
""",
    "SOUL.md": """# SOUL.md

## 关于你

（如果是空，则打开character.md先了解自己，并补充到这里）
""",
    "USER.md": """# USER.md - About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Timezone:**
- **Notes:**

## Context

_(What do they care about? What projects are they working on? What annoys them? What makes them laugh? Build this over time.)_

---

The more you know, the better you can help. But remember — you're learning about a person, not building a dossier. Respect the difference.
""",
    "LONG_TERM_MEMORY.md": """# 长期记忆

> 这里存放你的长期记忆，经历过的事、学到的东西、重要感悟。

（暂无内容）
""",
}


def init_workspace():
    """初始化 workspace，如果记忆文件不存在则从模板创建。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # AGENTS.md 单独处理：从 workspace 目录自带，不在模板里
    for filename, template in TEMPLATES.items():
        file_path = WORKSPACE_DIR / filename
        if not file_path.exists():
            file_path.write_text(template, encoding="utf-8")
            print(f"  已创建: {filename}")


def cleanup_old_logs():
    """清理超过 MAX_LOG_DAYS 天的聊天日志文件。"""
    cutoff = datetime.now() - timedelta(days=MAX_LOG_DAYS)
    removed = 0
    for f in WORKSPACE_DIR.glob("????-??-??.md"):
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d")
            if file_date < cutoff:
                f.unlink()
                removed += 1
        except ValueError:
            continue
    if removed:
        print(f"  已清理 {removed} 个过期日志文件")


def show_status():
    """显示当前所有记忆文件的状态。"""
    print("\n--- 记忆文件状态 ---")
    for mf in MEMORY_FILES:
        file_path = WORKSPACE_DIR / mf["path"]
        if file_path.exists():
            size = file_path.stat().st_size
            lines = len(file_path.read_text(encoding="utf-8").splitlines())
            print(f"  {mf['label']} ({mf['path']}): {lines} 行, {size} 字节")
        else:
            print(f"  {mf['label']} ({mf['path']}): 不存在")

    # 按需读取的文件
    for name in ["CHARACTER.md", "LONG_TERM_MEMORY.md"]:
        file_path = WORKSPACE_DIR / name
        if file_path.exists():
            size = file_path.stat().st_size
            lines = len(file_path.read_text(encoding="utf-8").splitlines())
            print(f"  {name} (按需读取): {lines} 行, {size} 字节")

    # 显示聊天日志
    logs = sorted(WORKSPACE_DIR.glob("????-??-??.md"))
    if logs:
        print(f"  聊天日志: {len(logs)} 个文件")
        for log in logs[-5:]:
            print(f"    - {log.name}")
    print("---\n")


def reset_workspace():
    """重置所有记忆文件为初始模板。"""
    confirm = input("确认重置所有记忆？这将删除角色的所有记忆 (y/N): ")
    if confirm.lower() != "y":
        print("已取消")
        return
    for filename, template in TEMPLATES.items():
        (WORKSPACE_DIR / filename).write_text(template, encoding="utf-8")
    # 清空聊天日志
    for f in WORKSPACE_DIR.glob("????-??-??.md"):
        f.unlink()
    print("已重置所有记忆")


def main():
    print("=" * 50)
    print("  角色扮演自修改记忆系统 MVP")
    print("=" * 50)
    print()

    # 初始化
    print("正在初始化 workspace...")
    init_workspace()
    cleanup_old_logs()
    print(f"workspace: {WORKSPACE_DIR.resolve()}")
    print()

    # 读取角色名（从 CHARACTER.md 第一行）
    char_file = WORKSPACE_DIR / "CHARACTER.md"
    if char_file.exists():
        char_content = char_file.read_text(encoding="utf-8")
        first_line = char_content.strip().splitlines()[0] if char_content.strip() else "未知角色"
        print(f"角色: {first_line}")

    print()
    print("命令: /quit 退出 | /status 查看记忆 | /reset 重置记忆")
    print("-" * 50)
    print()

    messages = []

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        # 斜杠命令
        if user_input == "/quit":
            print("再见！")
            break
        elif user_input == "/status":
            show_status()
            continue
        elif user_input == "/reset":
            reset_workspace()
            continue

        # 追加用户消息
        messages.append({"role": "user", "content": user_input})

        # 截断历史（保留最近 N 轮）
        max_messages = MAX_HISTORY_TURNS * 2  # 每轮 = 1 user + 1 assistant
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        # 调用主 Agent（纯对话，无工具）
        try:
            reply = chat(messages)
            print(f"\n角色: {reply}\n")
        except Exception as e:
            print(f"\n[错误] {e}\n")
            # 移除失败的消息，避免污染历史
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            continue

        # 每轮对话后，启动异步 Agent 在后台整理记忆
        start_async_agent(messages.copy())


if __name__ == "__main__":
    try:
        main()
    finally:
        # 确保 langfuse 数据发送完毕
        get_langfuse().flush()

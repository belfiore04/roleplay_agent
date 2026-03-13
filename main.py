import argparse
import shutil
import sys
import unicodedata
from pathlib import Path

from langfuse import get_client as get_langfuse
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML

from roleplay.config import ALL_FILES, FILE_TEMPLATES, MAX_HISTORY_TURNS, WORKSPACE_DIR
from roleplay.agent import chat
from roleplay.async_agent import get_status, start_async_agent


# CHARACTER.md 的默认模板（只在首次创建 workspace 时使用）
_CHARACTER_TEMPLATE = """甄嬛 · 回宫线

你回到了紫禁城，活过了所有人，赢得了权力，坐上了太后之位。
你看似拥有一切，却在后半生彻底失去了"活着的重量"。
"""


def init_workspace():
    """初始化 workspace，如果记忆文件不存在则从模板创建。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # CHARACTER.md 单独处理（不在 FILE_TEMPLATES 中）
    char_path = WORKSPACE_DIR / "CHARACTER.md"
    if not char_path.exists():
        char_path.write_text(_CHARACTER_TEMPLATE, encoding="utf-8")
        print("  已创建: CHARACTER.md")

    for filename, template in FILE_TEMPLATES.items():
        file_path = WORKSPACE_DIR / filename
        if not file_path.exists():
            file_path.write_text(template, encoding="utf-8")
            print(f"  已创建: {filename}")


def show_status():
    """显示当前所有记忆文件的状态。"""
    print("\n--- 记忆文件状态 ---")
    for mf in ALL_FILES:
        file_path = WORKSPACE_DIR / mf["path"]
        if file_path.exists():
            size = file_path.stat().st_size
            lines = len(file_path.read_text(encoding="utf-8").splitlines())
            print(f"  {mf['label']} ({mf['path']}): {lines} 行, {size} 字节")
        else:
            print(f"  {mf['label']} ({mf['path']}): 不存在")
    print("---\n")


def reset_workspace():
    """重置所有记忆文件为初始模板。"""
    confirm = input("确认重置所有记忆？这将删除角色的所有记忆 (y/N): ")
    if confirm.lower() != "y":
        print("已取消")
        return
    for filename, template in FILE_TEMPLATES.items():
        (WORKSPACE_DIR / filename).write_text(template, encoding="utf-8")
    print("已重置所有记忆")


def show_notes():
    """显示角色的私人笔记。"""
    notes_path = WORKSPACE_DIR / "NOTES.md"
    if not notes_path.exists() or not notes_path.read_text(encoding="utf-8").strip():
        print("\n（角色还没有写过笔记）\n")
        return
    print("\n--- 角色笔记 ---")
    print(notes_path.read_text(encoding="utf-8"))
    print("---\n")


def _display_width(s: str) -> int:
    """计算字符串的实际显示宽度（全角字符算2列）。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def _truncate_to_width(s: str, max_width: int) -> str:
    """从右侧保留，截断到指定显示宽度，超出部分用 … 替代。"""
    if _display_width(s) <= max_width:
        return s
    # 从末尾往前取，直到填满宽度
    chars = []
    width = 1  # 预留 … 的宽度
    for c in reversed(s):
        cw = 2 if unicodedata.east_asian_width(c) in ("F", "W") else 1
        if width + cw > max_width:
            break
        chars.append(c)
        width += cw
    return "…" + "".join(reversed(chars))


def _bottom_toolbar():
    """prompt_toolkit bottom toolbar 回调，显示异步 Agent 状态。"""
    status = get_status()
    prefix = "[异步] "
    text = status if status else "空闲"
    max_width = shutil.get_terminal_size().columns - _display_width(prefix) - 3
    text = _truncate_to_width(text, max_width)
    return HTML(f"<b>{prefix}</b>{text}")


def main():
    print("=" * 50)
    print("  角色扮演自修改记忆系统 MVP")
    print("=" * 50)
    print()

    # 初始化
    print("正在初始化 workspace...")
    init_workspace()
    print(f"workspace: {WORKSPACE_DIR.resolve()}")
    print()

    # 读取角色名（从 CHARACTER.md 第一行）
    char_file = WORKSPACE_DIR / "CHARACTER.md"
    if char_file.exists():
        char_content = char_file.read_text(encoding="utf-8")
        first_line = char_content.strip().splitlines()[0] if char_content.strip() else "未知角色"
        print(f"角色: {first_line}")

    print()
    print("命令: /quit 退出 | /status 查看记忆 | /notes 查看笔记 | /reset 重置记忆")
    print("-" * 50)
    print()

    session = PromptSession(bottom_toolbar=_bottom_toolbar)
    messages = []

    while True:
        try:
            user_input = session.prompt("> ").strip()
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
        elif user_input == "/notes":
            show_notes()
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


def run_single_turn(message: str, history: list = None) -> str:
    """
    单轮对话模式，用于程序化调用。
    
    Args:
        message: 用户输入消息
        history: 可选的历史消息列表
    
    Returns:
        角色回复内容
    """
    # 初始化 workspace
    init_workspace()
    
    # 准备消息
    messages = history.copy() if history else []
    messages.append({"role": "user", "content": message})
    
    # 截断历史
    max_messages = MAX_HISTORY_TURNS * 2
    if len(messages) > max_messages:
        messages = messages[-max_messages:]
    
    # 调用主 Agent
    try:
        reply = chat(messages)
        
        # 启动异步 Agent 整理记忆（不阻塞）
        start_async_agent(messages.copy())
        
        return reply
    except Exception as e:
        raise RuntimeError(f"对话失败: {e}")


def main_cli():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="角色扮演自修改记忆系统")
    parser.add_argument(
        "--message", "-m",
        type=str,
        help="单轮对话模式：直接传入用户消息，输出角色回复后退出"
    )
    parser.add_argument(
        "--workspace", "-w",
        type=str,
        default=str(WORKSPACE_DIR),
        help=f"指定 workspace 目录（默认: {WORKSPACE_DIR}）"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="显示记忆文件状态后退出"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="重置所有记忆文件"
    )
    
    args = parser.parse_args()
    
    # 更新 workspace 路径（通过修改 config 模块）
    if args.workspace != str(WORKSPACE_DIR):
        from roleplay import config
        config.WORKSPACE_DIR = Path(args.workspace).expanduser().resolve()
    
    # 处理命令
    if args.status:
        init_workspace()
        show_status()
        return
    
    if args.reset:
        init_workspace()
        reset_workspace()
        return
    
    if args.message:
        # 单轮对话模式
        try:
            reply = run_single_turn(args.message)
            print(reply)
            # 确保 langfuse 数据发送
            get_langfuse().flush()
            return
        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            sys.exit(1)
    
    # 交互式模式
    try:
        main()
    finally:
        # 确保 langfuse 数据发送完毕
        get_langfuse().flush()


if __name__ == "__main__":
    main_cli()

"""
回放测试工具：用固定的对话内容测试异步 Agent 的记忆整理效果。

用法：
    python replay.py conversation.json

输入格式（conversation.json）：
[
    {"user": "你好啊", "assistant": "（放下手里的工具）是你啊。"},
    {"user": "我们聊聊吧", "assistant": "行，聊什么？"}
]

运行时会在项目根目录创建 replay_workspace/ 文件夹，
从原始 workspace 复制只读文件（CHARACTER.md、TOOLS.md），
系统文件（SOUL.md、MEMORY.md、USER.md、NOTES.md）从空白开始。
全程不动原始 workspace。
"""

import json
import sys
import shutil
import difflib
from pathlib import Path

project_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(project_dir))

from config import WORKSPACE_DIR

REPLAY_DIR = project_dir / "replay_workspace"

# 从原始 workspace 复制的只读文件
COPY_FILES = ["CHARACTER.md", "TOOLS.md"]
# 系统文件，从空白开始
SYSTEM_FILES = ["SOUL.md", "MEMORY.md", "USER.md", "NOTES.md"]


def setup_replay_workspace():
    """创建 replay_workspace，复制只读文件，清空系统文件。"""
    if REPLAY_DIR.exists():
        shutil.rmtree(REPLAY_DIR)
    REPLAY_DIR.mkdir()

    for name in COPY_FILES:
        src = WORKSPACE_DIR / name
        if src.exists():
            shutil.copy2(src, REPLAY_DIR / name)

    for name in SYSTEM_FILES:
        (REPLAY_DIR / name).write_text("", encoding="utf-8")


def get_workspace_state():
    state = {}
    for p in REPLAY_DIR.glob("*.md"):
        state[p.name] = p.read_text("utf-8")
    return state


def print_diff(old_state, new_state):
    changed = False
    for name in sorted(new_state):
        old_text = old_state.get(name, "")
        new_text = new_state.get(name, "")
        if old_text != new_text:
            changed = True
            diff_lines = list(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"old/{name}",
                tofile=f"new/{name}",
                n=2,
            ))
            print(f"  📁 {name}")
            print("  " + "".join(diff_lines).replace("\n", "\n  "))
            print()
    if not changed:
        print("  （无变更）\n")


def main():
    if len(sys.argv) < 2:
        print("用法: python replay.py <conversation.json>")
        print()
        print("输入格式:")
        print('[{"user": "你好", "assistant": "你好啊"}, ...]')
        sys.exit(1)

    conv_file = Path(sys.argv[1])
    if not conv_file.exists():
        print(f"文件不存在: {conv_file}")
        sys.exit(1)

    conversation = json.loads(conv_file.read_text("utf-8"))
    print(f"载入 {len(conversation)} 轮对话\n")

    # 创建独立的 replay workspace
    setup_replay_workspace()
    print(f"已创建测试工作区: {REPLAY_DIR}\n")

    # 将 config 中的 WORKSPACE_DIR 指向 replay 目录
    import config
    config.WORKSPACE_DIR = REPLAY_DIR

    # 延迟导入（在修改 config 之后）
    from async_agent import run_async_agent
    from langfuse import get_client as get_langfuse

    messages = []

    for i, turn in enumerate(conversation):
        user_msg = turn["user"]
        assistant_msg = turn["assistant"]

        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": assistant_msg})

        print(f"{'='*60}")
        print(f"第 {i+1}/{len(conversation)} 轮")
        print(f"{'='*60}")
        print(f"  用户: {user_msg[:80]}{'...' if len(user_msg) > 80 else ''}")
        print(f"  角色: {assistant_msg[:80]}{'...' if len(assistant_msg) > 80 else ''}")
        print()

        old_state = get_workspace_state()

        run_async_agent(messages.copy())
        get_langfuse().flush()

        new_state = get_workspace_state()

        print("  变更:")
        print_diff(old_state, new_state)

    # 最终状态
    print(f"{'='*60}")
    print("回放结束，最终文件状态：")
    print(f"{'='*60}\n")
    for name in sorted(get_workspace_state()):
        content = (REPLAY_DIR / name).read_text("utf-8")
        if content.strip():
            print(f"--- {name} ---")
            print(content)
            print()

    print(f"测试文件保留在: {REPLAY_DIR}")
    print("如需清理: rm -rf replay_workspace/")


if __name__ == "__main__":
    main()

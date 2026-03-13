import json
import sys
import difflib
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()


def get_workspace_state():
    state = {}
    for p in (PROJECT_DIR / "workspace").glob("*.md"):
        state[p.name] = p.read_text("utf-8")
    return state

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/chat_turn.py '<user_input>'")
        return

    user_input = sys.argv[1]

    from roleplay.agent import chat
    from roleplay.async_agent import run_async_agent
    from langfuse import get_client as get_langfuse

    HISTORY_FILE = PROJECT_DIR / "history.json"
    if HISTORY_FILE.exists():
        messages = json.loads(HISTORY_FILE.read_text("utf-8"))
    else:
        messages = []

    messages.append({"role": "user", "content": user_input})
    old_state = get_workspace_state()

    print(f"\n[豆几]: {user_input}")
    reply = chat(messages)
    print(f"\n[张山]: {reply}\n")

    print("[系统] 正在后台总结记忆并触发 async_agent...")
    run_async_agent(messages.copy())
    get_langfuse().flush()

    new_state = get_workspace_state()

    current_changes = []
    print("=== 记忆文件变更 ===\n")
    for name in new_state:
        old_text = old_state.get(name, "")
        new_text = new_state.get(name, "")
        if old_text != new_text:
            print(f"📁 【{name}】 发生更新:\n")
            diff_lines = list(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f'old/{name}',
                tofile=f'new/{name}',
                n=2
            ))
            diff_text = "".join(diff_lines)
            print(diff_text, end='')
            current_changes.append({"file": name, "diff": diff_text})
            print("\n- - - - - - - - - - ")

    if not current_changes:
        print("\n[系统] 本轮记忆文件无更新。\n")

    messages.append({"role": "assistant", "content": reply, "changes": current_changes})
    HISTORY_FILE.write_text(json.dumps(messages, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

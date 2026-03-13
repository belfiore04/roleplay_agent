import json
from pathlib import Path

def export_chat_history(json_path="history.json", output_path="full_chat_history.md"):
    with open(json_path, "r", encoding="utf-8") as f:
        history = json.load(f)

    md_lines = ["# 完整聊天记录 (豆几 vs 张山)\n"]
    
    round_count = 1
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        
        if role == "user":
            md_lines.append(f"### 第 {round_count} 轮")
            md_lines.append(f"**【豆几】**:\n{content}\n")
        elif role == "assistant":
            md_lines.append(f"**【张山】**:\n{content}\n")
            md_lines.append("---\n")
            round_count += 1

    Path(output_path).write_text("\n".join(md_lines), encoding="utf-8")
    print(f"✅ 聊天记录已成功导出至: {output_path} (共 {round_count-1} 轮)")

if __name__ == "__main__":
    export_chat_history()

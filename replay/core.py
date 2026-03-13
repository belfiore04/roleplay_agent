"""Replay 系统的共享逻辑：workspace 管理、diff 计算。"""

import difflib
import shutil
from pathlib import Path


def setup_replay_workspace(replay_dir: Path, workspace_dir: Path,
                           file_templates: dict, copy_files: list[str]):
    """创建 replay workspace，复制只读文件，用模板初始化系统文件。"""
    if replay_dir.exists():
        shutil.rmtree(replay_dir)
    replay_dir.mkdir()

    for name in copy_files:
        src = workspace_dir / name
        if src.exists():
            shutil.copy2(src, replay_dir / name)

    for name, template in file_templates.items():
        (replay_dir / name).write_text(template, encoding="utf-8")


def get_workspace_state(replay_dir: Path) -> dict[str, str]:
    """获取 workspace 所有 .md 文件内容。"""
    state = {}
    for p in replay_dir.glob("*.md"):
        state[p.name] = p.read_text("utf-8")
    return state


def compute_diffs(old_state: dict, new_state: dict) -> dict[str, str]:
    """计算两个状态之间每个文件的 unified diff。"""
    diffs = {}
    for name in sorted(set(list(old_state.keys()) + list(new_state.keys()))):
        old_text = old_state.get(name, "")
        new_text = new_state.get(name, "")
        if old_text != new_text:
            diff_lines = list(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"old/{name}",
                tofile=f"new/{name}",
                n=3,
            ))
            diffs[name] = "".join(diff_lines)
    return diffs

from pathlib import Path

from config import WORKSPACE_DIR


def _safe_resolve(path: str) -> Path:
    """将相对路径解析为 workspace 内的绝对路径，防止路径逃逸。"""
    resolved = (WORKSPACE_DIR / path).resolve()
    workspace_resolved = WORKSPACE_DIR.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise PermissionError(f"禁止访问 workspace 之外的路径: {path}")
    return resolved


def read_file(path: str) -> str:
    """读取 workspace 内的文件。"""
    file_path = _safe_resolve(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    return file_path.read_text(encoding="utf-8")


PROTECTED_FILES = {"CHARACTER.md"}


def write_file(path: str, content: str) -> str:
    """创建或覆盖 workspace 内的文件。"""
    if Path(path).name in PROTECTED_FILES:
        return f"错误: {path} 是只读文件，不允许修改"
    file_path = _safe_resolve(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"已写入: {path}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """精确替换文件中的文本片段。"""
    if Path(path).name in PROTECTED_FILES:
        return f"错误: {path} 是只读文件，不允许修改"
    file_path = _safe_resolve(path)
    if not file_path.exists():
        return f"错误: 文件不存在 - {path}"
    content = file_path.read_text(encoding="utf-8")
    if old_text not in content:
        return f"错误: 未找到要替换的文本"
    count = content.count(old_text)
    if count > 1:
        return f"错误: 找到 {count} 处匹配，请提供更精确的文本以确保唯一匹配"
    new_content = content.replace(old_text, new_text, 1)
    file_path.write_text(new_content, encoding="utf-8")
    return f"已更新: {path}"


# 工具执行分发
TOOL_HANDLERS = {
    "read_file": lambda args: read_file(args["path"]),
    "write_file": lambda args: write_file(args["path"], args["content"]),
    "edit_file": lambda args: edit_file(args["path"], args["old_text"], args["new_text"]),
}

# Anthropic tool schemas
TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "读取 workspace 内的文件内容。用于查看你的记忆文件或其他文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的相对路径，如 'USER.md'、'CHARACTER.md'、'SOUL.md'、'MEMORY.md' 或 '2026-03-02.md'",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "创建或覆盖 workspace 内的文件。用于更新你的记忆、记录新的信息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的相对路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整文件内容",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "精确替换文件中的一段文本。适合小范围修改，无需重写整个文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的相对路径",
                },
                "old_text": {
                    "type": "string",
                    "description": "要被替换的原始文本（必须精确匹配）",
                },
                "new_text": {
                    "type": "string",
                    "description": "替换后的新文本",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
]

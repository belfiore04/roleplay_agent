import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 主 Agent API（纯对话，无工具，OpenAI 兼容）
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "")
CHAT_BASE_URL = os.getenv("CHAT_BASE_URL", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "deepseek-v3.2")
CHAT_MAX_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "4096"))

# 异步 Agent API（后台记忆管理，有文件工具，Anthropic 兼容）
ASYNC_AGENT_API_KEY = os.getenv("ASYNC_AGENT_API_KEY", "")
ASYNC_AGENT_BASE_URL = os.getenv("ASYNC_AGENT_BASE_URL", "")
ASYNC_AGENT_MODEL = os.getenv("ASYNC_AGENT_MODEL", "MiniMax-M2.5")
ASYNC_AGENT_MAX_TOKENS = int(os.getenv("ASYNC_AGENT_MAX_TOKENS", "4096"))

# Workspace
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", Path(__file__).parent.parent / "workspace"))

# 主 Agent 注入的文件（纯对话，不需要工具相关信息）
MAIN_INJECT_FILES = [
    {"path": "CHARACTER.md", "label": "角色设定"},
    {"path": "SOUL.md", "label": "角色灵魂"},
    {"path": "USER.md", "label": "用户信息"},
    {"path": "MEMORY.md", "label": "对话记忆"},
]

# 异步 Agent 注入的文件（后台记忆管理，比主 Agent 多看到工具目录）
ASYNC_INJECT_FILES = [
    {"path": "CHARACTER.md", "label": "角色设定"},
    {"path": "SOUL.md", "label": "角色灵魂"},
    {"path": "USER.md", "label": "用户信息"},
    {"path": "MEMORY.md", "label": "对话记忆"},
    {"path": "TOOLS.md", "label": "工具目录"},
]

# 所有 workspace 文件（供 /status 显示）
ALL_FILES = MAIN_INJECT_FILES + [
    {"path": "TOOLS.md", "label": "工具目录"},
    {"path": "NOTES.md", "label": "角色笔记"},
]

# 每个记忆文件注入的最大字符数
MAX_INJECT_CHARS = int(os.getenv("MAX_INJECT_CHARS", "10000"))

# 对话历史保留轮数
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "50"))

# 系统文件初始模板
FILE_TEMPLATES = {
    "SOUL.md": "# Soul\n\n## 成长变化\n\n",
    "MEMORY.md": "# Memory\n\n## 钉住的（不可压缩）\n\n\n## 近期\n\n",
    "USER.md": "# User\n\n## 身份\n\n## 性格\n\n## 喜好\n\n## 与角色的关系\n\n",
    "NOTES.md": "",
}

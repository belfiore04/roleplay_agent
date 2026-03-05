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
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", Path(__file__).parent / "workspace"))

# 每轮注入 system prompt 的文件（只注入核心文件，其余按需读取）
MEMORY_FILES = [
    {"path": "CHARACTER.md", "label": "角色设定", "inject": True},
    {"path": "SOUL.md", "label": "角色灵魂", "inject": True},
    {"path": "USER.md", "label": "用户信息", "inject": True},
    {"path": "MEMORY.md", "label": "对话记忆", "inject": True},
]

# 每个记忆文件注入的最大字符数
MAX_INJECT_CHARS = int(os.getenv("MAX_INJECT_CHARS", "10000"))

# 对话历史保留轮数
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "50"))

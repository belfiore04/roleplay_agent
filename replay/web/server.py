"""
Replay Web UI：实时可视化异步 Agent 的记忆整理过程。

用法：
    python -m replay.web.server data/test_conversation.json
    python -m replay.web.server data/test_mini.json --port 8888

启动后自动打开浏览器，实时显示每轮对话后的文件变更。
"""

import asyncio
import json
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from roleplay.config import FILE_TEMPLATES, WORKSPACE_DIR

from replay.core import setup_replay_workspace, get_workspace_state, compute_diffs

PROJECT_DIR = Path(__file__).parent.parent.parent.resolve()
REPLAY_DIR = PROJECT_DIR / "replay_workspace"
COPY_FILES = ["CHARACTER.md", "TOOLS.md", "AGENTS.md"]

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI()

# 全局状态
_conversation = []
_conv_file_name = ""


def run_replay(ws_send, stop_event: threading.Event):
    """在同步线程中执行 replay，通过 ws_send 回调发送消息。"""
    from roleplay.async_agent import run_async_agent
    from langfuse import get_client as get_langfuse

    messages = []

    for i, turn in enumerate(_conversation):
        if stop_event.is_set():
            ws_send(json.dumps({"type": "stopped", "turn": i}))
            return

        user_msg = turn["user"]
        assistant_msg = turn["assistant"]

        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": assistant_msg})

        # 通知前端：开始处理
        ws_send(json.dumps({
            "type": "turn_start",
            "turn": i,
            "total": len(_conversation),
            "user": user_msg,
            "assistant": assistant_msg,
        }))

        old_state = get_workspace_state(REPLAY_DIR)

        try:
            run_async_agent(messages.copy(), workspace_dir=REPLAY_DIR)
            get_langfuse().flush()
        except Exception as e:
            if stop_event.is_set():
                ws_send(json.dumps({"type": "stopped", "turn": i}))
                return
            ws_send(json.dumps({
                "type": "turn_error",
                "turn": i,
                "error": str(e),
            }))
            continue

        new_state = get_workspace_state(REPLAY_DIR)
        diffs = compute_diffs(old_state, new_state)

        ws_send(json.dumps({
            "type": "turn_done",
            "turn": i,
            "total": len(_conversation),
            "user": user_msg,
            "assistant": assistant_msg,
            "diffs": diffs,
            "files": new_state,
        }))

    ws_send(json.dumps({"type": "done"}))


@app.get("/")
async def index():
    """返回前端页面。"""
    html_path = TEMPLATES_DIR / "replay.html"
    content = html_path.read_text("utf-8")
    # 注入对话文件名
    content = content.replace("{{CONV_FILE}}", _conv_file_name)
    content = content.replace("{{TOTAL_TURNS}}", str(len(_conversation)))
    return HTMLResponse(content)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点，等待前端发送 start/stop 指令。"""
    await websocket.accept()

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    stop_event = threading.Event()
    replay_thread = None

    def ws_send(msg: str):
        """线程安全地把消息放入队列。"""
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    try:
        while True:
            # 等待前端指令
            raw = await websocket.receive_text()
            cmd = json.loads(raw)

            if cmd.get("type") == "start":
                # 如果有正在运行的 replay，先停掉
                if replay_thread and replay_thread.is_alive():
                    stop_event.set()
                    replay_thread.join(timeout=5)

                # 清空队列和状态
                while not queue.empty():
                    queue.get_nowait()
                stop_event.clear()

                # 重新初始化 workspace
                setup_replay_workspace(REPLAY_DIR, WORKSPACE_DIR, FILE_TEMPLATES, COPY_FILES)

                # 启动新的 replay 线程
                replay_thread = threading.Thread(
                    target=run_replay, args=(ws_send, stop_event), daemon=True
                )
                replay_thread.start()

                # 并发：转发队列消息 + 监听前端指令
                while True:
                    # 同时等待队列消息和 WebSocket 消息
                    queue_task = asyncio.ensure_future(queue.get())
                    ws_task = asyncio.ensure_future(websocket.receive_text())

                    done, pending = await asyncio.wait(
                        [queue_task, ws_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    # 取消未完成的任务
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

                    if queue_task in done:
                        msg = queue_task.result()
                        try:
                            await websocket.send_text(msg)
                        except Exception:
                            stop_event.set()
                            break
                        data = json.loads(msg)
                        if data.get("type") in ("done", "stopped"):
                            break

                    if ws_task in done:
                        try:
                            raw = ws_task.result()
                            cmd = json.loads(raw)
                            if cmd.get("type") == "stop":
                                stop_event.set()
                                # 继续 drain 直到收到 stopped
                        except Exception:
                            stop_event.set()
                            break

    except Exception:
        # WebSocket 断开
        stop_event.set()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Replay Web UI")
    parser.add_argument("conversation", help="对话 JSON 文件路径")
    parser.add_argument("--port", type=int, default=8765, help="端口号 (默认: 8765)")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    global _conversation, _conv_file_name

    conv_path = Path(args.conversation)
    if not conv_path.exists():
        print(f"文件不存在: {conv_path}")
        sys.exit(1)

    _conversation = json.loads(conv_path.read_text("utf-8"))
    _conv_file_name = conv_path.name
    print(f"载入 {len(_conversation)} 轮对话: {conv_path}")

    if not args.no_open:
        # 延迟打开浏览器，等服务器启动
        def open_browser():
            import time
            time.sleep(1)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"启动服务: http://localhost:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

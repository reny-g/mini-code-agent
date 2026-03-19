"""
相对s1添加了更多的工具，并且稍微规范了工具的执行：使用字典分发，并且在路径沙箱内执行
"""
from openai import OpenAI
import os
from pathlib import Path
import subprocess
import json

SYSTEM = f"You are a coding agent at {Path.cwd()}. Use tools to solve tasks. Act, don't explain."

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def _safe_path(relative_path: str) -> Path:
    # 逃逸情况举例：p = "../../etc/passwd"
    path = (Path.cwd() / relative_path).resolve()
    if not Path.relative_to(path, Path.cwd()):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_write(path: str, content: str) -> str:
    try:
        file_path = _safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"error: {e}"

def run_read(path: str, limit: int) -> str:
    """
    limit用于限制需要返回的行数
    """
    try:
        text = _safe_path(path).read_text(encoding="utf-8")
        lines = text.splitlines()
        if limit and len(lines) > limit:
            lines = lines[:limit]
            lines.append(f"... ({len(lines) - limit}) more lines.")
        # 最后兜底5000行
        return "/n".join(lines[:5000])
    except Exception as e:
        return f"error: {e}"

def run_edit(path, old_content: str, new_content: str) -> str:
    try:
        file_path = _safe_path(path)
        content = file_path.read_text(encoding="utf-8")
        if old_content not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_content, new_content, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"error: {e}"

# 字典类型的工具分发器(根据名称返回对应的工具函数)与工具协议描述
HANDLERS = {
    "run_bash": lambda **kw: run_bash(kw["command"]),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "run a shell command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"]
        },
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read file contents.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write content to file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace exact text in file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
    }},
]

# 核心 agent loop：持续调用工具直到模型停止
def agent_loop(messages: list):
    # todo 可以限制最多循环多少次防止错误死循环
    count = 0
    tool_history = []
    max_steps = 10
    while True:
        count += 1
        if count > max_steps:
            print(f"error: 超出最大工具调用次数{max_steps}")
            break
        print(f"第 {count} 轮次对话")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=TOOLS,
            extra_body={"enable_thinking": False},
        )
        msg = response.choices[0].message
        # 追加 assistant 回复
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        # 模型没有调用工具，结束循环
        if response.choices[0].finish_reason != "tool_calls":
            # print(f"最后响应：msg，结束循环：{response.choices[0]}")
            # print()
            break

        # 执行每个工具调用，把结果塞回 messages
        for tool_call in msg.tool_calls:
            print(f"调用工具数量：{len(msg.tool_calls)}")
            handler = HANDLERS.get(tool_call.function.name)
            tool_history.append({"name": tool_call.function.name, "arguments": tool_call.function.arguments[:100]+"\n..."})
            print(f"执行工具 {tool_call.function.name}，参数 {tool_call.function.arguments}")

            arguments = json.loads(tool_call.function.arguments)
            output = handler(**arguments) if handler else f"unknow tool: {tool_call.function.name}"
            print(f"工具执行结果：{output[:200]}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })
    print(f"工具调用历史：{json.dumps(tool_history, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # 始终查看最新回复
        last = history[-1]["content"]
        if last:
            print(last)
        print()

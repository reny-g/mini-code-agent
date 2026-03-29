"""
相对s1添加了更多的工具，并且稍微规范了工具的执行：使用字典分发，并且在路径沙箱内执行
"""
from openai import OpenAI
import os
from pathlib import Path
import subprocess
import json

SYSTEM = f"""You are a coding agent at {Path.cwd().parent}.
Workspace layout: {list(Path.cwd().parent.iterdir())}
Use the todo tool to plan multi-step tasks. Mark in_progress before starting, completed when done.
IMPORTANT: 
1. Only mark a task completed after you have actually performed and verified the work.
2. Prefer tools over prose.
3. Do NOT switch to other directories if you encounter errors — report the error instead.

NOTICE: Before you use bash tool, make sure you know the os environment is linux or windows.
"""

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"

class TodoManager:
    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """
        更新任务列表
        """
        if len(items) >= 20:
            raise ValueError("max 20 tods items allowed!")
        validated = []
        in_progress_count = 0
        for i, item in enumerate(items):
            item_id = str(item.get('id', i+1))
            text = str(item.get('text', "")).strip()
            status = str(item.get('status', "pending"))

            # 检查文本和校验状态
            if not text:
                raise ValueError(f"Item {item_id}: text required")
            if status not in ["pending", "in_progress", "completed"]:
                raise ValueError(f"Item {item_id}: invalid status {status}")
            if status == "in_progress":
                in_progress_count += 1
            if in_progress_count >= 2:
                raise ValueError(f"only one task in status 'in_progress'")
            validated.append({"id": item_id, "text": text, "status": status})
        self.items = items
        return self._render(items)

    def _render(self, items: list) -> str:
        if not items:
            return 'no todos'
        lines = []
        for item in items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            line = f'{marker} {item["id"]}: {item["text"]}'
            lines.append(line)
        # 计算已经完成的任务
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

todo_manager = TodoManager()

def _safe_path(relative_path: str) -> Path:
    # 逃逸情况举例：p = "../../etc/passwd"
    path = (Path.cwd() / relative_path).resolve()
    if not Path.is_relative_to(path, Path.cwd()):
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
            capture_output=True, text=True, timeout=120, encoding=None, errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

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
    "todo": lambda **kw: todo_manager.update(kw["items"])
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
        "name": "write_file", "description": "Write content to file. Use only for new files or small files (<50 lines). For editing existing files, prefer edit_file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace exact text in file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
    }},    
    {"type": "function", "function": {
        "name": "todo", "description": "Update task list. Track progress on multi-step tasks.",
        "parameters": {"type": "object", "properties": 
            {"items": {"type": "array", "items": 
                {"type": "object", "properties": 
                    {"id": {"type": "string"}, "text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["id", "text", "status"]}}}, "required": ["items"]}}},
]
# 核心 agent loop：持续调用工具直到模型停止
def agent_loop(messages: list):
    # todo 可以限制最多循环多少次防止错误死循环
    count = 0 # 对话轮次
    tool_history = [] # 对话历史
    max_steps = 20 # 最大步数
    rounds_since_todo = 0 # 未注入todo工具调用的轮数 
    while True:
        count += 1
        if count > max_steps:
            print(f"error: 超出最大对话轮数{max_steps}")
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
        use_todo = False
        for tool_call in msg.tool_calls:
            print(f"调用工具数量：{len(msg.tool_calls)}")
            handler = HANDLERS.get(tool_call.function.name)
            tool_history.append({"name": tool_call.function.name, "arguments": tool_call.function.arguments[:100]+"\n..."})
            print(f"执行工具 {tool_call.function.name}，参数 {json.dumps(tool_call.function.arguments, ensure_ascii=False, indent=2)}")

            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                output = f"Error: Failed to parse tool arguments: {e}"
                print(f"工具执行结果：{output}")
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": output})
                continue
            output = handler(**arguments) if handler else f"unknow tool: {tool_call.function.name}"
            print(f"工具执行结果：{output[:200]}")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })
        # 
        if tool_call.function.name == "todo":
            use_todo = True
        rounds_since_todo = 0 if use_todo else rounds_since_todo + 1
        # 超过三轮没有调用todo工具时，强行注入todo的结果
        if rounds_since_todo >= 3:
            messages.append({
                "role": "user",
                "content": "<reminder> update your todos </reminder>"
            })

    print(f"工具调用历史：{json.dumps(tool_history, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
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

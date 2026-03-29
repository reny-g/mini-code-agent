"""
将任务落在磁盘上，有些类似s3中的plan模式。这里主要是为了多agent的实现打基础。
    .tasks/
      task_1.json  {"id":1, "subject":"...", "status":"completed", ...}
      task_2.json  {"id":2, "blockedBy":[1], "status":"pending", ...}
      task_3.json  {"id":3, "blockedBy":[2], "blocks":[], ...}
"""
from openai import OpenAI
import os
from pathlib import Path
import subprocess
import json

SYSTEM = f"You are a coding agent at {Path.cwd()}. Before your act, ensure what the env is. Use task tools to plan and track work. Act, don't explain."

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"

class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        ids = [int(f.stem.split('_')[1]) for f in self.dir.glob('task_*.json')]
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> dict:
        """根据task_id读取文件内容"""
        path = self.dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, task: dict):
        """task写入文件"""
        path = self.dir / f"task_{task['id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(task, indent=2, ensure_ascii=False))

    def create(self, subject: str, description: str = "") -> str:
        """创建任务并存入磁盘，更新next_id"""
        task = {
            "id": self._next_id, "subject": subject, "description": description,
            "status": "pending", "blockedBy": [], "blocks": [], "owner": ""
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2, ensure_ascii=False)

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2, ensure_ascii=False)

    def update(self, task_id: int, status: str = None,
               add_blockedBy: list = None, add_blocks: list = None) -> str:
        task = self._load(task_id)
        if status:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f'invalid status: {status}')
            if status == "completed":
                self._clear_dependency(task_id)
            task["status"] = status
        if add_blockedBy:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blockedBy))
        if add_blocks:
            # 同时反向更新被阻塞任务的 blockedBy
            task["blocks"] = list(set(task["blocks"] + add_blocks))
            for blocked_id in add_blocks:
                blocked_task = self._load(blocked_id)
                if task_id not in blocked_task["blockedBy"]:
                    blocked_task["blockedBy"].append(task_id)
                    self._save(blocked_task)
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def _clear_dependency(self, completed_id: int):
        """清除因为当前id而阻塞的任务，需要遍历所有任务"""
        for f in self.dir.glob("task_*.json"):
            task_id = int(f.stem.split('_')[1])
            task = self._load(task_id)
            if completed_id in task["blockedBy"]:
                task["blockedBy"].remove(completed_id)
                self._save(task)

    def list_all(self) -> str:
        """列出所有任务的状态"""
        tasks = []
        for f in sorted(self.dir.glob('task_*.json')):
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        if not tasks:
            return "no tasks."
        lines = []
        for task in tasks:
            marker = {"pending": "[]", "in_progress": "[>]", "completed": "[x]"}.get(task["status"], "?")
            blockedBy = f"blocked by: {task['blockedBy']}" if task.get("blockedBy") else ""
            lines.append(f"{marker} #{task['id']}: {task['subject']} {blockedBy}")
        return "\n".join(lines)


TASKS = TaskManager(Path.cwd() / ".tasks")

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
    path = (Path.cwd() / relative_path).resolve()
    if not path.is_relative_to(Path.cwd()):
        raise ValueError(f"Path escapes workspace: {relative_path}")
    return path

def run_write(path: str, content: str) -> str:
    try:
        file_path = _safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"error: {e}"

def run_read(path: str, limit: int = None) -> str:
    """limit用于限制需要返回的行数"""
    try:
        text = _safe_path(path).read_text(encoding="utf-8")
        lines = text.splitlines()
        if limit and len(lines) > limit:
            lines = lines[:limit]
            lines.append(f"... ({len(lines) - limit}) more lines.")
        return "\n".join(lines[:5000])
    except Exception as e:
        return f"error: {e}"

def run_edit(path: str, old_content: str, new_content: str) -> str:
    try:
        file_path = _safe_path(path)
        content = file_path.read_text(encoding="utf-8")
        if old_content not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_content, new_content, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"error: {e}"

HANDLERS = {
    "run_bash":    lambda **kw: run_bash(kw["command"]),
    "write_file":  lambda **kw: run_write(kw["path"], kw["content"]),
    "read_file":   lambda **kw: run_read(kw["path"], kw.get("limit")),
    "edit_file":   lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "task_create": lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("add_blockedBy"), kw.get("add_blocks")),
    "task_list":   lambda **kw: TASKS.list_all(),
    "task_get":    lambda **kw: TASKS.get(kw["task_id"]),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "run_bash", "description": "run a shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
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
    {"type": "function", "function": {
        "name": "task_create", "description": "Create a new task.",
        "parameters": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]},
    }},
    {"type": "function", "function": {
        "name": "task_update", "description": "Update a task's status or dependencies.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "integer"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            "add_blockedBy": {"type": "array", "items": {"type": "integer"}},
            "add_blocks": {"type": "array", "items": {"type": "integer"}}
        }, "required": ["task_id"]},
    }},
    {"type": "function", "function": {
        "name": "task_list", "description": "List all tasks with status summary.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "task_get", "description": "Get full details of a task by ID.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
    }},
]

def agent_loop(messages: list):
    count = 0
    tool_history = []
    max_steps = 10
    while True:
        count += 1
        if count > max_steps:
            print(f"error: 超出最大工具调用次数{max_steps}")
            break
        print('='*50)
        print(f"第 {count} 轮次对话")
        print('='*50)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=TOOLS,
            extra_body={"enable_thinking": False},
        )
        msg = response.choices[0].message
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        if response.choices[0].finish_reason != "tool_calls":
            break

        for tool_call in msg.tool_calls:
            print(f"调用工具数量：{len(msg.tool_calls)}")
            handler = HANDLERS.get(tool_call.function.name)
            tool_history.append({"name": tool_call.function.name, "arguments": tool_call.function.arguments[:100] + "\n..."})
            print(f"执行工具 {tool_call.function.name}，参数 {tool_call.function.arguments}")
            arguments = json.loads(tool_call.function.arguments)
            output = handler(**arguments) if handler else f"unknown tool: {tool_call.function.name}"
            print(f"工具执行结果：{output[:200]}")
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": output})
    print(f"工具调用历史：{json.dumps(tool_history, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms07 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        last = history[-1]["content"]
        if last:
            print(last)
        print()

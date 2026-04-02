"""
后台线程使用subprocess异步执行"命令"
"""
from openai import OpenAI
import os
from pathlib import Path
import subprocess
import json
import uuid
import threading

SYSTEM = f"You are a coding agent at {Path.cwd()}. 开始前注意是出于windows环境还是linux环境。 Use tools to solve tasks. Act, don't explain."

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"

# thread execution+notification queue
class BackGroundManager:
    def __init__(self):
        self.tasks: dict[str, dict] = {} # taskid -> status, command, result
        self._notification_queue: list[dict] = [] # 用于存放任务执行结果
        self._lock = threading.Lock() # 主要用于queue的互斥使用

    def start(self, command: str) -> str:
        """
        将命令放入线程去异步执行
        """
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {
            "status": "running",
            "command": command,
            "result": None,
        }
        thread = threading.Thread(
            target=self._execute, args=(task_id, command), daemon=True
        )
        thread.start()
        return f"task {task_id} started!"

    def _execute(self,task_id, command):
        """
        使用subprocess异步执行, 并将结果互斥放入queue
        """
        try:
            r = subprocess.run(
                command, shell=True, cwd=Path.cwd(),
                capture_output=True, text=True, timeout=300
            )
            status = "completed"
            result = (r.stdout+r.stderr)[:50000]
        except TimeoutError as e:
            status = "timeout"
            result = f"failed execute: timeout {300}s"
        except Exception as e:
            status = "error"
            result =  f"failed execute: { str(e)}"
        
        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["result"] = result

        with self._lock:
            self._notification_queue.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "result": (result or "no output")[:800],
                    "command": command[:80],
                }
            )

    def check(self, task_id: str=None) -> str:
        """
        检查一个或所有任务的状态
        """
        if task_id:
            task = self.tasks.get(task_id)
            if not task:
                return f"error: unknow task {task_id}"
            return f"{task['status']}: {task['command'][:80]}\nresult: {task.get('result', 'running')}"
        lines = []
        # kv对遍历字典
        for tid, task in self.tasks.items():
            line = f"{tid}: {task['status']} \n command: {task['command']} \n result: {task['result']}"
            lines.append(line)
        return '\n'.join(lines)

    def drain_notification(self) -> list:
        # 清空任务结果队列
        notifications = []
        print(f"清空任务结果队列：")
        with self._lock:
            notifications = list(self._notification_queue) # 进行浅克隆
            self._notification_queue.clear()
        return notifications

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
    if not Path.is_relative_to(path, Path.cwd()):
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

BG = BackGroundManager()
# 字典类型的工具分发器(根据名称返回对应的工具函数)与工具协议描述
HANDLERS = {
    "run_bash": lambda **kw: run_bash(kw["command"]),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "background_run": lambda **kw: BG.start(kw["command"]),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
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
    {"type": "function", "function": {
        "name": "background_run", "description": "Run command in background thread. Returns task_id immediately.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "check_background", "description": "Check background task status. Omit task_id to list all.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}},
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
        # 每轮开始前，检查后台任务是否有新结果（用 user 消息注入）
        notifications = BG.drain_notification()
        notif_text = ""
        for n in notifications:
            notif_text +=  f"[bg task {n['task_id']}] {n['status']}: {n['result']}\n"
        messages.append({
            "role": "user",
            "content": notif_text
        })
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
            print(f"最后响应：msg，结束循环：{response.choices[0].message.content}")
            # print()
            break

        # 执行每个工具调用，把结果塞回 messages
        for tool_call in msg.tool_calls:
            print(f"调用工具数量：{len(msg.tool_calls)}")
            name = tool_call.function.name
            handler = HANDLERS.get(name)
            tool_history.append({"name": name, "arguments": tool_call.function.arguments[:100]+"\n..."})
            print(f"执行工具 {name}，参数 {tool_call.function.arguments}")

            arguments = json.loads(tool_call.function.arguments)
            output = handler(**arguments) if handler else f"unknow tool: {name}"

            print(f"工具执行结果：{output[:200]}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })
    print(f"工具调用历史：{json.dumps(tool_history, ensure_ascii=False, indent=2)}")
    print("\n对话历史：")
    for i, m in enumerate(messages):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "?")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "?")
        print(f"  [{i}] {role}: {str(content)[:100]}")

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
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

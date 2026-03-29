"""
两层压缩：第一层为每轮都进行工具压缩。第二层为上下文压缩，达到固定阈值时触发
"""
from openai import OpenAI
import os
from pathlib import Path
import subprocess
import json
import re
import time

SKILLS_DIR = Path.cwd() / "skills"
print(f'SKILLS_DIR: {SKILLS_DIR}')

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"
THRESHOLD = 5000 # 触发llm压缩的阈值

class SkillLoader:
    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.skills = {}
        self._load_all()

    def _load_all(self):
        if not self.skill_dir:
            return
        # 搜索对应目录下的所有skill描述
        for f in sorted(self.skill_dir.rglob('SKILL.md')):
            text = f.read_text(encoding="utf-8")
            meta, body = self.parse_format(text)
            name = meta.get('name', f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}
            print(f"load_skill {name}: {self.skills[name]}")

    def parse_format(self, text: str) -> tuple:
        """解析skill描述，为yaml和正文。一般---分隔符中yaml的内容为元信息"""
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1) # 参数1表示只匹配一次
                meta[key.strip()] = val.strip()
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        """第一层次：获得skill描述"""
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill.get('description', "no description")
            line = f'- {name}: {desc}'
            lines.append(line)
        return '\n'.join(lines)


    def get_content(self, name: str) -> str:
        """渐进式加载的第二层次：获得skill的具体内容"""
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill meta=\"{skill['meta']}\">\n{skill['body']}\n</skill>"
        
SKILL_LOADER = SkillLoader(SKILLS_DIR)

SYSTEM = f"""You are a coding agent at {Path.cwd()}. your maybe run on windows or linux. Use load_skill to access specialized knowledge before tackling unfamiliar topics.
Skills available:
{SKILL_LOADER.get_descriptions()}."""


TOOL_KEEP_RECENT = 3
def micro_compact(messages: list) -> list:
    """
    将messages中部分工具结果的内容超过一百的，改为只记录名称
    """
    tool_results = []
    for i, msg in enumerate(messages):
        if msg["role"] == "tool":
            # 记录压缩前的工具执行结果
            tool_results.append((i, msg["content"]))

    # 记录工具名称
    tool_call_map = {}
    for msg in messages:
        if msg["role"] == "assistant" and msg["tool_calls"]:
            for tool_call in msg["tool_calls"]:
                tool_call_map[tool_call.id] = tool_call.function.name

    # 这里对工具执行结果直接进行移除，content用名字进行替代
    for msg, content in tool_results[:-TOOL_KEEP_RECENT]:
        if len(content) >= 100:
            content = f"previous used tool: {tool_call_map.get(msg.tool_call_id, 'unknow')} ..."
    return messages

def auto_compact(messages: list) -> list:
    """
    历史上下文超过阈值时调用llm进行压缩（除了system类型的消息）
    """
    # 1.压缩前写到本地
    transcrip_path = Path.cwd() / f"transcript_{int(time.time())}.jsonl"
    with open(transcrip_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")

    # 2.使用llm进行压缩
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", 
            "content": "Summarize this conversation for continuity..."+json.dumps(messages, default=str)[:80000]}],
        extra_body={"enable_thinking": False},
        max_tokens=2000, # 限制llm的输出为不超过2000
    )
    return [
        [m for m in messages if m["role"] == "system"]
        +
        [{"role": "user", "content": f"compressed \n\n {response.choices[0].message.content}"}, {"role": "assistant", "content": f"Understood. Continuing."}]
    ]


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

def estimate_tokens(messages: list) -> int:
    """粗略地人为四个字符一个token"""
    return len(str(messages)) // 4


# 字典类型的工具分发器(根据名称返回对应的工具函数)与工具协议描述
HANDLERS = {
    "run_bash": lambda **kw: run_bash(kw["command"]),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
    # todo 不依赖模型传参直接修改本地messages
    "compact":    lambda **kw: "Manual compression requested.",
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
    {
    "type": "function", "function": {
        "name": "load_skill", "description": "Load specialized knowledge by name.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name to load"}}, "required": ["name"]}
    }},
    {
    "type": "function", "function": {
        "name": "compact", "description": "Trigger manual conversation compression.",
        "parameters": {"type": "object", "properties": {"focus": {"type": "string", "description": "What to preserve in the summary"}}},
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
        # 层次一的压缩每轮都进行
        micro_compact(messages)
        # 层次二的压缩看是否达到阈值
        if estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = auto_compact(messages)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=TOOLS,
            extra_body={"enable_thinking": False},
            max_tokens=8000,
        )
        msg = response.choices[0].message
        # 追加 assistant 回复
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        # 模型没有调用工具，结束循环
        if response.choices[0].finish_reason != "tool_calls":
            break

        # 执行每个工具调用，把结果塞回 messages
        manual_compact = False
        for tool_call in msg.tool_calls:
            print(f"调用工具数量：{len(msg.tool_calls)}")
            function_name = tool_call.function.name
            function_arguments = tool_call.function.arguments
            handler = HANDLERS.get(function_name)
            tool_history.append({"name": function_name, "arguments": function_arguments[:100]+"\n..."})
            print(f"执行工具 {function_name}，参数 {function_arguments}")

            arguments = json.loads(function_arguments)
            # compact 工具单独执行
            if function_name == "compact":
                output = "messages compressing"
                manual_compact = True
            else:
                try:
                    output = handler(**arguments) if handler else f"unknow tool: {function_name}"
                except Exception as e:
                    output = f"{function_name} execute error: {str(e)}"

            print(f"工具执行结果：{output[:200]}...")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })
        if manual_compact:
            print("[manual compact]")
            messages[:] = auto_compact(messages)
    print(f"工具调用历史：{json.dumps(tool_history, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
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

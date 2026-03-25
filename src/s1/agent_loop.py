from openai import OpenAI
import os
import subprocess
import json

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }
    }
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

# 核心 agent loop：持续调用工具直到模型停止
def agent_loop(messages: list):
    while True:
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
            return

        # 执行每个工具调用，把结果塞回 messages
        for tool_call in msg.tool_calls:
            print(f"一次性调用工具数量：{len(msg.tool_calls)}")
            print(f'tool_call: {tool_call}')
            command = json.loads(tool_call.function.arguments)["command"]
            print(f"\033[33m$ {command}\033[0m")
            output = run_bash(command)
            print(output[:200])
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        # 同时查看当前目录和windows下的用户根目录
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # 始终查看最新回复
        last = history[-1]["content"]
        if last:
            print(last)
        print()

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

def agent_loop(messages: list):
    while True:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tools=TOOLS,
            extra_body={"enable_thinking": False},
            stream=True,
        )

        # 流式拼接：文本内容 + 工具调用参数
        text_res = ""
        # tool_calls_map: index -> {id, name, arguments}
        tool_calls_map = {}
        finish_reason = None

        for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta
            finish_reason = choice.finish_reason or finish_reason

            # 拼接文本
            if delta.content:
                print(delta.content, end="", flush=True)
                text_res += delta.content

            # 拼接工具调用（参数是分块流过来的）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    # 开始收集工具名称和参数
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": tc.id, "name": tc.function.name, "arguments": ""}
                    if tc.function.arguments:
                        tool_calls_map[idx]["arguments"] += tc.function.arguments

        if text_res:
            print()  # 换行

        # 把 assistant 消息追加到 messages
        tool_calls_for_msg = None
        if tool_calls_map:
            tool_calls_for_msg = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]}
                }
                for tc in tool_calls_map.values()
            ]

        messages.append({
            "role": "assistant",
            "content": text_res or None,
            **({"tool_calls": tool_calls_for_msg} if tool_calls_for_msg else {})
        })

        # 没有工具调用，结束循环
        if finish_reason != "tool_calls":
            return

        # 执行工具，把结果塞回 messages
        for tc in tool_calls_map.values():
            command = json.loads(tc["arguments"])["command"]
            print(f"\033[33m$ {command}\033[0m")
            output = run_bash(command)
            print(output[:200])
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": output
            })

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01-stream >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        print()

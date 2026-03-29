#!/usr/bin/env python3

# This is the main entry point for the agent.
# It imports and runs the agent logic from agent_tool.py.

from agent_tool import agent_loop
history = []

if __name__ == "__main__":
    print("Starting mini-code-agent...")
    try:
        while True:
            query = input("\033[36ms02 >> \033[0m")
            if query.strip().lower() in ("q", "exit", ""):
                break
            history.append({"role": "user", "content": query})
            agent_loop(history)
            if history[-1]["content"]:
                print(history[-1]["content"])
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye!")

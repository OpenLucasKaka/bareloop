from pathlib import Path

from bareloop.goal import stop_goal_gate
from bareloop.settings import DENY_LIST, DESTRUCTIVE, WORKDIR

HOOKS = {
    "PreUserPromptInput": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "StopGoalGate": [],
    "Stop": [],
}

# 无状态、单次调用级 HITL
def permission_hook(block):
    arguments = block["arguments"]
    if block["name"] == "bash":
        command = str(arguments.get("command", ""))
        for deny in DENY_LIST:
            if deny in command:
                return f"Permission denied: {deny}"
        for destructive in DESTRUCTIVE:
            if destructive in command:
                print("DESTRUCTIVE:", destructive)
        print(f"Tool: bash需要执行{arguments['command']}")
        choice = input("allow? y/n?: ")
        if choice.lower().strip() == "y":
            return None
        return "user denied"
    if block["name"] in {"read", "edit", "write", "glob"}:
        root = Path(arguments.get("cwd") or WORKDIR).resolve()
        requested = Path(arguments.get("path") or ".")
        file_path = (requested if requested.is_absolute() else root / requested).resolve()
        outside_workspace = not root.is_relative_to(WORKDIR.resolve())
        if block["name"] != "glob":
            outside_workspace = outside_workspace or not file_path.is_relative_to(WORKDIR.resolve())
        if outside_workspace:
            print("超出工作区域!")
            print(f"Tool: {block['name']}({arguments})")
            choice = input("allow? y/n")
            if choice.lower().strip() == "y":
                return None
            return "user denied"

    return None


def summary_hook(messages: list):
    count = 0
    for m in messages:
        if m["role"] == "tool":
            count += 1
    return count


def trigger_hook(event: str, *args):
    try:
        for callback in HOOKS[event]:
            result = callback(*args)
            if result is not None:
                return result
    except Exception as e:
        # print(f'触发hook异常: {e}')
        print(f"HOOK ERROR: {type(e).__name__}: {e!r}")
        return f"触发hook异常: {e}"


def registry_hook(event: str, callback):
    HOOKS[event].append(callback)


def hook():
    registry_hook("PreToolUse", permission_hook)
    # registry_hook('PostToolUse', permission_hook)
    registry_hook("StopGoalGate", stop_goal_gate)
    registry_hook("Stop", summary_hook)

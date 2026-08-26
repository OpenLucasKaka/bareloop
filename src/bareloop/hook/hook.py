from .config import HOOKS
from bareloop.config import DENY_LIST, DESTRUCTIVE, WORKDIR


def permission_hook(block):
    arguments = block['arguments']
    for deny in DENY_LIST:
        if deny in block['name']:
            return f"Permission denied: {deny}"
    for d in DESTRUCTIVE:
        if d in block['name']:
            print("DESTRUCTIVE:", d)

            choice = input("allow? y/n?: ")
            return None if choice == "y" else 'user denied'
    if block['name'] == 'bash':
        print(f'Tool: bash需要执行{arguments["command"]}')
        choice = input('allow? y/n?: ')
        if choice.lower().strip() == 'y':
            return None
        return "user denied"
    if block['name'] in ["read", "edit", "write"]:
        file_path = (WORKDIR / arguments['path']).resolve()
        if not file_path.is_relative_to(WORKDIR):
            print('超出工作区域!')
            print(f"Tool: {block['name']}({arguments['path']})")
            choice = input('allow? y/n')
            if choice.lower().strip() == 'y':
                return None
            return "user denied"

    return None


def context_inject_hook(quey):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
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
        print(
            f"HOOK ERROR: {type(e).__name__}: {e!r}"
        )
        return f'触发hook异常: {e}'


def registry_hook(event: str, callback):
    HOOKS[event].append(callback)


def hook():
    registry_hook('PreUserPromptInput', context_inject_hook)
    registry_hook('PreToolUse', permission_hook)
    # registry_hook('PostToolUse', permission_hook)
    registry_hook('Stop', summary_hook)

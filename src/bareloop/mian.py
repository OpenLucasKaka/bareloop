from bareloop.cron_scheduler import agent_lock, runtime_lock, start_cron_scheduler
from bareloop.loop import agent_loop
from bareloop.skills import _scan_skills, list_skills
from bareloop.config import WORKDIR
from bareloop.hook import hook, trigger_hook


def run_agent_turn_locked(user_input: str | None = None):
    if user_input is not None:
        trigger_hook("PreUserPromptInput", user_input)
        messages.append({"role": "user", "content": user_input})
    agent_loop(messages)

hook()

def build_system():
    return f"""
        你是一个 coding agent，工作目录是 {WORKDIR}。

        以下是已经扫描并注册的 Skill：
        {list_skills()}

        你需要自行判断用户请求是否匹配某个 Skill。
        如果决定使用 Skill，必须先调用 load_skill，并传入目录中的精确 Skill name。
        Skill 已经注册，不要使用 glob、read、bash 等工具在文件系统中搜索 Skill。
        获得 load_skill 返回的完整内容后，严格按照 Skill 执行。

        如果工作目录中没有用户需要的普通文件，可以查找工作区之外的目录。
        """



if __name__ == "__main__":
    _scan_skills()
    start_cron_scheduler()
    print('输入问题，回车发送。输入 q 退出。\n')
    messages = [
        {'role': 'system', "content": build_system()}
    ]
    while True:
        try:
            user_input = input("\033[36m请输入>>\033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        quit_list = ['q', 'quit']
        if user_input in quit_list:
            break
        if not user_input:
            continue
        with agent_lock:
            run_agent_turn_locked(user_input)
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.filters import to_filter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from transformers import AutoTokenizer

from bareloop.mode import AgentMode

WORKDIR = Path.cwd().resolve()

load_dotenv()

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL")
CODE_MODEL = os.getenv("CODE_MODEL")
MLX_MODEL = os.getenv("MLX_MODEL")
TOKENIZER_MODEL = os.getenv("TOKENIZER_MODEL")
DEFAUlT_MODEL = AgentMode.NORMAL

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"

client = OpenAI(
    api_key=os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or "bareloop-unconfigured",
    base_url=os.getenv("BASE_URL") or None,
)

CLI_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": "",
        "mode.goal": "fg:ansimagenta bold",
        "hint": "fg:ansibrightblack",
        "placeholder": "fg:ansibrightblack",
    }
)


class LazyTokenizer:
    def __init__(self, model_name: str | None):
        self.model_name = model_name
        self._tokenizer = None

    def _load(self):
        if not self.model_name:
            raise RuntimeError("TOKENIZER_MODEL must be configured before running the agent loop")
        if self._tokenizer is None:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._tokenizer

    def __getattr__(self, name):
        return getattr(self._load(), name)


tokenizer = LazyTokenizer(TOKENIZER_MODEL)

DENY_LIST = ["rm -rf", "reboot", "sudo"]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

SUBAGENT_SYSTEM_PROMPT = f"""
你是一个coding agent, 工作目录是{WORKDIR}
请完成委派给你的任务, 然后返回一个简洁的总结
不可以再向下委派agent
"""

KEY_BINDINGS = KeyBindings()


@KEY_BINDINGS.add("enter")
def submit_input(event):
    event.current_buffer.validate_and_handle()


@KEY_BINDINGS.add("escape", "enter")
def insert_newline_with_escape(event):
    event.current_buffer.insert_text("\n")


@KEY_BINDINGS.add("c-j")
def insert_newline_with_ctrl_j(event):
    event.current_buffer.insert_text("\n")


PROMPT_SESSION = PromptSession(
    multiline=True,
    key_bindings=KEY_BINDINGS,
)
# 输入框只占实际内容高度，多行时再自动增长
PROMPT_SESSION.layout.current_window.dont_extend_height = to_filter(True)

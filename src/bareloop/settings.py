import os
import threading

from dotenv import load_dotenv
from openai import OpenAI
from transformers import AutoTokenizer

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit import PromptSession
from prompt_toolkit.filters import to_filter
from pathlib import Path

WORKDIR = Path.cwd().resolve()

load_dotenv()

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL")
CODE_MODEL = os.getenv("CODE_MODEL")
MLX_MODEL = os.getenv("MLX_MODEL")
TOKENIZER_MODEL = os.getenv("TOKENIZER_MODEL")


RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"

client = OpenAI(
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL"),
)


tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL)

DENY_LIST = ["rm -rf", "reboot", "sudo"]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

SUBAGENT_SYSTEM_PROMPT = f"""
你是一个coding agent, 工作目录是{WORKDIR}
请完成委派给你的任务, 然后返回一个简洁的总结
不可以再向下委派agent
"""

KEY_BINDINGS = KeyBindings()

@KEY_BINDINGS.add('enter')
def submit_input(event):
    event.current_buffer.validate_and_handle()

@KEY_BINDINGS.add("escape", "enter")
def insert_newline(event):
    event.current_buffer.insert_text("\n")

@KEY_BINDINGS.add("c-j")
def insert_newline(event):
    event.current_buffer.insert_text("\n")

PROMPT_SESSION = PromptSession(
    multiline=True,
    key_bindings=KEY_BINDINGS,
)
# 输入框只占实际内容高度，多行时再自动增长
PROMPT_SESSION.layout.current_window.dont_extend_height = to_filter(True)

class CONSOLE_CONTROLLER:

    def __init__(self):
        self._lock = threading.Lock()
        self.reader = None

    def ask(self, promopt: str):
        with self._lock:
            return (self.reader or input())(promopt)


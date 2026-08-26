import os

from dotenv import load_dotenv
from openai import OpenAI
from transformers import AutoTokenizer

from bareloop.settings import WORKDIR

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

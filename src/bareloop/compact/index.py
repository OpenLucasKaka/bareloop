import hashlib
import json
import re
import time

from bareloop.settings import PRIMARY_MODEL, WORKDIR, client

KEEP_RECENT = 50
CONTEXT_LIMIT = 1000
TRANSCRIPT_DIR = WORKDIR / ".bareloop" / ".transcripts"
PERSIST_THRESHOLD = 1000
TOOL_RESULTS_DIR = WORKDIR / ".bareloop" / ".task_outputs" / "tool-results"


def persist_large_output(content, id):
    if len(str(content)) < PERSIST_THRESHOLD:
        return content
    content = str(content)
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_id = str(id)
    safe_id = raw_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", raw_id) else ""
    if not safe_id or safe_id in {".", ".."}:
        digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        safe_id = f"tool_{digest}"
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    path = TOOL_RESULTS_DIR / f"{safe_id}_{content_digest}.txt"
    if not path.exists():
        path.write_text(content, encoding="utf-8")
    prefix = f"<persisted-output>\nFull output: {path}\nPreview:\n"
    suffix = "\n</persisted-output>"
    preview_length = min(256, max(0, len(content) - len(prefix) - len(suffix) - 1))
    return f"{prefix}{content[:preview_length]}{suffix}"


def tool_budget_result(messages, max_bytes=20000):
    recent_tool_indexes = []
    for index in range(len(messages) - 1, -1, -1):
        m = messages[index]
        if m["role"] == "assistant" or m["role"] == "user":
            break
        if m["role"] == "tool":
            recent_tool_indexes.append(index)
    all_bytes = sum(len(str(messages[index]["content"])) for index in recent_tool_indexes)
    if all_bytes <= max_bytes:
        return messages
    ranked = sorted(
        recent_tool_indexes,
        key=lambda index: len(str(messages[index]["content"])),
        reverse=True,
    )
    for index in ranked:
        if all_bytes <= max_bytes:
            break
        message = messages[index]
        if len(str(message["content"])) < PERSIST_THRESHOLD:
            continue
        message["content"] = persist_large_output(message["content"], message["tool_call_id"])
        all_bytes = sum(len(str(messages[i]["content"])) for i in recent_tool_indexes)
    return messages


def micro_compact(messages):
    tool_messages = []
    for m in messages:
        if m["role"] == "tool":
            tool_messages.append(m)
    if len(tool_messages) < KEEP_RECENT:
        return messages
    for m in tool_messages[:-KEEP_RECENT]:
        if len(m["content"]) > 120:
            m["content"] = "[Earlier tool result compacted. Re-run if needed.]"
    return messages


def write_transcript(messages):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f".transcript_{int(time.time())}.jsonl"
    with path.open("w") as f:
        for m in messages:
            f.write(json.dumps(m, default=str) + "\n")
    return path


def summarize_history(messages):
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
        "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n" + conversation
    )
    response = client.chat.completions.create(
        model=PRIMARY_MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=2000
    )
    return response.choices[0].message.content


def compact_history(messages):
    transcript_path = write_transcript(messages)
    print(f"{transcript_path}")
    summarize = summarize_history(messages)
    instructions = [
        message for message in messages if message.get("role") in {"system", "developer"}
    ]
    return [*instructions, {"role": "user", "content": f"[Compacted]\n\n{summarize}"}]

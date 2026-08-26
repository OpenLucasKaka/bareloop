from .config import KEEP_RECENT

def persist_large_output(content, id):
    if len(str(content)) < PERSIST_THRESHOLD: return content
    TOOL_RESULTS_DIR.mkdir(parent=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{id}.txt"
    if not path.exists(): path.write_text(content)
    return f"<persisted-output>\nFull output: {path}\nPreview:\n{content[:2000]}\n</persisted-output>"


def tool_budget_result(messages, max_bytes=20000):
    last_tool_result = []
    for i, m in enumerate(reversed(messages)):
        if m['role'] == 'assistant' or m['role'] == 'user':
            break
        last_tool_result.append({'index': i, 'content': m['content']})
    all_bytes = sum(len(t['content']) for t in last_tool_result)
    if all_bytes <= max_bytes: return messages
    ranked = sorted(last_tool_result, key=lambda k: len(k['content']), reverse=True)
    for _, r in enumerate(ranked):
        if all_bytes <= max_bytes: break
        if len(str(r['content'])) < PERSIST_THRESHOLD: continue
        r['content'] = persist_large_output(r['content'], r['tool_call_id'])
        all_bytes = sum(len(t['content']) for t in last_tool_result)
    return messages


def micro_compact(messages):
    tool_messages = []
    for m in messages:
        if m['role'] == 'tool':
            tool_messages.append(m)
    if len(tool_messages) < KEEP_RECENT: return messages
    for m in tool_messages[:-KEEP_RECENT]:
        if len(m['content']) > 120:
            m['content'] = "[Earlier tool result compacted. Re-run if needed.]"
    return messages


def write_transcript(messages):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f".transcript_{int(time.time())}.jsonl"
    with path.open("w") as f:
        for m in messages: f.write(json.dumps(m, default=str) + "\n")
    return path


def summarize_history(messages):
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = ("Summarize this coding-agent conversation so work can continue.\n"
              "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
              "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n" + conversation)
    response = client.chat.completions.create(
        model=PRIMARY_MODEL,
        messages=[{"role": "user", "content": prompt}], max_tokens=2000
    )
    return response.choices[0].message.content


def compact_history(messages):
    transcript_path = write_transcript(messages)
    print(f'{transcript_path}')
    summarize = summarize_history(messages)
    return [{"role": "user", "content": f"[Compacted]\n\n{summarize}"}]

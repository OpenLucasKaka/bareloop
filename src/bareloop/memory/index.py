import re, json
from bareloop.config import PRIMARY_MODEL, client
from bareloop.utils import _parser_formatter
from bareloop.config import WORKDIR

MEMORY_DIR = WORKDIR / '.bareloop' / ".memory"
MEMORY_DIR.mkdir(exist_ok=True)
MEMORY_INDEX = MEMORY_DIR / 'MEMORY.md'

CONSOLIDATE_THRESHOLD = 10

def list_files():
    result = []
    for m in sorted(MEMORY_DIR.glob("*.md")):
        if m.name == "MEMORY.md":
            continue
        raw = m.read_text()
        meta, body = _parser_formatter(raw)
        result.append({
            "filename": m.name,
            "name": meta.get('name', ''),
            "description": meta.get('description', ''),
            "type": meta.get('type', ''),
            "body": body
        })
    return result


def consolidate_memories():
    """Merge duplicate/stale memories. Triggered when file count ≥ threshold."""
    files = list_files()
    if len(files) < CONSOLIDATE_THRESHOLD:
        return

    catalog = "\n\n".join(
        f"## {f['filename']}\nname: {f['name']}\ndescription: {f['description']}\n{f['body']}"
        for f in files
    )

    prompt = (
        "Consolidate the following memory files. Rules:\n"
        "1. Merge duplicates into one\n"
        "2. Remove outdated/contradicted memories\n"
        "3. Keep the total under 30 memories\n"
        "4. Preserve important user preferences above all\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n\n"
        f"{catalog[:16000]}"
    )

    try:
        response = client.messages.create(
            model=PRIMARY_MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=50000
        )
        text = json.loads(response.content)
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return
        items = json.loads(match.group())

        # Remove old memory files (keep MEMORY.md)
        for f in MEMORY_DIR.glob("*.md"):
            if f.name != "MEMORY.md":
                f.unlink()

        for mem in items:
            name = mem.get("name", f"memory_{int(time.time())}")
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                write_memory_file(name, mem_type, desc, body)

        print(f"\n\033[33m[Memory: consolidated {len(files)} → {len(items)} memories]\033[0m")
    except Exception:
        pass


def extract_memories(messages, count):
    dialogues = []
    for m in messages[count:]:
        if m['role'] == 'user' or m['role'] == 'assistant':
            dialogues.append(f'{m["role"]}: {m["content"]}')
    dialog = '\n'.join(dialogues)
    if not dialog.strip():
        return []
    exist_files = list_files()
    exist_text = '\n'.join(f"- {f['name']}: {f['description']}" for f in exist_files) if exist_files else '(none)'

    prompt = (
        "Extract user preferences, constraints, or project facts from this dialogue.\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n"
        "- name: short kebab-case identifier (e.g. 'user-preference-tabs')\n"
        "- type: one of 'user' (user preference), 'feedback' (guidance), "
        "'project' (project fact), 'reference' (external pointer)\n"
        "- description: one-line summary for index lookup\n"
        "- body: full detail in markdown\n"
        "If nothing new or already covered by existing memories, return [].\n\n"
        f"Existing memories:\n{exist_text}\n\n"
        f"Dialogue:\n{dialog[:4000]}"
    )

    try:
        response = client.chat.completions.create(
            messages=[{"role": "system", "content": prompt}],
            model=PRIMARY_MODEL,
            max_tokens=2000
        )
        message = response.choices[0].message.content.strip()
        if not message.strip():
            return None
        match = re.search(r'\[.*\]', message, re.DOTALL)
        if not match:
            return None
        tems = json.loads(match.group())
        count = 0
        for t in tems:
            name = t['name']
            desc = t['description']
            type = t['type']
            body = t['body']
            if desc and body:
                write_memory_file(name, type, body, desc)
                count += 1
        return f"写入{count}条记忆 "
    except Exception as e:
        pass


def read_memory_index():
    return MEMORY_INDEX.read_text().strip()


def extract_relevant_memories(messages: list, max_items: int = 5):
    memory_files = list_files()
    if not memory_files:
        return []
    relevant_input = []
    for m in reversed(messages):
        if m['role'] == 'user':
            relevant_input.append(m)
        if len(relevant_input) == 3:
            break
    recent_text = ''.join(str(i['content']) for i in relevant_input)
    if not recent_text.strip():
        return []
    catalog_lines = []
    for i, m in enumerate(memory_files):
        catalog_lines.append(f"{i}: {m['name']} {m['description']}")
    text = "\n".join(catalog_lines)

    prompt = (
        "Given the recent conversation and the memory catalog below, "
        "select the indices of memories that are clearly relevant. "
        "Return ONLY a JSON array of integers, e.g. [0, 3]. "
        "If none are relevant, return [].\n\n"
        f"Recent conversation:\n{recent_text}\n\n"
        f"Memory catalog:\n{text}"
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}], model=PRIMARY_MODEL, max_tokens=200
        )
        content = response.choices[0].message.content.strip()
        match = re.search(r'\[[\s\S]*?\]', content)
        if match:
            lines = []
            inlines = json.loads(match.group())
            for i in inlines:
                if isinstance(i, int) and 0 <= i <= len(memory_files):
                    lines.append(memory_files[i])
                if len(lines) >= max_items:
                    break
        return lines
    except Exception as e:
        pass
    return []


def read_memory_file(filename):
    path = MEMORY_DIR / filename
    if not path.exists():
        return None
    return path.read_text()


def load_memories(messages):
    selected_files = extract_relevant_memories(messages)
    if not selected_files: return ''
    parts = ["<relevant_memories>"]
    for f in selected_files:
        content = read_memory_file(f['filename'])
        parts.append(f"{content}")
    return '\n'.join(parts)


def write_memory_file(name, type, body, description):
    slug = name.lower().strip().replace(" ", "-").replace("/", "-")
    file_name = f'{slug}.md'
    file_path = MEMORY_DIR / file_name
    file_path.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {type}\n---\n\n{body}\n"
    )
    _rebuild_index()
    return file_path


def _rebuild_index():
    """Rebuild MEMORY.md index from all memory files."""
    lines = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text()
        meta, body = _parser_formatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description", body.split("\n")[0][:80])
        lines.append(f"- [{name}]({f.name}) — {desc}")
    MEMORY_INDEX.write_text("\n".join(lines) + "\n" if lines else "")

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from bareloop.settings import PRIMARY_MODEL, WORKDIR, client
from bareloop.utils import _parser_formatter

MEMORY_DIR = WORKDIR / ".bareloop" / ".memory"


def _recover_memory_directory(memory_dir: Path) -> None:
    backup = memory_dir.with_name(f"{memory_dir.name}.backup")
    if memory_dir.exists():
        if backup.exists():
            shutil.rmtree(backup)
        return
    if backup.exists():
        os.replace(backup, memory_dir)


_recover_memory_directory(MEMORY_DIR)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

CONSOLIDATE_THRESHOLD = 10
MEMORY_TYPES = frozenset({"user", "feedback", "project", "reference"})


def _memory_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower().strip()).strip("-.")
    if not slug:
        raise ValueError("memory name must contain letters or numbers")
    return slug


def _memory_document(name: str, memory_type: str, body: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\ntype: {memory_type}\n---\n\n{body}\n"


def _validated_memory_items(items) -> list[dict[str, str]]:
    if not isinstance(items, list) or not items:
        raise ValueError("consolidation must return a non-empty JSON array")
    if len(items) > 30:
        raise ValueError("consolidation must return at most 30 memories")
    validated: list[dict[str, str]] = []
    filenames: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each consolidated memory must be an object")
        values = {field: item.get(field) for field in ("name", "type", "description", "body")}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise ValueError("consolidated memory fields must be non-empty strings")
        if values["type"] not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {values['type']}")
        filename = f"{_memory_slug(values['name'])}.md"
        if filename in filenames:
            raise ValueError(f"duplicate consolidated memory filename: {filename}")
        filenames.add(filename)
        validated.append({**values, "filename": filename})
    return validated


def _replace_memory_set(items: list[dict[str, str]]) -> None:
    MEMORY_DIR.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{MEMORY_DIR.name}.staging-", dir=MEMORY_DIR.parent))
    backup = MEMORY_DIR.with_name(f"{MEMORY_DIR.name}.backup")
    try:
        index_lines = []
        for item in items:
            (staging / item["filename"]).write_text(
                _memory_document(item["name"], item["type"], item["body"], item["description"]),
                encoding="utf-8",
            )
            index_lines.append(f"- [{item['name']}]({item['filename']}) — {item['description']}")
        (staging / "MEMORY.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

        if backup.exists():
            shutil.rmtree(backup)
        had_existing = MEMORY_DIR.exists()
        if had_existing:
            os.replace(MEMORY_DIR, backup)
        try:
            os.replace(staging, MEMORY_DIR)
        except Exception:
            if had_existing and backup.exists():
                os.replace(backup, MEMORY_DIR)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def list_files():
    result = []
    for m in sorted(MEMORY_DIR.glob("*.md")):
        if m.name == "MEMORY.md":
            continue
        raw = m.read_text()
        meta, body = _parser_formatter(raw)
        result.append(
            {
                "filename": m.name,
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "type": meta.get("type", ""),
                "body": body,
            }
        )
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
        response = client.chat.completions.create(
            model=PRIMARY_MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=50000
        )
        text = response.choices[0].message.content or ""
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return
        items = _validated_memory_items(json.loads(match.group()))
        _replace_memory_set(items)

        print(f"\n\033[33m[Memory: consolidated {len(files)} → {len(items)} memories]\033[0m")
    except Exception as error:
        print(f"[memory] consolidation failed: {error}", file=sys.stderr)


def extract_memories(messages, count):
    dialogues = []
    for m in messages[count:]:
        if m["role"] == "user" or m["role"] == "assistant":
            dialogues.append(f"{m['role']}: {m['content']}")
    dialog = "\n".join(dialogues)
    if not dialog.strip():
        return []
    exist_files = list_files()
    exist_text = (
        "\n".join(f"- {f['name']}: {f['description']}" for f in exist_files)
        if exist_files
        else "(none)"
    )

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
            messages=[{"role": "system", "content": prompt}], model=PRIMARY_MODEL, max_tokens=2000
        )
        message = response.choices[0].message.content.strip()
        if not message.strip():
            return None
        match = re.search(r"\[.*\]", message, re.DOTALL)
        if not match:
            return None
        tems = json.loads(match.group())
        count = 0
        for t in tems:
            name = t["name"]
            desc = t["description"]
            type = t["type"]
            body = t["body"]
            if desc and body:
                write_memory_file(name, type, body, desc)
                count += 1
        return f"写入{count}条记忆 "
    except Exception as error:
        print(f"[memory] extraction failed: {error}", file=sys.stderr)
        return None


def read_memory_index():
    return MEMORY_INDEX.read_text().strip()


def extract_relevant_memories(messages: list, max_items: int = 5):
    memory_files = list_files()
    if not memory_files:
        return []
    relevant_input = []
    for m in reversed(messages):
        if m["role"] == "user":
            relevant_input.append(m)
        if len(relevant_input) == 3:
            break
    recent_text = "".join(str(i["content"]) for i in relevant_input)
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
        match = re.search(r"\[[\s\S]*?\]", content)
        if match:
            lines = []
            inlines = json.loads(match.group())
            for i in inlines:
                if isinstance(i, int) and 0 <= i < len(memory_files):
                    lines.append(memory_files[i])
                if len(lines) >= max_items:
                    break
        return lines
    except Exception as error:
        print(f"[memory] selection failed: {error}", file=sys.stderr)
    return []


def read_memory_file(filename):
    path = MEMORY_DIR / filename
    if not path.exists():
        return None
    return path.read_text()


def load_memories(messages):
    selected_files = extract_relevant_memories(messages)
    if not selected_files:
        return ""
    parts = ["<relevant_memories>"]
    for f in selected_files:
        content = read_memory_file(f["filename"])
        parts.append(f"{content}")
    return "\n".join(parts)


def write_memory_file(name, type, body, description):
    slug = _memory_slug(name)
    file_name = f"{slug}.md"
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    file_path = MEMORY_DIR / file_name
    file_path.write_text(
        _memory_document(name, type, body, description),
        encoding="utf-8",
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

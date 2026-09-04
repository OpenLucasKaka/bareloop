from bareloop.settings import WORKDIR
from bareloop.utils import _parser_formatter

SKILLS_DIR = WORKDIR / ".bareloop" / "skills"
SKILL_REGISTRY = {}


def _scan_skills():
    if not SKILLS_DIR.exists():
        return
    for skill in sorted(SKILLS_DIR.iterdir()):
        if not skill.is_dir():
            continue
        if (skill / "SKILL.md").exists():
            raw = (skill / "SKILL.md").read_text()
            meta, _body = _parser_formatter(raw)
            name = meta.get("name", skill.name)
            description = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
            SKILL_REGISTRY[name] = {"name": name, "description": description, "content": raw}


def list_skills():
    if not SKILL_REGISTRY:
        return "no skills registered"
    return "\n".join(f"{skill['name']}:{skill['description']}" for skill in SKILL_REGISTRY.values())

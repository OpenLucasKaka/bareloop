def load_skill(name: str) -> str:
    from bareloop.skills import SKILL_REGISTRY

    skill = SKILL_REGISTRY.get(name)
    return skill["content"] if skill else f"Error: skill '{name}' not found"

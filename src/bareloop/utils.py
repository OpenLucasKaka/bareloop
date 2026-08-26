import yaml, json



def _parser_formatter(raw):
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def normalize_tool_call(tool):
    return {
        "id": tool.id,
        "name": tool.function.name,
        "arguments": json.loads(tool.function.arguments),
    }


def _format_bash_result(output, exit_code):
    if exit_code in [0, None]:
        return output
    return f"Error: 后台任务执行失败{exit_code}"

from bareloop.cron_scheduler import cancel_job, cron_lock, schedule_cron, scheduled_jobs


def run_cron_scheduler(cron: str, prompt: str, recurring: bool) -> str:
    result = schedule_cron(cron, prompt, recurring)
    if isinstance(result, str):
        return f"Error: {result}"
    return f"Scheduled {result.id}: {cron} -> {prompt}"


def run_cron_list() -> str:
    with cron_lock:
        jobs = list(scheduled_jobs.values())
    if not jobs:
        return "No cron jobs."
    lines = []
    for job in jobs:
        frequency = "recurring" if job.recurring else "one-shot"
        lines.append(f"{job.id}: {job.cron} -> {job.prompt[:60]} [{frequency}]")
    return "\n".join(lines)


def run_cancel_cron(job_id: str) -> str:
    return cancel_job(job_id)

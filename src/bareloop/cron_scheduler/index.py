import json
import os
import secrets
from dataclasses import asdict
from datetime import datetime
import threading
from bareloop.settings import WORKDIR
from dataclasses import dataclass


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool
    pending_delivery: bool = False
    last_fired: str | None = None

cron_start_flag = False
STOP_CRON = threading.Event()
cron_runtime_list: list[threading.Thread] = []
runtime_lock = threading.RLock()
DURABLE_CRON_PATH = WORKDIR / '.bareloop' / ".schedule_task.json"
cron_lock = threading.RLock()
agent_lock = threading.Lock()
scheduled_jobs: dict[str, CronJob] = {}
cron_queue: list[CronJob] = []

def _validate_cron_field(field: str, minimum: int, maximum: int) -> str | None:
    if field == "*":
        return None
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit() or int(step) <= 0:
            return f"Invalid step: {field}"
        return None
    if "," in field:
        for part in field.split(","):
            error = _validate_cron_field(part.strip(), minimum, maximum)
            if error:
                return error
        return None
    if "-" in field:
        start, end = field.split("-", 1)
        if not start.isdigit() or not end.isdigit():
            return f"Invalid range: {field}"
        start_value, end_value = int(start), int(end)
        if start_value > end_value:
            return f"Range start is greater than end: {field}"
        if start_value < minimum or end_value > maximum:
            return f"Range {field} is outside [{minimum}-{maximum}]"
        return None
    if not field.isdigit():
        return f"Invalid field: {field}"
    value = int(field)
    if value < minimum or value > maximum:
        return f"Value {value} is outside [{minimum}-{maximum}]"
    return None


def validate_cron(cron):
    fields = cron.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields, got {len(fields)}"
    field_rules = [
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("day-of-month", 1, 31),
        ("month", 1, 12),
        ("day-of-week", 0, 6),
    ]
    for field, (name, min, max) in zip(fields, field_rules):
        error = _validate_cron_field(field, min, max)
        if error:
            return f"Error: {error}"
    return None


def load_durable_cron():
    if not DURABLE_CRON_PATH.exists():
        print(f'[cron]: {DURABLE_CRON_PATH} is not found')
        return
    try:
        payload = json.loads(DURABLE_CRON_PATH.read_text())
        if not isinstance(payload, list):
            raise ValueError("expected a list")
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"解析{DURABLE_CRON_PATH}失败")

    loaded = 0
    with cron_lock:
        for item in payload:
            try:
                job = CronJob(**item)
                error = validate_cron(job.cron)
                if error:
                    raise ValueError(f"Error: {error}")
                if not job.id.startswith("cron_"):
                    raise ValueError(f"Error: cron任务的id格式错误")
                if not job.prompt.strip():
                    raise ValueError("prompt cannot be empty")
            except Exception as error:
                raise RuntimeError(f"Error: {error}")
            scheduled_jobs[job.id] = job
            if job.pending_delivery:
                cron_queue.append(job)
            loaded += 1
        print(f'[cron]: 已加载 {loaded} cronjobs')


def _cron_field_matches(field, value: int):
    if field == '*':
        return True
    if field.startWith('*/'):
        return value % int(field[2:]) == 0
    if '-' in field:
        start, end = field.split('-')
        return int(start) <= value <= int(end)
    if ',' in field:
        return any(_cron_field_matches(t.strip(), value) for t in field.split(','))
    return value == int(field)


def cron_matches(cron_expr, moment):
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    cron_weekday = (moment.weekday() + 1) % 7
    minute, hour, day, month, weekday = fields
    if not (
            _cron_field_matches(minute, moment.minute)
            and _cron_field_matches(hour, moment.hour)
            and _cron_field_matches(day, moment.day)
    ):
        return False

    day_matches = _cron_field_matches(day, moment.day)
    weekday_matches = _cron_field_matches(weekday, cron_weekday)

    if day == "*" and weekday == "*":
        return True
    if day == "*":
        return weekday_matches
    if weekday == "*":
        return day_matches
    return day_matches or weekday_matches


def _enqueue_due_job(job, time_maker):
    old_pending = job.pending_delivery
    old_last_fired = job.last_fired
    job.pending_delivery = True
    if time_maker is not None:
        job.last_fired = time_maker
    try:
        save_cron_durable()
    except Exception:
        job.pending_delivery = old_pending
        job.last_fired = old_last_fired
        raise
    cron_queue.append(job)


def poll_due_jobs(moment: datetime):
    time_maker = moment.strftime("%Y-%m-%d %H:%M")
    with cron_lock:
        for cron in list(scheduled_jobs.values()):
            try:
                if cron.pending_delivery or cron.last_fired == time_maker:
                    continue
                if cron_matches(cron.cron, moment):
                    _enqueue_due_job(cron, time_maker)
            except Exception as error:
                print('\033[33m[ERROR]\033[0m', error)


def cron_scheduler_loop(stop_cron: threading.Event = STOP_CRON):
    while not stop_cron.wait(1.0):
        poll_due_jobs(datetime.now())


def has_cron_queue():
    with cron_lock:
        return bool(cron_queue)


def queue_processor_loop(stop_event: threading.Event = STOP_CRON):
    while not stop_event.wait(0.2):
        if not has_cron_queue() or agent_lock.acquire(blocking=False):
            continue
        try:
            if has_cron_queue():
                from bareloop.mian import run_agent_turn_locked

                run_agent_turn_locked()
        finally:
            agent_lock.release()


def save_cron_durable():
    with cron_lock:
        payload = [
            asdict(job)
            for job in scheduled_jobs.values()
        ]
        temporary = DURABLE_CRON_PATH.with_name(
            f"{DURABLE_CRON_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(payload, indent=2))
            os.replace(temporary, DURABLE_CRON_PATH)
        finally:
            temporary.unlink(missing_ok=True)


def start_cron_scheduler():
    global cron_start_flag
    with runtime_lock:
        if cron_start_flag:
            return
        load_durable_cron()
        cron_runtime_list.extend([
            threading.Thread(
                target=cron_scheduler_loop,
                name="cron_scheduler_loop",
                daemon=True
            ),
            threading.Thread(
                target=queue_processor_loop,
                name="queue_processor_loop",
                daemon=True
            )
        ])
        for thread in cron_runtime_list:
            thread.start()
        cron_start_flag = True

def acknowledge_cron_jobs(jobs: list[CronJob]):
    changed: list[tuple[CronJob, bool]] = []
    removed: list[CronJob] = []
    with cron_lock:
        for delivered in jobs:
            current = scheduled_jobs.get(delivered.id)
            if current is None:
                continue
            changed.append((current, current.pending_delivery))
            if current.recurring:
                current.pending_delivery = False
            else:
                removed.append(current)
                scheduled_jobs.pop(current.id)

        try:
            save_cron_durable()
        except Exception:
            for job in removed:
                scheduled_jobs[job.id] = job
            for job, pending in changed:
                job.pending_delivery = pending
            queued_ids = {job.id for job in cron_queue}
            for job, _ in changed:
                if job.id not in queued_ids:
                    cron_queue.append(job)
            raise


def restore_cron_jobs(jobs: list[CronJob]):
    with cron_lock:
        queued_ids = {job.id for job in cron_queue}
        for delivered in jobs:
            current = scheduled_jobs.get(delivered.id)
            if current is None:
                continue
            current.pending_delivery = True
            if current.id not in queued_ids:
                cron_queue.append(current)
                queued_ids.add(current.id)


def consume_cron_queue() -> list[CronJob]:
    with cron_lock:
        jobs = list(cron_queue)
        cron_queue.clear()
    return jobs



def new_cron_id() -> str:
    for _ in range(100):
        job_id = f"cron_{secrets.token_hex(4)}"
        if job_id not in scheduled_jobs:
            return job_id
    raise RuntimeError("Could not allocate a cron job ID")


def schedule_cron(cron: str, prompt: str, recurring: bool):
    error = validate_cron(cron)
    if error:
        return error
    if not prompt.strip():
        return "Prompt cannot be empty"
    with cron_lock:
        job = CronJob(
            id=new_cron_id(),
            cron=cron,
            prompt=prompt,
            recurring=recurring
        )
        scheduled_jobs[job.id] = job
        try:
            save_cron_durable()
        except Exception:
            scheduled_jobs.pop(job.id, None)
            raise
    print(f"  [cron] scheduled {job.id}: {cron} -> {prompt[:60]}")
    return job


def cancel_job(job_id):
    with cron_lock:
        job = scheduled_jobs.get(job_id)
        if job is None:
            return f"Job {job_id} not found"

        previous_queue = list(cron_queue)
        scheduled_jobs.pop(job_id)
        cron_queue[:] = [queued for queued in cron_queue if queued.id != job_id]
        try:
            if job.durable:
                save_durable_jobs()
        except Exception:
            scheduled_jobs[job_id] = job
            cron_queue[:] = previous_queue
            raise
    print(f"  [cron] cancelled {job_id}")
    return f"Cancelled {job_id}"

def stop_runtime_threads():
    global runtime_started
    with runtime_lock:
        if not runtime_started:
            return
        STOP_CRON.set()
        for thread in cron_runtime_list:
            thread.join(timeout=1)
        cron_runtime_list.clear()
        runtime_started = False


def save_durable_jobs():
    with cron_lock:
        payload = [
            asdict(job)
            for job in scheduled_jobs.values()
            if job.durable
        ]
        temporary = DURABLE_CRON_PATH.with_name(
            f"{DURABLE_CRON_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(payload, indent=2))
            os.replace(temporary, DURABLE_CRON_PATH)
        finally:
            temporary.unlink(missing_ok=True)


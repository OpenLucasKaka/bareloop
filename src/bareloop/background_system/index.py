import threading

from bareloop.tools.shell import format_shell_result, run_shell_process


class BackgroundManager:
    def __init__(self):
        self.task = {}
        self._results = {}
        self._ready = []
        self._counter = 0
        self.lock = threading.Lock()

    def start_task(self, block):
        with self.lock:
            task_id = f"task_{self._counter}os"
            task = {"tool_use_id": block["id"], "command": block["command"], "status": "running"}
            self.task[task_id] = task
            self._counter += 1

        thread = threading.Thread(target=self._run, args=(task_id, block["command"]), daemon=True)

        try:
            thread.start()
        except Exception:
            with self.lock:
                self.task.pop(task_id, None)
            raise
        print(f"  [background] started {task_id}: {block['command'][:60]}")
        return task_id

    def _run(self, task_id, command):
        try:
            output, exit_code = run_shell_process(command)
            result = format_shell_result(output, exit_code)
            status = "completed" if exit_code == 0 else "failed"
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e}"
            status = "failed"

        with self.lock:
            task = self.task[task_id]
            if task:
                task["status"] = status
                self._results[task_id] = result
                self._ready.append(task_id)

    def collect(self):
        ready = []
        with self.lock:
            for task_id in self._ready:
                task = self.task.pop(task_id, None)
                result = self._results.pop(task_id, None)
                if task and result:
                    ready.append((task_id, task, result))
        self._ready.clear()
        notifications = []
        for task_id, task, result in ready:
            notifications.append(
                f"<task_notification>\n"
                f"  <task_id>{task_id}</task_id>\n"
                f"  <status>{task['status']}</status>\n"
                f"  <command>{task['command']}</command>\n"
                f"  <summary>{result[:500]}</summary>\n"
                f"</task_notification>"
            )
            print(f"  [background] collected {task_id}: {task['status']}")
        return notifications


BACKTASK = BackgroundManager()


def start_background_task(block):
    return BACKTASK.start_task(block)


def collect_background_results():
    return BACKTASK.collect()


def inject_background_results(messages):
    notifications = collect_background_results()
    if not notifications:
        return None
    messages.append(
        {
            "role": "user",
            "content": "\n".join(notifications),
        }
    )

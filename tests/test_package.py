import os
import subprocess
import sys
from importlib import import_module, util


def test_package_exposes_version() -> None:
    spec = util.find_spec("bareloop")

    assert spec is not None, "bareloop package is not implemented"

    package = import_module("bareloop")
    assert package.__version__ == "0.1.0"


def test_task_modules_import_without_local_model_configuration(tmp_path) -> None:
    environment = os.environ.copy()
    for name in (
        "API_KEY",
        "BASE_URL",
        "PRIMARY_MODEL",
        "FALLBACK_MODEL",
        "CODE_MODEL",
        "MLX_MODEL",
        "TOKENIZER_MODEL",
    ):
        environment.pop(name, None)
    environment["HF_HOME"] = str(tmp_path / "hf-home")

    result = subprocess.run(
        [sys.executable, "-c", "import bareloop.task_system"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr

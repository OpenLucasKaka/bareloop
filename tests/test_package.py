from importlib import import_module, util


def test_package_exposes_version() -> None:
    spec = util.find_spec("bareloop")

    assert spec is not None, "bareloop package is not implemented"

    package = import_module("bareloop")
    assert package.__version__ == "0.1.0"


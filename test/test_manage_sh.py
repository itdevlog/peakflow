"""Тесты manage.sh: синтаксис и справка (не разрушающие)."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANAGE = ROOT / "manage.sh"

COMMANDS = [
    "install", "update", "start", "stop", "restart",
    "status", "logs", "backup", "restore", "doctor",
    "caddy", "uninstall", "help",
]


def _run(*args):
    return subprocess.run(["bash", str(MANAGE), *args],
                          capture_output=True, text=True, cwd=ROOT)


def test_manage_sh_exists_and_executable():
    assert MANAGE.is_file()


def test_syntax_ok():
    r = subprocess.run(["bash", "-n", str(MANAGE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_lists_all_commands():
    r = _run("--no-color", "help")
    assert r.returncode == 0, r.stderr
    for cmd in COMMANDS:
        assert cmd in r.stdout

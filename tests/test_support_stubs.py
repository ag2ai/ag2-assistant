"""The stub helper must produce a real, runnable file on disk."""

import shutil
import subprocess

from tests.support.stubs import write_stub


def test_stub_is_a_real_executable_resolvable_by_which(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = write_stub(bin_dir / "my-tool", stdout="hello")
    assert shutil.which("my-tool", path=str(bin_dir)) == str(stub)


def test_stub_actually_runs_and_reports_its_scripted_result(tmp_path):
    stub = write_stub(tmp_path / "t", stdout="out", stderr="err", exit_code=3)
    done = subprocess.run([str(stub)], capture_output=True, text=True)
    assert done.stdout.strip() == "out"
    assert done.stderr.strip() == "err"
    assert done.returncode == 3


def test_stub_creates_missing_parent_directories(tmp_path):
    stub = write_stub(tmp_path / "deep" / "bin" / "tool")
    assert stub.is_file()
    assert subprocess.run([str(stub)]).returncode == 0


def test_stub_output_is_not_shell_interpreted(tmp_path):
    """Quoting keeps scripted output literal, even with shell metacharacters."""
    stub = write_stub(tmp_path / "t", stdout="$HOME 'x' && echo no")
    done = subprocess.run([str(stub)], capture_output=True, text=True)
    assert done.stdout.strip() == "$HOME 'x' && echo no"

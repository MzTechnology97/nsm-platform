from pathlib import Path
import subprocess


def main():
    repo_root = Path(__file__).resolve().parents[2]
    test_script = repo_root / "scripts" / "test-update-source-guard.sh"
    result = subprocess.run(
        ["bash", str(test_script)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
    assert "runtime source guard smoke test passed" in result.stdout
    print("update.sh runtime-source regression smoke test passed")


if __name__ == "__main__":
    main()

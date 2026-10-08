"""manage.sh launched from the Git clone acts on the installation directory."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        clone, runtime = Path(tmp) / "clone", Path(tmp) / "runtime"
        clone.mkdir()
        (runtime / "secrets").mkdir(parents=True)
        (runtime / "secrets" / "bootstrap.env").write_text("X=1\n", encoding="utf-8")
        shutil.copy(ROOT / "manage.sh", clone / "manage.sh")
        env = {**os.environ, "PLATFORM_DIR": str(runtime)}
        result = subprocess.run(["bash", "manage.sh", "help"], cwd=clone, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "uso l'installazione in" in result.stderr, result.stderr
        # From the installation itself nothing changes.
        shutil.copy(ROOT / "manage.sh", runtime / "manage.sh")
        direct = subprocess.run(["bash", "manage.sh", "help"], cwd=runtime, env=env, capture_output=True, text=True)
        assert direct.returncode == 0 and "uso l'installazione" not in direct.stderr
    print("manage.sh runtime directory smoke passed")


if __name__ == "__main__":
    main()

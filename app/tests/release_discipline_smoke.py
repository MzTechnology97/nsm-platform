"""Release and versioning discipline: version, changelog and migration chain stay consistent."""
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.entrypoint import APP_VERSION
from app.mikrotik_agent_generation import TARGET_AGENT_VERSION

ROOT = Path(__file__).resolve().parents[2]
HEADING = re.compile(r"^## (0\.49\.(\d+))(?: — (\d{4}-\d{2}-\d{2}))?\s*$", re.M)


def main():
    changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = list(HEADING.finditer(changelog))
    assert headings, "CHANGELOG has 0.49.x entries"
    assert headings[0].group(1) == APP_VERSION, f"APP_VERSION {APP_VERSION} must be the first CHANGELOG entry ({headings[0].group(1)})"
    patches = [int(h.group(2)) for h in headings]
    assert len(patches) == len(set(patches)), "every release appears once"
    assert patches == list(range(patches[0], patches[0] - len(patches), -1)), f"releases are consecutive and newest first: {patches[:8]}"
    for current, following in zip(headings, headings[1:] + [None]):
        body = changelog[current.end():following.start() if following else len(changelog)]
        assert body.strip(), f"{current.group(1)} has no description"
    assert all(h.group(3) for h in headings[:-1]), "dated releases"

    assert re.fullmatch(r"0\.49\.\d+", TARGET_AGENT_VERSION), TARGET_AGENT_VERSION
    assert f"agent {TARGET_AGENT_VERSION}".lower() in changelog.lower() or f"Agent {TARGET_AGENT_VERSION}" in changelog, \
        "the current MikroTik agent version is documented in the CHANGELOG"

    config = Config(str(ROOT / "app" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "app" / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"a single migration head: {heads}"
    chain = list(script.walk_revisions())
    numbers = [int(rev.revision.split("_")[0]) for rev in chain]
    assert numbers == list(range(len(chain), 0, -1)), f"migrations are numbered 0001..{len(chain):04d} without gaps"
    for rev in chain:
        prefix = rev.revision.split("_")[0]
        assert Path(rev.path).name.startswith(prefix + "_"), (rev.revision, rev.path)
        assert rev.module.downgrade is not None, f"{rev.revision} has a downgrade"
    print(f"Release discipline smoke passed ({APP_VERSION}, {len(chain)} migrations, agent {TARGET_AGENT_VERSION})")


if __name__ == "__main__":
    main()

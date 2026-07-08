"""Reference snapshot loading for BDR hook generation."""

from __future__ import annotations

from pathlib import Path

LEGACY_POSITIONING_SNAPSHOT = "legacy_positioning_guide.md"
POSITIONING_GUIDE_SNAPSHOT = "outreach_positioning_guide.md"
BDR_STYLE_PROFILES_SNAPSHOT = "bdr_style_profiles_snapshot.md"

REQUIRED_REFERENCE_SNAPSHOTS = (
    LEGACY_POSITIONING_SNAPSHOT,
    POSITIONING_GUIDE_SNAPSHOT,
    BDR_STYLE_PROFILES_SNAPSHOT,
)
BDR_AGENT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = BDR_AGENT_ROOT / "references"
LEGACY_REFERENCE_DIR = BDR_AGENT_ROOT / "legacy" / "skills" / "write-hook" / "references"
REFERENCE_DIR = REFERENCE_ROOT

CANONICAL_REFERENCE_PATHS = {
    LEGACY_POSITIONING_SNAPSHOT: (
        REFERENCE_ROOT / "outreach_positioning" / LEGACY_POSITIONING_SNAPSHOT
    ),
    POSITIONING_GUIDE_SNAPSHOT: (
        REFERENCE_ROOT / POSITIONING_GUIDE_SNAPSHOT
    ),
    BDR_STYLE_PROFILES_SNAPSHOT: (
        REFERENCE_ROOT / "outreach_style" / BDR_STYLE_PROFILES_SNAPSHOT
    ),
}

LEGACY_REFERENCE_PATHS = {
    filename: LEGACY_REFERENCE_DIR / filename
    for filename in REQUIRED_REFERENCE_SNAPSHOTS
}


def reference_snapshot_path(filename: str) -> Path:
    if filename not in REQUIRED_REFERENCE_SNAPSHOTS:
        raise ValueError(f"Unknown reference snapshot: {filename}")
    canonical_path = CANONICAL_REFERENCE_PATHS[filename]
    if canonical_path.exists():
        return canonical_path
    legacy_path = LEGACY_REFERENCE_PATHS[filename]
    if legacy_path.exists():
        return legacy_path
    return canonical_path


def load_reference_snapshot(filename: str) -> str:
    return reference_snapshot_path(filename).read_text()


def load_positioning_guide_snapshot() -> str:
    return load_reference_snapshot(POSITIONING_GUIDE_SNAPSHOT)


def load_style_profiles_snapshot() -> str:
    return load_reference_snapshot(BDR_STYLE_PROFILES_SNAPSHOT)


def load_required_reference_snapshots() -> dict[str, str]:
    return {
        filename: load_reference_snapshot(filename)
        for filename in REQUIRED_REFERENCE_SNAPSHOTS
    }

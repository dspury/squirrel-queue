"""Validation module. Binary pass/fail check of execution results.

Three layers:
1. Lane results — did the agent report success?
2. Verify commands — run shell scripts that check specific criteria.
3. Filesystem heuristics — did the expected changes actually happen?

Validation NEVER trusts self-reported success alone.

Criteria format:
  Plain string:        "game.py file exists"
  With verify command:  "12x12 grid :: python main.py tick | wc -l | grep -q 12"

The text before ' :: ' is the description. The text after is a shell
command run in cwd. Exit code 0 = pass, non-zero = fail.
"""

import re
import subprocess
from pathlib import Path

VERIFY_SEPARATOR = " :: "
VERIFY_TIMEOUT = 30


def parse_criterion(raw: str) -> tuple[str, str | None]:
    """Split a criterion into (description, verify_command_or_None)."""
    if VERIFY_SEPARATOR in raw:
        desc, cmd = raw.split(VERIFY_SEPARATOR, 1)
        return desc.strip(), cmd.strip()
    return raw.strip(), None


def check(task: dict, lane_results: list[dict], cwd: str = None) -> tuple[bool, str, list]:
    """Validate execution results against task success_criteria.

    Returns (passed: bool, notes: str, details: list).
    details is a list of (criterion, passed, note) tuples for partial retry.
    """
    criteria = task.get("success_criteria", [])
    if not criteria:
        return False, "No success_criteria defined. Cannot validate.", []

    # Layer 1: Did lanes report failure?
    failed_packets = [r for r in lane_results if not r["success"]]
    if failed_packets:
        ids = [r["packet_id"] for r in failed_packets]
        return False, f"Lane execution failed for packets: {', '.join(ids)}", []

    # Layer 2+3: Check each criterion
    resolve_from = Path(cwd) if cwd else Path.cwd()
    results = []
    for raw_criterion in criteria:
        desc, verify_cmd = parse_criterion(raw_criterion)
        if verify_cmd:
            passed, note = _run_verify(desc, verify_cmd, resolve_from)
        else:
            passed, note = _check_criterion(desc, resolve_from)
        results.append((raw_criterion, passed, note))

    failed = [(c, n) for c, p, n in results if not p]

    if failed:
        details = "; ".join(f"[FAIL] {c}: {n}" for c, n in failed)
        return False, f"{len(failed)}/{len(criteria)} criteria failed. {details}", results

    return True, f"All {len(criteria)} criteria verified.", results


def _run_verify(description: str, command: str, cwd: Path) -> tuple[bool, str]:
    """Run a verify shell command. Exit 0 = pass."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
        )
        if result.returncode == 0:
            return True, f"Verify passed: {description}"
        else:
            stderr = result.stderr.strip()[:200]
            stdout = result.stdout.strip()[:200]
            detail = stderr or stdout or f"exit code {result.returncode}"
            return False, f"Verify failed: {detail}"
    except subprocess.TimeoutExpired:
        return False, f"Verify timed out after {VERIFY_TIMEOUT}s"
    except Exception as e:
        return False, f"Verify error: {e}"


def _check_criterion(criterion: str, cwd: Path) -> tuple[bool, str]:
    """Attempt to verify a single criterion against the filesystem.

    Uses heuristic pattern matching on the criterion text to determine
    what to check. If no heuristic matches, falls back to fail-safe.
    """
    text = criterion.lower().strip()

    # Pattern: file/directory existence
    file_match = _extract_file_path(criterion)
    if file_match:
        target = cwd / file_match
        if target.exists():
            return True, f"File exists: {file_match}"
        else:
            return False, f"File not found: {target}"

    # Pattern: file contains content
    if "includes" in text or "contains" in text:
        return _check_content_criterion(criterion, cwd)

    # Fallback: cannot verify programmatically — fail safe
    return False, f"UNVERIFIABLE: Cannot verify programmatically. Add a verify command with ' :: ' separator."


def _extract_file_path(criterion: str) -> str | None:
    """Try to extract a file path from a criterion string."""
    _LOCATION_PHRASES = [
        "at project root", "at the project root", "at root",
        "at the root", "in project root", "in the project root",
    ]

    cleaned = criterion
    for phrase in _LOCATION_PHRASES:
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)

    # Look for quoted or backticked paths first (most explicit)
    quoted = re.search(r'[`"\']([^`"\']+(?:\.\w+|/))[`"\']', criterion)
    if quoted:
        return quoted.group(1)

    # Look for "exists at <path>" pattern (after stripping location phrases)
    at_match = re.search(r'exists\s+at\s+(\S+)', cleaned, re.IGNORECASE)
    if at_match:
        path = at_match.group(1).strip(".,;")
        if path and ("/" in path or "." in path):
            return path

    # Look for "<filename> file exists"
    name_file_exists = re.search(r'((?:\S+/)?\S*\.[\w]+)\s+file\s+exists', cleaned, re.IGNORECASE)
    if name_file_exists:
        return name_file_exists.group(1).strip(",;")

    # Look for "File <path> exists"
    file_exists = re.search(r'file\s+((?:\S+/)?\S*\.[\w]+)\s+exists', cleaned, re.IGNORECASE)
    if file_exists:
        return file_exists.group(1).strip(",;")

    # Look for "<dirname>/ exists" or "<dirname> directory exists"
    dir_match = re.search(r'(\S+)/?\s+(?:directory\s+)?exists', cleaned, re.IGNORECASE)
    if dir_match:
        path = dir_match.group(1).strip(".,;")
        if path and not path.startswith("("):
            return path

    return None


def _check_content_criterion(criterion: str, cwd: Path) -> tuple[bool, str]:
    """Check if recently modified files contain expected content."""
    text = criterion.strip()

    content_part = re.sub(r'^(includes|contains)\s+', '', text, flags=re.IGNORECASE)

    items = re.split(r'[,]\s*|\s+and\s+', content_part)
    items = [i.strip().strip(".,;`\"'") for i in items if i.strip()]

    if not items:
        return True, "No content items to verify."

    candidates = []
    for p in cwd.iterdir():
        if p.is_file() and not p.name.startswith("."):
            candidates.append(p)
    for item in items:
        p = cwd / item
        if p.is_file():
            candidates.append(p)

    if not candidates:
        try:
            result = subprocess.run(
                ["find", str(cwd), "-maxdepth", "1", "-type", "f", "-newer",
                 str(cwd), "-name", ".*"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if line:
                    candidates.append(Path(line))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    for candidate in candidates:
        try:
            content = candidate.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        missing = [item for item in items if item not in content]
        if not missing:
            return True, f"All items found in {candidate.name}"

    all_content = ""
    for p in cwd.iterdir():
        if p.is_file():
            try:
                all_content += p.read_text()
            except (OSError, UnicodeDecodeError):
                continue

    missing = [item for item in items if item not in all_content]
    if not missing:
        return True, f"All items found across project files."

    return False, f"Missing content: {', '.join(missing)}"

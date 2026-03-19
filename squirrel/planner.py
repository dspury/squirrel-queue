"""Planner module. Decides whether to send a task to one agent or split
across multiple agents.

v1 strategy:
  - Default: single packet with the full objective and all criteria.
    One agent does the whole job. This is correct for cohesive tasks.
  - Split: if criteria target clearly independent artifacts (different
    directories, unrelated files), decompose into separate packets so
    multiple agents can work in parallel.
  - The objective is ALWAYS included in every packet so agents have
    full context even when working on a subset.

v1.5 additions:
  - Role assignment: builder, researcher, reviewer, operator
  - Priority propagation from parent task
  - Dependency tracking between packets (depends_on)
"""

import json
import re
from squirrel import SCHEMAS
from squirrel.validation import parse_criterion


# ── role inference ─────────────────────────────────────────────────

# Keywords that signal a role. Checked against objective + criteria text.
# Each role has keywords scored by strength: higher = stronger signal.
_ROLE_SIGNALS = {
    "builder": ["build", "create", "implement", "scaffold", "write", "add", "generate", "make", "develop"],
    "researcher": ["research", "investigate", "find out", "analyze", "survey", "explore", "look up", "report on"],
    "reviewer": ["review", "audit", "check", "verify", "inspect", "validate", "assess", "evaluate"],
    "operator": ["deploy", "migrate", "configure", "provision", "setup", "install", "run script", "execute"],
}

# Objective keywords are weighted 3x vs criteria keywords because the
# objective is the primary intent signal. This prevents criteria like
# "verify the build works" from overriding a builder objective.
_OBJECTIVE_WEIGHT = 3
_CRITERIA_WEIGHT = 1


def infer_role(objective: str, criteria: list[str]) -> str:
    """Infer the sub-agent role from objective and criteria text.

    Uses weighted keyword scoring: objective matches count 3x more than
    criteria matches. If multiple roles tie, falls back to 'builder'.
    Falls back to 'builder' if no keywords match at all.
    """
    obj_lower = objective.lower()
    crit_lower = " ".join(criteria).lower()

    scores = {}
    for role, keywords in _ROLE_SIGNALS.items():
        score = 0
        for kw in keywords:
            if kw in obj_lower:
                score += _OBJECTIVE_WEIGHT
            if kw in crit_lower:
                score += _CRITERIA_WEIGHT
        if score > 0:
            scores[role] = score

    if not scores:
        return "builder"

    # Return highest-scoring role. On tie, builder wins implicitly
    # because it's the fallback and ties indicate ambiguity.
    best_score = max(scores.values())
    winners = [r for r, s in scores.items() if s == best_score]
    if len(winners) == 1:
        return winners[0]
    # Tie: ambiguous signal, default to builder
    return "builder"


def _load_packet_schema():
    with open(SCHEMAS / "work_packet.schema.json") as f:
        return json.load(f)


def decompose(task: dict) -> list[dict]:
    """Break a task into work packets.

    Analyzes the objective and criteria to decide:
      - Single packet (cohesive task, one agent)
      - Multiple packets (independent subtasks, parallel agents)
    """
    task_id = task["task_id"]
    objective = task.get("objective", "")
    criteria = task.get("success_criteria", [])
    context_files = task.get("context_files", [])
    constraints = task.get("constraints", [])
    priority = task.get("priority", "normal")
    role_override = task.get("role")  # Explicit role from submit --role

    if not criteria:
        criteria = ["Objective completed as described"]

    # On retry, narrow scope to only failed criteria
    failed_criteria = task.get("failed_criteria")
    if failed_criteria:
        criteria = failed_criteria

    # Decide: single agent or split?
    groups = _group_criteria(criteria, objective)

    if len(groups) == 1:
        # Single packet — one agent does everything
        packets = [_make_packet(
            task_id=task_id,
            step=1,
            objective=objective,
            criteria=criteria,
            context_files=context_files,
            constraints=constraints,
            priority=priority,
            role_override=role_override,
        )]
    else:
        # Multiple packets — independent work streams
        packets = []
        for i, group in enumerate(groups, start=1):
            packets.append(_make_packet(
                task_id=task_id,
                step=i,
                objective=objective,
                criteria=group,
                context_files=context_files,
                constraints=constraints,
                priority=priority,
                role_override=role_override,
            ))

    # Warn when a single packet has many criteria — may benefit from splitting
    if len(packets) == 1 and len(criteria) >= 5:
        print(f"  NOTE: Single packet with {len(criteria)} criteria. Consider splitting "
              f"into independent tasks for parallel execution.")

    return packets


def _make_packet(task_id, step, objective, criteria, context_files, constraints,
                 priority="normal", role_override=None):
    step_num = f"{step:02d}"
    role = role_override if role_override else infer_role(objective, criteria)
    return {
        "packet_id": f"wp_{task_id.split('_', 1)[1]}_{step_num}",
        "task_id": task_id,
        "step": step,
        "role": role,
        "objective": objective,
        "criteria": criteria,
        "lane_id": f"lane_{step_num}",
        "lane_hint": "",
        "inputs": [],
        "context_files": context_files,
        "constraints": constraints,
        "expected_artifact": "",
        "depends_on": [],
        "priority": priority,
        "success_criteria": criteria,
        "status": "queued",
    }


def _group_criteria(criteria: list[str], objective: str) -> list[list[str]]:
    """Decide how to group criteria into work packets.

    Returns a list of groups. Each group becomes one packet/agent.

    Rules:
      1. If there's only one criterion, single group.
      2. Extract the target path/artifact from each criterion.
      3. Criteria targeting the same directory → same group.
      4. Criteria with no extractable target → stay in the default group.
      5. If all criteria share the same target → single group.
    """
    if len(criteria) <= 1:
        return [criteria]

    # Extract targets from each criterion
    targets = []
    for c in criteria:
        desc, _ = parse_criterion(c)
        target = _extract_target(desc)
        targets.append(target)

    # Group by target directory
    groups_map = {}
    default_group = []

    for criterion, target in zip(criteria, targets):
        if target is None:
            default_group.append(criterion)
        else:
            # Use the top-level directory as the group key.
            # Files without a directory (game.py, main.py) share the "." group.
            group_key = target.split("/")[0] if "/" in target else "."
            groups_map.setdefault(group_key, []).append(criterion)

    # If everything shares one target (or has no target), keep it as one group
    if len(groups_map) <= 1:
        return [criteria]

    # Build final groups — merge the default group into the largest target group
    groups = list(groups_map.values())
    if default_group:
        # Add ungrouped criteria to the largest group
        largest = max(groups, key=len)
        largest.extend(default_group)

    return groups


def _extract_target(criterion: str) -> str | None:
    """Try to extract a file/directory target from criterion text.

    Returns the path string or None if no target found.
    """
    text = criterion.strip()

    # Backticked or quoted paths
    quoted = re.search(r'[`"\']([^`"\']+(?:\.\w+|/))[`"\']', text)
    if quoted:
        return quoted.group(1).rstrip("/")

    # "X file exists" or "File X exists"
    file_exists = re.search(r'((?:\S+/)?\S*\.[\w]+)\s+file\s+exists', text, re.IGNORECASE)
    if file_exists:
        return file_exists.group(1).strip(",;")

    file_exists2 = re.search(r'file\s+((?:\S+/)?\S*\.[\w]+)\s+exists', text, re.IGNORECASE)
    if file_exists2:
        return file_exists2.group(1).strip(",;")

    # "X/ exists" or "X directory exists"
    dir_match = re.search(r'(\S+)/\s+exists', text, re.IGNORECASE)
    if dir_match:
        return dir_match.group(1).strip(",;")

    return None

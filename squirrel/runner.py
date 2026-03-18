"""Runner — orchestrates the full Squirrel execution loop.

intake -> register -> decompose -> dispatch -> validate -> receipt -> summary

This is the Commander. It owns the loop.

Stability guarantees:
- File lock prevents concurrent runners from corrupting state
- Control directory is checked for pause/cancel signals
- Every failure produces a structured receipt
- Missing workspace self-recovers via ensure_workspace()
- Blocked state used when context files are missing
"""

import fcntl
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from squirrel import REGISTRY, CONTROL, ensure_workspace
from squirrel import intake
from squirrel import state
from squirrel import planner
from squirrel import lanes
from squirrel import validation
from squirrel import receipts
from squirrel import events


_LOCK_FILE = CONTROL / "runner.lock"


def _load_task(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _save_task(task: dict):
    """Atomic save: write to temp file then rename."""
    path = REGISTRY / f"{task['task_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(task, f, indent=2)
    tmp.rename(path)


def _check_control() -> dict:
    """Read control signals from .squirrel/control/.

    Returns dict with:
        paused: bool — pipeline.json says paused
        cancel_ids: set — task IDs with cancel requests
        retry_ids: set — task IDs with retry requests
    """
    signals = {"paused": False, "cancel_ids": set(), "retry_ids": set()}

    # Check pipeline.json for pause
    # Doc contract: { "state": "paused" } / { "state": "running" }
    pipeline_file = CONTROL / "pipeline.json"
    if pipeline_file.exists():
        try:
            with open(pipeline_file) as f:
                pipeline = json.load(f)
            signals["paused"] = pipeline.get("state") == "paused"
        except (json.JSONDecodeError, OSError):
            pass

    # Scan for cancel/retry control files
    # Doc contract: cancel_{task_id}.json, retry_{task_id}.json
    for sig_file in CONTROL.glob("cancel_*.json"):
        try:
            with open(sig_file) as f:
                sig = json.load(f)
            task_id = sig.get("task_id")
            if task_id:
                signals["cancel_ids"].add(task_id)
            sig_file.unlink()
        except (json.JSONDecodeError, OSError):
            pass

    for sig_file in CONTROL.glob("retry_*.json"):
        try:
            with open(sig_file) as f:
                sig = json.load(f)
            task_id = sig.get("task_id")
            if task_id:
                signals["retry_ids"].add(task_id)
            sig_file.unlink()
        except (json.JSONDecodeError, OSError):
            pass

    return signals


def _make_crash_receipt(task_id: str, error: str) -> dict:
    """Generate a structured failure receipt when a task crashes."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "task_id": task_id,
        "lane_id": "",
        "status": "failed",
        "artifacts": [],
        "actions_taken": [],
        "validation_result": "fail",
        "validation_notes": f"Runner crash: {error}",
        "errors": [error],
        "started_at": now,
        "completed_at": now,
    }


def run_once(handler: lanes.LaneHandler = None, handler_factory=None, cwd: str = None):
    """Run one full cycle: process all inbox tasks through to receipts.

    Args:
        handler: Static lane handler used for all tasks.
        handler_factory: Callable(task) -> handler. Creates a per-task handler
                        so the handler can access parent task context (title,
                        priority, etc). Takes precedence over handler.
        cwd: Working directory for filesystem validation.
    """
    # Self-recover workspace structure
    ensure_workspace()

    cwd_path = Path(cwd) if cwd else None

    print("=" * 50)
    print("SQUIRREL v1 — Execution Loop")
    print("=" * 50)

    events.update_commander("starting", {"detail": "Acquiring runner lock"})

    # Acquire runner lock — only one runner at a time
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: Another runner is active (lock held). Exiting.")
        lock_fd.close()
        return []

    try:
        return _run_cycle(handler, handler_factory, cwd, cwd_path, lock_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _run_cycle(handler, handler_factory, cwd, cwd_path, lock_fd):
    """Inner cycle, runs under the file lock."""

    # Check control signals
    signals = _check_control()
    if signals["paused"]:
        print("\nPipeline is PAUSED. Write {\"state\": \"running\"} to control/pipeline.json to resume.")
        return []

    # Phase 1: Intake
    events.update_commander("intake", {"detail": "Scanning inbox"})
    print("\n[1/6] INTAKE — scanning inbox...")
    try:
        intake_results = intake.ingest_all()
    except Exception as e:
        print(f"  INTAKE ERROR: {e}")
        intake_results = []

    if not intake_results:
        print("  No new tasks in inbox.")
    else:
        for filename, success, msg in intake_results:
            status = "OK" if success else "REJECTED"
            print(f"  [{status}] {msg}")
            if success:
                events.emit("task_submitted", {"filename": filename, "message": msg})

    # Phase 1.5: Process retry signals — re-queue failed tasks
    if signals["retry_ids"]:
        max_r = state.max_retries()
        for tf in REGISTRY.glob("*.json"):
            try:
                task = _load_task(tf)
            except (json.JSONDecodeError, OSError):
                continue
            tid = task.get("task_id")
            if tid not in signals["retry_ids"]:
                continue
            if task.get("status") != "failed":
                print(f"  Retry signal for {tid} ignored (status: {task.get('status')})")
                continue
            retry_count = task.get("retry_count", 0)
            if retry_count >= max_r:
                print(f"  Retry signal for {tid} ignored (retries exhausted: {retry_count}/{max_r})")
                continue
            state.transition(task, "queued", "manual_retry")
            task["retry_count"] = retry_count + 1
            _save_task(task)
            print(f"  Re-queued {tid} via retry signal (attempt {task['retry_count']}/{max_r})")

    # Phase 2: Pick up queued tasks from registry
    events.update_commander("registry", {"detail": "Loading queued tasks"})
    print("\n[2/6] REGISTRY — loading queued tasks...")
    _PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    task_files = sorted(REGISTRY.glob("*.json"))
    tasks = []
    for tf in task_files:
        try:
            task = _load_task(tf)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not load {tf.name}: {e}")
            continue
        if task.get("status") == "queued":
            tasks.append(task)

    # Sort by priority: critical > high > normal > low
    tasks.sort(key=lambda t: _PRIORITY_ORDER.get(t.get("priority", "normal"), 2))

    if not tasks:
        print("  No queued tasks.")
        return []

    print(f"  Found {len(tasks)} queued task(s).")

    all_receipts = []

    for task in tasks:
        task_id = task["task_id"]

        # Check for cancel signal
        if task_id in signals["cancel_ids"]:
            print(f"\n--- Cancelling: {task_id} (control signal) ---")
            events.emit("task_cancelled", {"task_id": task_id})
            task["status"] = "failed"
            _save_task(task)
            receipt = _make_crash_receipt(task_id, "Cancelled via control signal.")
            receipts.write(receipt)
            all_receipts.append(receipt)
            continue

        print(f"\n--- Processing: {task_id} ---")

        try:
            receipt = _process_task(task, handler, handler_factory, cwd, cwd_path)
            all_receipts.append(receipt)
        except Exception as e:
            # Catch-all: no task can crash the runner
            error_msg = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()
            print(f"  FATAL ERROR processing {task_id}: {error_msg}")
            print(f"  {tb}")

            # Ensure task is in a terminal state
            try:
                if task.get("status") not in ("complete", "failed", "blocked"):
                    task["status"] = "failed"
                    _save_task(task)
            except Exception:
                pass

            # Always produce a receipt, even on crash
            receipt = _make_crash_receipt(task_id, error_msg)
            try:
                receipts.write(receipt)
            except Exception:
                print(f"  CRITICAL: Could not write crash receipt for {task_id}")
            all_receipts.append(receipt)

    events.update_commander("idle", {
        "detail": f"Cycle complete. Processed {len(all_receipts)} task(s).",
        "tasks_processed": len(all_receipts),
    })
    print(f"\n{'=' * 50}")
    print(f"Cycle complete. Processed {len(all_receipts)} task(s).")
    print(f"{'=' * 50}")

    return all_receipts


def _process_task(task, handler, handler_factory, cwd, cwd_path):
    """Process a single task through the full pipeline. Returns a receipt.

    Raises on unexpected errors — caller handles crash receipts.
    """
    task_id = task["task_id"]
    task_title = task.get("title", task_id)

    # Transition: queued -> active
    state.transition(task, "active", "lane_pickup")
    _save_task(task)
    print(f"  State: queued -> active")

    # Resolve handler for this task
    task_handler = handler_factory(task) if handler_factory else handler

    # Phase 3: Decompose
    events.update_commander("decompose", {"task_id": task_id, "title": task_title})
    print("\n[3/6] DECOMPOSE — breaking into work packets...")
    packets = planner.decompose(task)
    events.emit("plan_created", {
        "task_id": task_id,
        "packet_count": len(packets),
        "packet_ids": [p["packet_id"] for p in packets],
    })
    print(f"  Generated {len(packets)} work packet(s).")
    for p in packets:
        n_criteria = len(p.get('criteria', []))
        label = f"{n_criteria} criteria" if n_criteria else p.get('objective', '')[:60]
        print(f"    {p['packet_id']}: {label}")

    # Phase 4: Dispatch to lanes
    events.update_commander("dispatch", {
        "task_id": task_id,
        "detail": f"Executing {len(packets)} packet(s)",
    })
    print("\n[4/6] DISPATCH — executing in lanes...")
    lane_results = []
    blocked = False

    for packet in packets:
        lane_id = packet["lane_id"]
        packet_id = packet["packet_id"]
        role = packet.get("role", "builder")

        events.emit("packet_dispatched", {
            "task_id": task_id, "packet_id": packet_id, "lane_id": lane_id, "role": role,
        })
        events.update_lane(lane_id, {
            "role": role, "status": "running", "task_id": task_id,
            "packet_id": packet_id, "current_action": packet.get("objective", "")[:80],
            "artifact_path": "", "last_error": "",
        })

        try:
            result = lanes.dispatch(packet, handler=task_handler, cwd=cwd_path)
            status_icon = "PASS" if result["success"] else "FAIL"
            print(f"  [{status_icon}] {result['packet_id']}: {result['notes'][:60]}")
            lane_results.append(result)

            events.emit("lane_completed", {
                "task_id": task_id, "packet_id": packet_id,
                "lane_id": lane_id, "success": result["success"],
            })
            events.update_lane(lane_id, {
                "role": role, "status": "complete" if result["success"] else "failed",
                "task_id": task_id, "packet_id": packet_id,
                "current_action": "", "artifact_path": result.get("artifact", ""),
                "last_error": "" if result["success"] else result.get("notes", ""),
            })

        except lanes.BlockedError as be:
            # Missing context files — transition to blocked
            print(f"  [BLOCKED] {packet['packet_id']}: {be}")
            events.emit("lane_blocked", {
                "task_id": task_id, "packet_id": packet_id,
                "lane_id": lane_id, "reason": str(be),
            })
            events.update_lane(lane_id, {
                "role": role, "status": "blocked", "task_id": task_id,
                "packet_id": packet_id, "current_action": "",
                "artifact_path": "", "last_error": str(be),
            })
            state.transition(task, "blocked", "dependency_missing")
            task["blocked_reason"] = str(be)
            task["blocked_at"] = datetime.now(timezone.utc).isoformat()
            _save_task(task)
            blocked = True
            break

    if blocked:
        print(f"\n  State: active -> blocked")
        print(f"  Task {task_id} is blocked. Resolve dependencies and re-queue.")
        # Produce a blocked receipt so there's always an artifact
        now = datetime.now(timezone.utc).isoformat()
        receipt = {
            "task_id": task_id,
            "title": task_title,
            "lane_id": "",
            "status": "blocked",
            "artifacts": [],
            "actions_taken": [r["notes"] for r in lane_results],
            "validation_result": "fail",
            "validation_notes": task.get("blocked_reason", "Blocked on missing dependencies."),
            "errors": [task.get("blocked_reason", "")],
            "started_at": now,
            "completed_at": now,
        }
        receipts.write(receipt)
        events.emit("receipt_written", {"task_id": task_id, "status": "blocked"})
        return receipt

    # Transition: active -> validating
    state.transition(task, "validating", "execution_complete")
    _save_task(task)
    print(f"\n  State: active -> validating")

    # Phase 5: Validate
    events.update_commander("validate", {"task_id": task_id})
    print("\n[5/6] VALIDATE — checking success criteria...")
    passed, notes, details = validation.check(task, lane_results, cwd=cwd)
    result_label = "PASS" if passed else "FAIL"
    print(f"  Result: {result_label}")
    print(f"  Notes: {notes}")

    # Transition: validating -> complete/failed
    if passed:
        state.transition(task, "complete", "validation_pass")
    else:
        state.transition(task, "failed", "validation_fail")
        # Store failed criteria for partial retry
        failed_criteria = [c for c, p, n in details if not p]
        if failed_criteria:
            task["failed_criteria"] = failed_criteria
    _save_task(task)
    print(f"  State: validating -> {task['status']}")

    # Phase 6: Receipt
    events.update_commander("receipt", {"task_id": task_id, "status": task["status"]})
    print("\n[6/6] RECEIPT — generating output...")
    receipt = receipts.generate(task, lane_results, passed, notes)
    receipt_path = receipts.write(receipt)
    print(f"  Written: {receipt_path}")
    events.emit("receipt_written", {"task_id": task_id, "status": receipt["status"]})

    # Summary
    print("\n" + "-" * 40)
    print(receipts.summary(receipt))
    print("-" * 40)

    events.update_commander("idle", {"last_task": task_id, "last_status": task["status"]})

    return receipt

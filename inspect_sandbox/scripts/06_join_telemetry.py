"""
06 - Joining host telemetry to the eval log: one timeline per sample.

WHAT YOU LEARN HERE
    1. How to join two logs written on different machines: Inspect's .eval
       log (on your Mac) and Falco's alerts (in the Lima VM).
    2. The join key. Script 04 records each sample's container name in the
       log, and Falco tags every event with the same name.
    3. Why neither clock can be trusted. When your Mac sleeps, the VM is
       paused and Falco's timestamps fall behind (we once measured 15.5
       hours). So this script measures the offset from the data instead.
    4. Attribution by process ancestry: tracing an alert back through its
       parent processes to the command Inspect started, and from there to
       the agent step (or scorer) responsible.

HOW IT WORKS
    Inspect logs every command it runs in the sandbox, for example
        bash --login -c 'ls -R'
    and Falco's "TraceJoin Process Started" rule logs the same process with
        proc.name = bash    proc.args = --login -c ls -R
    1. MATCH      Pair the two by command text: the 2nd "ls -R" in one log
                  goes with the 2nd "ls -R" in the other. No clocks needed.
    2. CALIBRATE  Each pair gives (Inspect time - Falco time), the clock
                  offset. Take the median, ignoring pairs that disagree.
    3. ATTRIBUTE  A Falco event belongs to the Inspect command whose process
                  is the event's own process, its parent, or a grandparent.
                  Falco records 6 levels, but every process a command starts
                  passes the command on to its own children, so depth doesn't
                  matter. Inspect's `timeout` wrapper counts as part of the
                  command too. If nothing connects, fall back to time: which
                  command, or the setup phase, was running when it happened?
                  The offset is only known to within a few milliseconds, so
                  an event that close to the edge of a step is reported as
                  "too close to call" instead of being guessed. (Docker's
                  "runc init" helper usually lands there: it starts just
                  before each command, and Falco can't always see its link.)

NEEDS
    detections/tracejoin_rules.yaml and detections/falco-output.yaml
    deployed in the Lima VM (see the comments at the top of each file).

HOW TO RUN (from inspect_sandbox/)
    inspect eval scripts/04_agent_in_sandbox.py -T sandbox=docker
    python scripts/06_join_telemetry.py                   # newest agent_in_sandbox log
    python scripts/06_join_telemetry.py logs/<file>.eval  # a specific log
    python scripts/06_join_telemetry.py --falco alerts.jsonl --json joined.json

    Without --falco, the script reads Falco's log straight from the VM
    (only root can read it there, hence the sudo in FALCO_FETCH).
"""

import argparse
import json
import os
import shlex
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from inspect_ai.event import SandboxEvent, SpanBeginEvent, SpanEndEvent, ToolEvent
from inspect_ai.log import EvalLog, EvalSample, list_eval_logs, read_eval_log

# Use the same log folder as `inspect eval` (set in .env, default ./logs).
LOG_DIR = os.environ.get("INSPECT_LOG_DIR", "./logs")

# The metadata key that script 04's record_container() writes.
JOIN_KEY = "tracejoin_container"

# How to read Falco's JSON alerts from the Mac.
FALCO_FETCH = [
    "limactl", "shell", "falco-quickstart",
    "sudo", "cat", "/var/log/falco/tracejoin.jsonl",
]  # fmt: skip

# The telemetry rule in detections/tracejoin_rules.yaml.
PROCESS_RULE = "TraceJoin Process Started"

# A process and its ancestors, added to every alert by falco-output.yaml.
ANCESTRY_FIELDS = ["proc.pid", "proc.ppid"] + [f"proc.apid[{n}]" for n in range(2, 7)]

# A matched pair whose offset is further than this from the median was
# matched wrongly, and is left out of the calibration.
MAX_DISAGREEMENT = 1.0  # seconds

# Falco cuts proc.args off at about 4096 bytes, so compare only this much.
ARGS_COMPARED = 4000

# The smallest margin of error for attribution by time: roughly how long
# `docker exec` takes to start a process. The calibration spread is used
# instead when it is bigger.
MIN_TIME_MARGIN = 0.025  # seconds


def short(text: str, width: int = 100) -> str:
    """Squash text onto one line and cut it to `width` characters."""
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def pick_log(path: str | None, task_name: str | None) -> EvalLog:
    # The same as script 05 (a file name starting with a digit can't be
    # imported, so it's copied here).
    if path:
        return read_eval_log(path)
    wanted = task_name.replace("_", "-") if task_name else None
    for info in list_eval_logs(LOG_DIR):
        if wanted is None or info.task.replace("_", "-") == wanted:
            return read_eval_log(info)
    raise SystemExit(f"No logs found in {LOG_DIR}. Run script 04 first.")


# ---------------------------------------------------------------------------
# FALCO'S SIDE
# ---------------------------------------------------------------------------
def parse_falco_time(stamp: str) -> datetime:
    # Falco writes nanoseconds ("19:56:03.742829040Z"). Python's datetime
    # holds microseconds, so keep the first 6 digits of the fraction.
    stamp = stamp.removesuffix("Z")
    if "." in stamp:
        whole, fraction = stamp.split(".")
        stamp = f"{whole}.{fraction[:6]}"
    return datetime.fromisoformat(stamp + "+00:00")


def load_falco(path: str | None) -> list[dict]:
    """Read Falco's JSON alerts (one per line) from a file, or from the VM."""
    if path:
        text = Path(path).read_text()
    else:
        result = subprocess.run(FALCO_FETCH, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(
                "Couldn't read Falco's log from the VM. Is falco-quickstart running?\n"
                + result.stderr
            )
        text = result.stdout

    events = []
    for line in text.splitlines():
        if line.strip():
            event = json.loads(line)
            event["when"] = parse_falco_time(event["time"])  # on Falco's clock
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# INSPECT'S SIDE
# ---------------------------------------------------------------------------
@dataclass
class Command:
    """Something Inspect did in the sandbox, with the time window it took.

    Mostly SandboxEvents: running a command ("exec", with argv) or reading or
    writing a file (argv empty). The setup phase is one too (see
    setup_windows), so setup work can be attributed by time.
    """

    start: datetime
    end: datetime
    argv: list[str]  # the command's arguments; empty if it isn't a command
    phase: str  # "agent", "scorer", "setup" or "other"
    step: int | None  # which agent tool call (1, 2, 3...) when phase is "agent"
    label: str  # e.g. "agent step 3: bash(cp ...)"


def tool_text(tool: ToolEvent) -> str:
    return ", ".join(str(value) for value in tool.arguments.values())


def describe(
    event: SandboxEvent, spans: dict[str, SpanBeginEvent], tools: list[ToolEvent]
) -> tuple[str, int | None, str]:
    """Work out which part of the run a sandbox command belongs to.

    Inspect wraps everything in spans (init, solvers, each tool call, each
    scorer). Walk up from the command's span until one says what it is.
    """
    span_id = event.span_id
    while span_id in spans:
        span = spans[span_id]
        if span.type == "tool":
            # Number the step by the tool call whose time window holds this
            # command. Both times come from Inspect's clock, so this is exact.
            for number, tool in enumerate(tools, start=1):
                if tool.timestamp <= event.timestamp <= (tool.completed or event.timestamp):
                    label = f"agent step {number}: {tool.function}({short(tool_text(tool), 40)})"
                    return "agent", number, label
            return "agent", None, f"agent tool: {span.name}"
        if span.type == "scorer":
            return "scorer", None, f"scorer: {span.name}"
        if span.type == "init":
            return "setup", None, "setup"
        span_id = span.parent_id
    return "other", None, "other"


def inspect_commands(sample: EvalSample) -> tuple[list[Command], list[ToolEvent]]:
    spans = {e.id: e for e in sample.events if isinstance(e, SpanBeginEvent)}
    tools = [e for e in sample.events if isinstance(e, ToolEvent)]
    commands = []
    for event in sample.events:
        if isinstance(event, SandboxEvent):
            argv = []
            if event.action == "exec" and event.cmd:
                # Inspect logs the command as one shell-quoted string; split it
                # back into the argument list the process was started with.
                try:
                    argv = shlex.split(event.cmd)
                except ValueError:
                    argv = event.cmd.split()
            phase, step, label = describe(event, spans, tools)
            end = event.completed or event.timestamp
            commands.append(Command(event.timestamp, end, argv, phase, step, label))
    return commands, tools


def setup_windows(sample: EvalSample) -> list[Command]:
    """The setup phase (Inspect's "init" span) as a time window.

    It covers starting the container and copying the sample's files in.
    Inspect runs its own commands for those without logging each one, so
    their processes can only be attributed by time.
    """
    begins = {
        e.id: e for e in sample.events if isinstance(e, SpanBeginEvent) and e.type == "init"
    }
    return [
        Command(begins[e.id].timestamp, e.timestamp, [], "setup", None, "setup")
        for e in sample.events
        if isinstance(e, SpanEndEvent) and e.id in begins
    ]


# ---------------------------------------------------------------------------
# 1. MATCH   2. CALIBRATE   3. ATTRIBUTE
# ---------------------------------------------------------------------------
def command_key(name: str, args: str) -> tuple[str, str]:
    # Linux keeps only the first 15 characters of a process name.
    return name[:15], args[:ARGS_COMPARED]


def match(commands: list[Command], process_starts: list[dict]) -> list[tuple[Command, dict]]:
    """Pair Inspect's commands with Falco's process starts by command text."""
    # Falco's clock may be off, but it agrees with itself, so the ORDER of
    # events within Falco's log is right. Queue them up per command text.
    queues: dict[tuple[str, str], list[dict]] = {}
    for event in sorted(process_starts, key=lambda e: e["when"]):
        fields = event["output_fields"]
        key = command_key(fields.get("proc.name") or "", fields.get("proc.args") or "")
        queues.setdefault(key, []).append(event)

    pairs = []
    for command in sorted((c for c in commands if c.argv), key=lambda c: c.start):
        key = command_key(os.path.basename(command.argv[0]), " ".join(command.argv[1:]))
        if queues.get(key):
            pairs.append((command, queues[key].pop(0)))
    return pairs


def calibrate(
    pairs: list[tuple[Command, dict]],
) -> tuple[timedelta | None, list[tuple[Command, dict]], float | None]:
    """Measure Falco's clock offset. Returns (offset, pairs used, spread in ms)."""
    offsets = [(command.start - event["when"]).total_seconds() for command, event in pairs]
    if not offsets:
        return None, [], None
    middle = statistics.median(offsets)
    # A pair far from the median was matched wrongly (say Falco missed one
    # run of a command, shifting the rest). Leave it out and measure again.
    kept = [(p, o) for p, o in zip(pairs, offsets) if abs(o - middle) <= MAX_DISAGREEMENT]
    if not kept:
        return None, [], None
    good = [o for _, o in kept]
    spread_ms = (max(good) - min(good)) * 1000
    return timedelta(seconds=statistics.median(good)), [p for p, _ in kept], spread_ms


def exec_groups(
    pairs: list[tuple[Command, dict]], process_starts: list[dict]
) -> tuple[dict[int, Command], dict[int, Command]]:
    """Find the processes that make up each matched command.

    Inspect runs each command with `docker exec`, which starts a tree like
        runc                          (outside the container; one per exec)
         |- runc init                 docker's helper, logged as "6 init"
         '- timeout -k 5s 30s ...     Inspect's time limit on the command
             '- bash --login -c ...   <- the process we matched
    Returns two lookups:
        owners:   process id -> command, for the matched process and the
                  wrappers above it that also started inside the container
        siblings: runc's process id -> command, so runc's other children
                  (its "init" helper) count as part of the command too.
                  This only works when Falco saw `timeout` start while runc
                  was still its parent. runc exits quickly, and if it's gone
                  first, `timeout` is handed to the container's shared
                  parent (the containerd shim) and the link is lost.
    """
    # The parent of every process that started inside this container.
    parent = {}
    for event in process_starts:
        fields = event["output_fields"]
        if fields.get("proc.pid") is not None:
            parent[fields["proc.pid"]] = fields.get("proc.ppid")

    owners: dict[int, Command] = {}
    by_runc: dict[int, list[Command]] = {}
    for command, event in pairs:
        pid = event["output_fields"].get("proc.pid")
        if pid is None:
            continue
        owners[pid] = command
        # Climb through wrappers that also started inside the container
        # (the cap is just a guard against a loop in odd data).
        for _ in range(10):
            if parent.get(pid) not in parent:
                break
            pid = parent[pid]
            owners[pid] = command
        # The first parent outside the container is that exec's runc.
        if parent.get(pid) is not None:
            by_runc.setdefault(parent[pid], []).append(command)

    # Only trust a parent that belongs to exactly one command. (The shared
    # containerd shim is the parent of every command's `timeout`, so it is
    # left out here.)
    siblings = {pid: cmds[0] for pid, cmds in by_runc.items() if len(cmds) == 1}
    return owners, siblings


def attribute(
    event: dict,
    owners: dict[int, Command],
    siblings: dict[int, Command],
    windows: list[Command],
    offset: timedelta,
    margin: timedelta,
) -> tuple[Command | None, str]:
    """Find what caused a Falco event: by ancestry, else by time."""
    fields = event["output_fields"]
    # 1. Ancestry: the process itself, its parent, its grandparent...
    for name in ANCESTRY_FIELDS:
        pid = fields.get(name)
        if pid is not None and pid in owners:
            return owners[pid], "ancestry"
    # ...or a sibling under the same `docker exec` (runc's "init" helper).
    if fields.get("proc.ppid") in siblings:
        return siblings[fields["proc.ppid"]], "ancestry"
    # 2. Time: nothing connects, so which window was open when it happened?
    #    The offset is only known to within `margin`, so the event has to be
    #    at least that far inside a window to count. Take the narrowest such
    #    window, e.g. a file write rather than all of setup.
    when = event["when"] + offset
    inside = [w for w in windows if w.start + margin <= when <= w.end - margin]
    if inside:
        return min(inside, key=lambda w: w.end - w.start), "time"
    if any(w.start - margin <= when <= w.end + margin for w in windows):
        return None, "too close to call"
    return None, "-"


def join_sample(sample: EvalSample, falco: list[dict]) -> dict | None:
    container = (sample.metadata or {}).get(JOIN_KEY)
    if not container:
        return None
    events = [e for e in falco if e["output_fields"].get("container.name") == container]
    process_starts = [e for e in events if e["rule"] == PROCESS_RULE]

    commands, tools = inspect_commands(sample)
    offset, pairs, spread_ms = calibrate(match(commands, process_starts))
    calibrated = offset is not None
    offset = offset or timedelta(0)
    margin = timedelta(seconds=max((spread_ms or 0) / 1000, MIN_TIME_MARGIN))
    owners, siblings = exec_groups(pairs, process_starts)
    windows = commands + setup_windows(sample)

    # Build one timeline, every entry on Inspect's clock.
    timeline = []
    for number, tool in enumerate(tools, start=1):
        timeline.append({
            "when": tool.timestamp, "kind": "TOOL",
            "detail": f"{tool.function}: {tool_text(tool)}",
            "phase": "agent", "step": number, "label": f"agent step {number}", "method": "inspect",
        })  # fmt: skip
    for command in commands:
        # Agent commands already show as TOOL lines; file reads and writes
        # aren't commands.
        if command.argv and command.phase != "agent":
            timeline.append({
                "when": command.start, "kind": "CMD", "detail": " ".join(command.argv),
                "phase": command.phase, "step": None, "label": command.label, "method": "inspect",
            })  # fmt: skip

    alerts = []
    # Oldest first (Falco's order), so parents are seen before their children.
    for event in sorted(events, key=lambda e: e["when"]):
        fields = event["output_fields"]
        cause, method = attribute(event, owners, siblings, windows, offset, margin)
        if cause and method == "ancestry" and event["rule"] == PROCESS_RULE:
            # Anything this process starts later belongs to the same command,
            # however deep the process tree gets.
            owners.setdefault(fields.get("proc.pid"), cause)
        item = {
            "when": event["when"] + offset,
            "kind": "EXEC" if event["rule"] == PROCESS_RULE else "ALERT",
            "detail": f"{fields.get('proc.name')} {fields.get('proc.args') or ''}".strip(),
            "phase": cause.phase if cause else None,
            "step": cause.step if cause else None,
            "label": cause.label if cause else "unattributed",
            "method": method,
        }
        if item["kind"] == "ALERT":
            item |= {
                "rule": event["rule"], "priority": event["priority"],
                "file": fields.get("fd.name"), "pid": fields.get("proc.pid"),
            }  # fmt: skip
            alerts.append(item)
        timeline.append(item)

    timeline.sort(key=lambda item: item["when"])
    for item in timeline:
        item["time"] = item.pop("when").isoformat()

    return {
        "id": sample.id,
        "epoch": sample.epoch,
        "container": container,
        "offset_s": offset.total_seconds() if calibrated else None,
        "pairs": len(pairs),
        "spread_ms": spread_ms,
        "timeline": timeline,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
def print_sample(result: dict) -> None:
    print(f"\n{'=' * 80}\nSAMPLE {result['id']} (epoch {result['epoch']})")
    print(f"Container: {result['container']}\n{'=' * 80}")

    offset_s = result["offset_s"]
    if offset_s is None:
        print("WARNING: no commands matched, so Falco's clock is uncalibrated (offset 0).")
        print("         Is the 'TraceJoin Process Started' rule deployed in the VM?")
    else:
        hours = f" (= {offset_s / 3600:+.2f} h)" if abs(offset_s) >= 60 else ""
        print(
            f"Falco clock offset: {offset_s:+.3f} s{hours}, from {result['pairs']} "
            f"matched commands (spread {result['spread_ms']:.0f} ms)"
        )

    print("\n-- Timeline (Inspect's clock; Falco events shifted by the offset) --")
    for item in result["timeline"]:
        text = f"{item['rule']} ({item['detail']})" if item["kind"] == "ALERT" else item["detail"]
        where = f"agent step {item['step']}" if item["step"] else item["label"]
        method = f"  [{item['method']}]" if item["kind"] in ("EXEC", "ALERT") else ""
        print(f"{item['time'][11:23]}  {item['kind']:5}  {short(text, 60):60}  {where}{method}")

    print("\n-- Alerts, and the step that caused each --")
    for alert in result["alerts"] or []:
        print(f"{alert['rule']}  ({short(alert['detail'], 60)})")
        print(f"    -> {alert['label']}  [by {alert['method']}]")
    if not result["alerts"]:
        print("(none)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Join Falco's host telemetry to an Inspect log.")
    parser.add_argument("log", nargs="?", help="Path to a .eval log (default: newest for --task)")
    parser.add_argument("--task", default="agent_in_sandbox", help="Use the newest log for this task")
    parser.add_argument("--falco", help="A Falco JSON alerts file (default: read it from the VM)")
    parser.add_argument("--json", help="Also write the joined result to this JSON file")
    args = parser.parse_args()

    log = pick_log(args.log, args.task)
    falco = load_falco(args.falco)
    print(f"Log:    {log.location}")
    print(f"Falco:  {args.falco or 'read from the VM'} ({len(falco)} events)")

    results = []
    for sample in log.samples or []:
        result = join_sample(sample, falco)
        if result is None:
            print(
                f"\nSAMPLE {sample.id} (epoch {sample.epoch}): no container recorded "
                "(sandbox=local?), skipping."
            )
            continue
        print_sample(result)
        results.append(result)

    if args.json:
        Path(args.json).write_text(json.dumps({"log": log.location, "samples": results}, indent=2))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()

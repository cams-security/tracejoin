"""
05 - Reading eval logs from Python: the trajectory inspector.

WHAT YOU LEARN HERE
    1. Every `inspect eval` run writes a log file (.eval) to ./logs.
    2. read_eval_log() loads it into Python objects you can query.
    3. The difference between MESSAGES and EVENTS:
         - messages: the conversation (system, user, assistant, tool), i.e.
           what the model saw and said.
         - events:   a timestamped record of everything Inspect did,
           including model calls, tool calls, sandbox commands, scoring,
           and errors.
       For telemetry work, events matter more. Each one has a UTC timestamp,
       so you can line it up against host logs (process starts, file opens,
       DNS lookups) recorded during the same run.

HOW TO RUN (from inspect_sandbox/)
    python scripts/05_read_logs.py                 # newest log in ./logs
    python scripts/05_read_logs.py --task agent_in_sandbox   # newest log for one task
    python scripts/05_read_logs.py logs/<file>.eval          # a specific log

    Run script 04 first to get the most interesting output.

    You can also explore logs with the CLI:
        inspect log list               # list logs
        inspect log dump <file>.eval   # print a log as JSON
        inspect view                   # browse logs in the web viewer
"""

import argparse
import os

from inspect_ai.event import ModelEvent, SandboxEvent, ToolEvent
from inspect_ai.log import EvalLog, list_eval_logs, read_eval_log

# Use the same log folder as `inspect eval` (set in .env, default ./logs).
LOG_DIR = os.environ.get("INSPECT_LOG_DIR", "./logs")


def short(text: str, width: int = 100) -> str:
    """Squash text onto one line and cut it to `width` characters."""
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def pick_log(path: str | None, task_name: str | None) -> EvalLog:
    if path:
        return read_eval_log(path)
    # list_eval_logs() returns newest first. info.task comes from the log's
    # filename, where underscores become hyphens ("tool_use" -> "tool-use"),
    # so compare both forms the same way.
    wanted = task_name.replace("_", "-") if task_name else None
    for info in list_eval_logs(LOG_DIR):
        if wanted is None or info.task.replace("_", "-") == wanted:
            return read_eval_log(info)
    raise SystemExit(f"No logs found in {LOG_DIR}. Run one of the other scripts first.")


def print_header(log: EvalLog) -> None:
    # log.eval holds what was run; log.results holds the summary scores.
    print(f"Task:    {log.eval.task}")
    print(f"Model:   {log.eval.model}")
    print(f"Status:  {log.status}")  # "success", "error", or "cancelled"
    print(f"File:    {log.location}")
    if log.results:
        for score in log.results.scores:
            metrics = ", ".join(f"{name}={m.value:.3f}" for name, m in score.metrics.items())
            print(f"Scorer:  {score.name}: {metrics}")


def print_sample(sample) -> None:
    print(f"\n{'=' * 80}\nSAMPLE {sample.id} (epoch {sample.epoch})\n{'=' * 80}")

    # --- MESSAGES: the conversation --------------------------------------
    print("\n-- Messages (what the model saw and said) --")
    for message in sample.messages:
        line = f"[{message.role:9}] {short(message.text)}"
        if message.role == "assistant" and message.tool_calls:
            calls = "; ".join(f"{c.function}({short(c.arguments, 60)})" for c in message.tool_calls)
            line += f"  -> CALLS {calls}"
        print(line)

    # --- EVENTS: the timeline --------------------------------------------
    # Each event type is its own class. Filter with isinstance() to pick out
    # the kinds you care about. These three are the most useful for you:
    #   ModelEvent   - one call to the model (input, output, token usage)
    #   ToolEvent    - one tool call (function, arguments, result, error)
    #   SandboxEvent - one low-level sandbox action (exec / read_file /
    #                  write_file), including file setup and scorer commands
    print("\n-- Events (timeline of what Inspect did) --")
    for event in sample.events:
        stamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
        if isinstance(event, ModelEvent):
            print(f"{stamp}  MODEL    {event.model}: {short(event.output.completion, 70)}")
        elif isinstance(event, ToolEvent):
            outcome = f"ERROR {event.error.message}" if event.error else short(event.result, 50)
            print(f"{stamp}  TOOL     {event.function}({short(event.arguments, 60)}) -> {outcome}")
        elif isinstance(event, SandboxEvent):
            detail = event.cmd if event.action == "exec" else event.file
            print(f"{stamp}  SANDBOX  {event.action}: {short(detail, 80)}")

    # Notice what is NOT in this timeline: the base64 decode, the cp process,
    # and the file it read. Inspect logs the command string it was asked to
    # run, not what that command did on the machine. Recording that is the
    # job of host telemetry.

    # --- SCORES -----------------------------------------------------------
    print("\n-- Scores --")
    for name, score in (sample.scores or {}).items():
        print(f"{name:16} value={score.value!s:4}  {short(score.explanation or '', 80)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print what happened in an Inspect eval log.")
    parser.add_argument("log", nargs="?", help="Path to a .eval log (default: newest)")
    parser.add_argument("--task", help="Use the newest log for this task name")
    args = parser.parse_args()

    log = pick_log(args.log, args.task)
    print_header(log)
    for sample in log.samples or []:
        print_sample(sample)


if __name__ == "__main__":
    main()

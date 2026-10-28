# Inspect sandbox

Seven small scripts for learning [Inspect](https://inspect.aisi.org.uk/), the UK AISI eval framework, and for joining host telemetry to it. Each one builds on the last. Scripts 01–05 run offline with Inspect's free mock model, so you can learn the mechanics before spending money on API calls. Scripts 06–07 add host telemetry, so they also need the Lima VM running Docker and Falco (see [`../lima_setup/`](../lima_setup/)), with the files in [`../detections/`](../detections/) deployed.

## Setup

Install the project from the repo root with `uv sync`, then activate it and run everything from this folder:

```sh
cd inspect_sandbox
source ../.venv/bin/activate
cp .env.example .env
inspect --version
```

`.env` sets the default model to `mockllm/model` and the log folder to `./logs`, and Inspect reads it automatically. It is gitignored, so it's also where your API keys go.

## The scripts

Work through them in order. Each file starts with a comment that explains what it teaches and how to run it.

| Script | What it teaches | Run it |
| --- | --- | --- |
| `01_hello_world.py` | Task = dataset + solver + scorer | `inspect eval scripts/01_hello_world.py` |
| `02_tool_use.py` | Custom tools, the tool loop, custom solvers and scorers, scripting a fake model | `inspect eval scripts/02_tool_use.py` |
| `03_datasets_and_scorers.py` | Loading samples from JSONL, prompt templates, several scorers, metrics, epochs | `inspect eval scripts/03_datasets_and_scorers.py` |
| `04_agent_in_sandbox.py` | The `react()` agent, the `bash` tool, sandboxes, a honeytoken, and transcript monitors versus state monitors | `inspect eval scripts/04_agent_in_sandbox.py` |
| `05_read_logs.py` | Reading logs in Python, and the difference between messages and events | `python scripts/05_read_logs.py` |
| `06_join_telemetry.py` | Joining Falco's host telemetry to the eval log: the join key, measuring clock offset from the data, and attributing events to agent steps by process ancestry | `python scripts/06_join_telemetry.py` (after running 04 with `-T sandbox=docker`) |
| `07_gate1.py` | Ground-truth labels and acceptance criteria: runs 04 in Docker five times, joins the telemetry, and checks every Gate 1 claim | `python scripts/07_gate1.py` |

After any run, open the log viewer:

```sh
inspect view
```

## Concepts in one place

- **Task**: one eval. It is built from a dataset, a solver and a scorer, and marked with `@task`.
- **Sample**: one test case, with an `input`, a `target`, and optional `metadata` and `files`.
- **Solver**: a step that works on a sample. `generate()` calls the model. Solvers run in a list, in order.
- **Tool**: a function the model can ask Inspect to run. The model only asks; Inspect does the work.
- **Agent**: a loop that keeps calling the model and its tools until it decides it is done. `react()` is the built-in one.
- **Sandbox**: where tool commands run. `local` means your own machine with no isolation; `docker` means a throwaway container.
- **Scorer**: judges a finished sample. It can grade the answer, or act as a monitor that checks for bad behaviour.
- **Log**: the `.eval` file each run writes. It records every message, event and score.

## Useful command-line flags

```sh
inspect eval <file> --limit 2              # only the first 2 samples
inspect eval <file> --sample-id mul-2      # one sample by id
inspect eval <file> --epochs 3             # run each sample 3 times
inspect eval <file> -T scripted=false      # pass an argument to the @task function
inspect eval <file> --model <provider/model>
```

## Using a real model

1. Install the provider package: `uv add anthropic` (or `openai`, and so on).
2. Add your key to `.env`, for example `ANTHROPIC_API_KEY=...`. The file is gitignored.
3. Pass `-T scripted=false` so the task stops using its fake model, plus `--model`:

```sh
inspect eval scripts/02_tool_use.py -T scripted=false --model anthropic/claude-haiku-4-5-20251001
```

Script 04 lets a model run shell commands, so with a real model it refuses to run unless you use the Docker sandbox (`-T sandbox=docker`). That needs a Docker engine; this project runs one inside the Lima VM (see [`../lima_setup/`](../lima_setup/)).

## Cleaning up

```sh
rm -rf logs/ output/
```

# Inspect Sandbox Learning Lab

This directory is designed to help you learn the core concepts of [Inspect](https://github.com/UK-AISI/inspect) (the framework from UK AISI) by simulating its execution environment.

## Core Concepts to Understand

1. **The Task:** The "mission" given to the agent. It includes the prompt and any tools available.
2. **The Agent:** The LLM wrapper that receives the task, decides on tool calls, and processes observations.
3. **The Sandbox/Environment:** The isolated space where the agent's tool calls actually execute (e.g., a Python interpreter or a shell).
4. **The Trajectory:** The "breadcrumb trail" of everything that happened—the prompt, the thought process, the tool call, and the result.
5. **The Eval/Score:** How we judge if the agent succeeded or failed based on its trajectory.

---

## Getting Started

To run these scripts, you will need a local environment with `inspect` installed:
`pip install inspect-ai` (Note: This is a placeholder; ensure you use the correct package for the framework you are targeting).

### 1. The "Hello World" Task
This script shows how to define a minimal task and run it against a model.

### 2. The Tool-Use Task (The most important part for us!)
Since our research is about **telemetry**, we need to see how a tool call creates a measurable event in the OS. This script defines a "dummy" tool that touches a file, which we can later detect with eBPF/Tetragon.

### 3. The Trajectory Inspector
A helper script to parse the output of an Inspect run to look for specific patterns.

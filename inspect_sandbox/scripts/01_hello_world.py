"""
01 - Hello World: the smallest complete Inspect eval.

WHAT YOU LEARN HERE
    Every Inspect eval is a Task, and every Task has three parts:

        dataset  ->  solver  ->  scorer
        (inputs)     (what the     (was the
                      model does)   answer good?)

    - dataset: a list of Samples. A Sample is one test case: an `input`
      (the prompt) and usually a `target` (what a good answer looks like).
    - solver:  the steps that turn the input into an answer. The simplest
      solver is `generate()`, which sends the prompt to the model once.
    - scorer:  compares the model's answer to the target and records a score.

HOW TO RUN (from the inspect_sandbox/ folder)
    inspect eval scripts/01_hello_world.py

    No API key needed. The .env file in inspect_sandbox/ sets the default
    model to `mockllm/model`, Inspect's built-in fake model. It always replies
    "Default output from mockllm/model", so expect a score of 0. That is fine:
    the point is to see the machinery run end to end.

    To try a real model (after `uv add anthropic` and setting ANTHROPIC_API_KEY):
        inspect eval scripts/01_hello_world.py --model anthropic/claude-haiku-4-5-20251001

THEN LOOK AT THE RESULT
    inspect view
    This opens a local web page that shows every sample, every message sent
    to and from the model, and every score. Get used to it: the log viewer is
    the main way you will study what an agent did.
"""

from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate


# The @task decorator registers this function with Inspect so the `inspect eval`
# command can find it. The function returns a Task object. You can have several
# @task functions in one file; Inspect runs all of them unless you pick one with
# `inspect eval scripts/01_hello_world.py@hello_world`.
@task
def hello_world():
    return Task(
        # DATASET: a plain Python list of Samples is fine for small tests.
        # Later scripts load samples from a file instead.
        dataset=[
            Sample(
                input="What is the capital of France? Answer in one word.",
                target="Paris",
            ),
            Sample(
                input="What colour do you get by mixing blue and yellow paint? One word.",
                target="green",
            ),
        ],
        # SOLVER: generate() = "call the model with the conversation so far and
        # append its reply". With no other solvers, that is one prompt, one reply.
        solver=generate(),
        # SCORER: includes() marks the sample CORRECT if the target text appears
        # anywhere in the model's reply (case-insensitive by default).
        scorer=includes(),
    )


# You can also start an eval from Python instead of the command line.
# `python scripts/01_hello_world.py` does the same thing as `inspect eval`.
# The CLI is more common; this form is handy when you want to post-process the
# results in the same script (eval() returns the logs as Python objects).
if __name__ == "__main__":
    logs = eval(hello_world(), model="mockllm/model")
    log = logs[0]
    print(f"\nStatus: {log.status}")
    if log.results:
        for name, metric in log.results.scores[0].metrics.items():
            print(f"{name}: {metric.value:.3f}")
    print(f"Log file: {log.location}")

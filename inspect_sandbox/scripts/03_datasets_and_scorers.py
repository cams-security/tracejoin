"""
03 - Datasets and scorers: loading test cases from a file, and judging answers.

WHAT YOU LEARN HERE
    1. Loading samples from a JSONL file with json_dataset().
    2. Mapping your file's columns onto Sample fields with record_to_sample.
    3. Chaining solvers: system_message() -> prompt_template() -> generate().
    4. Running several scorers at once, including a custom one.
    5. Metrics (accuracy, stderr) and how they summarise per-sample scores.
    6. Epochs: running each sample more than once.

THE DATA
    data/arithmetic.jsonl has one JSON object per line:
        {"id": "add-1", "question": "What is 12 + 30?", "answer": "42", "difficulty": "easy"}
    Your columns don't have to be called input/target; you map them yourself.

HOW TO RUN (from inspect_sandbox/)
    inspect eval scripts/03_datasets_and_scorers.py

    The scripted fake model does the arithmetic itself, but it makes a
    deliberate mistake on "hard" questions so you can see wrong answers
    being scored.

    Useful flags to try:
        --limit 2        run only the first 2 samples (good for quick checks)
        --epochs 3       run every sample 3 times
        --sample-id mul-2  run just one sample by id
        -T scripted=false --model anthropic/claude-haiku-4-5-20251001
"""

import re
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample, json_dataset
from inspect_ai.model import ChatMessage, GenerateConfig, ModelOutput, get_model
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Target,
    accuracy,
    match,
    scorer,
    stderr,
)
from inspect_ai.solver import TaskState, generate, prompt_template, system_message
from inspect_ai.tool import ToolChoice, ToolInfo

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "arithmetic.jsonl"


# ---------------------------------------------------------------------------
# DATASET
# ---------------------------------------------------------------------------
# record_to_sample is called once per line of the file. It receives the line
# as a dict and returns a Sample. Anything that isn't input/target/id can go
# in metadata, where solvers, scorers and the log viewer can all see it.
def record_to_sample(record: dict) -> Sample:
    return Sample(
        id=record["id"],
        input=record["question"],
        target=record["answer"],
        metadata={"difficulty": record["difficulty"]},
    )


# ---------------------------------------------------------------------------
# PROMPT TEMPLATE
# ---------------------------------------------------------------------------
# prompt_template() rewrites the user's message before it reaches the model.
# {prompt} is replaced with the sample's input. Asking for a fixed answer
# format ("ANSWER: <number>") makes scoring far more reliable than hoping
# the model's reply contains the right number somewhere.
TEMPLATE = """
Solve this arithmetic problem. Show brief working, then finish with a final
line in exactly this format:

ANSWER: <number>

Problem: {prompt}
"""


# ---------------------------------------------------------------------------
# A CUSTOM SCORER
# ---------------------------------------------------------------------------
# Built-in scorers cover a lot (match, includes, pattern, exact, f1,
# model_graded_qa, ...), but you will often want your own. This one pulls the
# number out of the "ANSWER: <number>" line and compares it to the target.
#
# A scorer can declare several metrics. accuracy() is the share of CORRECT
# samples; stderr() is the standard error of that mean, which tells you how
# much the accuracy would wobble if you reran with different samples.
@scorer(metrics=[accuracy(), stderr()])
def answer_line():
    async def score(state: TaskState, target: Target) -> Score:
        # state.output.completion is the model's final reply as plain text.
        found = re.search(r"ANSWER:\s*(-?[\d.]+)", state.output.completion)
        if found is None:
            return Score(
                value=INCORRECT,
                answer=None,
                explanation="No 'ANSWER:' line in the reply.",
            )
        answer = found.group(1)
        return Score(
            value=CORRECT if answer == target.text else INCORRECT,
            answer=answer,
            explanation=f"Parsed {answer!r}, expected {target.text!r}.",
        )

    return score


# ---------------------------------------------------------------------------
# A SCRIPTED FAKE MODEL
# ---------------------------------------------------------------------------
# Reads the problem out of the prompt, works it out with Python, and replies
# in the requested format. It adds 1 to the answer on "hard" problems so
# that some samples come out wrong.
def scripted_model(
    messages: list[ChatMessage],
    tools: list[ToolInfo],
    tool_choice: ToolChoice,
    config: GenerateConfig,
) -> ModelOutput:
    prompt = messages[-1].text
    expression = prompt.split("What is", 1)[1].split("?", 1)[0]
    # eval() on model input would be dangerous in real code. Here the input
    # comes from our own data file, and builtins are disabled anyway.
    result = eval(expression, {"__builtins__": {}})
    if "(" in expression:  # the "hard" question: be wrong on purpose
        result += 1
    result = int(result) if float(result).is_integer() else result
    return ModelOutput.from_content(
        model="scripted",
        content=f"Working: {expression.strip()} = {result}\n\nANSWER: {result}",
    )


@task
def arithmetic(scripted: bool = True):
    return Task(
        dataset=json_dataset(str(DATA_FILE), sample_fields=record_to_sample),
        solver=[
            # system_message() adds a system prompt at the start of the conversation.
            system_message("You are a careful maths tutor."),
            prompt_template(TEMPLATE),
            generate(),
        ],
        # A list of scorers: each sample gets one score from each, and the
        # results table shows each scorer's metrics side by side.
        #   - match(numeric=True) is built in. It looks for the target at the
        #     END of the reply and compares as numbers, so "42.0" matches "42".
        #   - answer_line() is our custom scorer above.
        scorer=[match(numeric=True), answer_line()],
        model=get_model("mockllm/model", custom_outputs=scripted_model) if scripted else None,
        # To have a second model mark the answers (an "LLM judge"), you would
        # add model_graded_qa() to the scorer list. It needs a real model, so
        # it is left out here.
    )

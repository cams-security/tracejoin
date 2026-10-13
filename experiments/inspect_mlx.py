"""
An all in one starting point for my experiments with MLX and Inspect.
We are going to run inference locally with models from HuggingFace. They will be served via MLX-lm

Then we will create some evaluations with Inspect, then determine ways to flag and detect activity that breaks our rules. 




"""



import argparse
import time

import os


import mlx.core as mx

from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate

from mlx.utils import tree_flatten
from mlx_lm import generate, load, stream_generate
from mlx_lm.sample_utils import make_sampler



def inference(model_name: str) -> None:

    print("--- MLX Inference Test -----------------------------------------------------")
    print("This script shows how to use MLX to run inference with a HuggingFace model, and how to use Inspect to evaluate it.")
    print(" We are using model ", model_name)
    

    # load() downloads the model the first time (cached in ~/.cache/huggingface)
    # and returns the model plus its TOKENIZER.
    print("Loading (first run downloads the model)...")
    model, tokenizer = load(model_name)
    params = sum(v.size for _, v in tree_flatten(model.parameters()))
    print(f"Loaded. Parameter arrays hold {params / 1e6:,.0f} million numbers "
          "(4-bit models pack 8 weights per number, so the real count is higher).")

    # --- Tokens --------------------------------------------------------------
    # Models don't read characters or words; they read TOKENS: chunks of text
    # from a fixed vocabulary, each with an id number. Context limits, speed
    # and API prices are all counted in tokens.
    text = "Honeytokens catch unauthorised access."
    ids = tokenizer.encode(text, add_special_tokens=False)
    pieces = [tokenizer.decode([i]) for i in ids]
    print(f"\nTokens for {text!r}:")
    print(f"  {len(ids)} tokens: {pieces}")

    # --- Chat templates ------------------------------------------------------
    # A chat model was trained on conversations wrapped in special marker
    # tokens. The CHAT TEMPLATE turns a list of messages into that exact text.
    # Inspect and mlx_lm.server do this for you; here you can see it.
    messages = [{"role": "user", "content": "In one sentence, what is a honeytoken?"}]
    prompt = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,  # end with the marker that says "assistant speaks now"
        tokenize=False,
        enable_thinking=False,  # Qwen3 option: skip the long hidden reasoning section
    )
    print("\nThe chat template turns the message into this raw text:")
    print("  " + prompt.replace("\n", "\n  "))

    # --- Generating, and how fast ---------------------------------------------
    # stream_generate yields one response per token, with speed statistics.
    #   prompt_tps     - "prefill": how fast the model reads your prompt
    #   generation_tps - "decode": how fast it writes new tokens
    # The first call includes one-off warm-up work, so its prompt speed reads
    # low. Long prompts (like an agent's growing transcript) show the real rate.
    #
    # Also notice whether the answer is RIGHT. A 0.6B model is fast but often
    # confidently wrong. Use it to test plumbing, not to judge behaviour.
    print("Reply: ", end="", flush=True)
    last = None
    for last in stream_generate(model, tokenizer, prompt, max_tokens=80):
        print(last.text, end="", flush=True)
    print(f"\n\n  prompt read at   {last.prompt_tps:7.1f} tokens/sec ({last.prompt_tokens} tokens)")
    print(f"  reply written at {last.generation_tps:7.1f} tokens/sec ({last.generation_tokens} tokens)")
    print(f"  peak memory      {last.peak_memory:7.2f} GB")

    # --- Temperature -----------------------------------------------------------
    # At each step the model scores every possible next token. TEMPERATURE
    # controls how it picks one:
    #   temp 0   - always the top-scoring token, so the same prompt gives the same reply
    #   temp ~1  - samples at random by score, so replies vary run to run
    # For evals this matters: temtp 0 gives repeatable results; higher temps
    # need several runs (Inspect's --epochs) to get a fair average.
    print("\nSame prompt, three runs at each temperature:")
    for temp in [0.0, 1.0]:
        sampler = make_sampler(temp=temp)
        replies = {generate(model, tokenizer, prompt, max_tokens=20, sampler=sampler).strip()
                   for _ in range(3)}
        print(f"  temp {temp}: {len(replies)} distinct repl{'y' if len(replies) == 1 else 'ies'}")
        for reply in replies:
            print(f"     - {reply[:70]!r}")

    print("\nTo use this model from Inspect, serve it:")
    print(f"  mlx_lm.server --model {model_name} --port 8080")
    print("then see the 'Using it with Inspect' section of MLX_GUIDE.md.")  
    
def main() -> None:    
    parser = argparse.ArgumentParser(description="A simple test using MLX for Inspect.")
    parser.add_argument("--model", default="mlx-community/Qwen3-0.6B-4bit",
                        help="Hugging Face model for part 6 (default: a 350 MB test model) ex: --model mlx-community/Qwen3-0.6B-4bit")
    args = parser.parse_args()




if __name__ == "__main__":
    main()



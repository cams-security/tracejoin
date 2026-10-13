"""
A guided tour of the parts of Apple MLX that matter for running local models
in Inspect evals. Read MLX_GUIDE.md alongside it for the background.

HOW TO RUN (from the ResearchShop/ folder, with the .venv active)
    python mlx_sandbox/mlx_tour.py            # parts 1-5, no download, ~10 seconds
    python mlx_sandbox/mlx_tour.py --llm      # also part 6: run a real model (downloads ~350 MB once)
    python mlx_sandbox/mlx_tour.py --llm --model mlx-community/Qwen3-8B-4bit   # a bigger model (~4.6 GB)

THE PARTS
    1. Arrays and dtypes        what a "tensor" is and why number formats matter
    2. Lazy evaluation          why MLX code seems instant until it suddenly isn't
    3. Unified memory           CPU and GPU sharing one pool of memory
    4. Quantization             how a 16 GB model fits in 4.5 GB
    5. Will a model fit?        memory maths for your machine
    6. Running an LLM (--llm)   tokens, chat templates, temperature, speed

Things the MLX guide covers that you can skip for now are listed at the
bottom of MLX_GUIDE.md.
"""

import argparse
import time

import mlx.core as mx

GB = 1024**3


def heading(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def name(dtype: mx.Dtype) -> str:
    """'mlx.core.float32' -> 'float32'."""
    return str(dtype).removeprefix("mlx.core.")


def timed(fn) -> tuple[object, float]:
    """Run fn() and return (result, milliseconds taken)."""
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000


# ---------------------------------------------------------------------------
# 1. ARRAYS AND DTYPES
# ---------------------------------------------------------------------------
# An mx.array is a grid of numbers, like a NumPy array. ML people call these
# "tensors". A language model is, physically, a few hundred of these grids
# (the "weights"), and running it is mostly multiplying them together.
#
# Every array has a dtype: the number format each element is stored in. The
# dtype sets how many bytes each number takes, so it decides how much memory
# a model needs.
def part1_arrays() -> None:
    heading("1. Arrays and dtypes")

    a = mx.array([1, 2, 3, 4])
    b = mx.array([1.0, 2.0, 3.0, 4.0])
    print(f"mx.array([1, 2, 3, 4])         -> shape {a.shape}, dtype {name(a.dtype)}")
    print(f"mx.array([1.0, 2.0, 3.0, 4.0]) -> shape {b.shape}, dtype {name(b.dtype)}")

    # The same 1,000 x 1,000 grid of numbers in each common format:
    print("\nOne 1000 x 1000 weight matrix in different dtypes:")
    weights = mx.random.normal((1000, 1000))
    for dtype in [mx.float32, mx.float16, mx.bfloat16]:
        w = weights.astype(dtype)
        print(f"  {name(dtype):9} {w.itemsize} bytes/number  -> {w.nbytes / 1e6:5.1f} MB")

    # float16 and bfloat16 both use 2 bytes but split them differently:
    #   float16  - more precision, but overflows above ~65,504
    #   bfloat16 - less precision, same range as float32 (no overflow)
    # Most modern LLMs ship in bfloat16.
    big = mx.array([70000.0])
    print(f"\n70000 as float16:  {big.astype(mx.float16).item()}   <- overflowed")
    print(f"70000 as bfloat16: {big.astype(mx.bfloat16).item()}  <- fits, slightly rounded")


# ---------------------------------------------------------------------------
# 2. LAZY EVALUATION
# ---------------------------------------------------------------------------
# MLX doesn't compute anything when you write `c = a @ b`. It writes down a
# recipe (a "compute graph") and only cooks it when the result is needed:
# when you call mx.eval(), print the array, call .item(), or convert it to
# NumPy.
#
# Why you care: if you time MLX code without forcing evaluation, you are
# timing how long it took to write the recipe, not to do the work. This
# matters when you measure model speed or line up timestamps with telemetry.
def part2_lazy() -> None:
    heading("2. Lazy evaluation")

    a = mx.random.normal((4096, 4096))
    b = mx.random.normal((4096, 4096))
    mx.eval(a, b)  # make sure the inputs exist before timing

    def build():
        c = a
        for _ in range(20):
            c = c @ b  # matrix multiply, 20 times in a row
        return c

    c, build_ms = timed(build)
    print(f"Building 20 matrix multiplies: {build_ms:8.2f} ms   <- nothing computed yet")
    _, eval_ms = timed(lambda: mx.eval(c))
    print(f"mx.eval(c):                    {eval_ms:8.2f} ms   <- the real work")

    # The same rule bites when you do NOT need a result: MLX skips it.
    unused = a @ b @ b @ b  # never evaluated, so never computed
    del unused
    print("\nA result you never use is never computed (but building its recipe still costs a little).")


# ---------------------------------------------------------------------------
# 3. UNIFIED MEMORY
# ---------------------------------------------------------------------------
# On a typical PC, the GPU has its own memory (VRAM), so data must be copied
# from system RAM to the GPU and back. On Apple silicon the CPU and GPU share
# one pool. MLX arrays live in that pool, and you pick which processor runs
# each OPERATION rather than moving the DATA.
#
# Why you care: a 48 GB Mac can run models that would need a very expensive
# GPU on a PC, because almost all of that 48 GB is usable by the GPU.
def part3_unified_memory() -> None:
    heading("3. Unified memory")

    info = mx.device_info()
    print(f"Chip:                         {info['device_name']}")
    print(f"Total memory:                 {info['memory_size'] / GB:.1f} GB")
    print(f"GPU working-set limit:        {info['max_recommended_working_set_size'] / GB:.1f} GB  "
          "<- what macOS lets the GPU use comfortably")
    print(f"Default device:               {mx.default_device()}")

    a = mx.random.normal((2048, 2048))
    b = mx.random.normal((2048, 2048))
    mx.eval(a, b)

    # The SAME arrays, no copying, run on either processor via `stream=`.
    for name, device in [("CPU", mx.cpu), ("GPU", mx.gpu)]:
        mx.eval(mx.matmul(a, b, stream=device))  # warm-up run
        _, ms = timed(lambda: mx.eval(mx.matmul(a, b, stream=device)))
        print(f"2048x2048 matmul on {name}:       {ms:8.2f} ms")
    print("Same arrays, no copy. The GPU wins on big maths like this; the CPU can win on tiny steps.")


# ---------------------------------------------------------------------------
# 4. QUANTIZATION
# ---------------------------------------------------------------------------
# Quantization stores each weight in fewer bits (4 or 8 instead of 16).
# Weights are split into small groups (here 64 numbers each). Each group
# stores a scale and an offset, plus one low-bit code per weight. To use a
# weight, MLX unpacks it as  code * scale + offset.
#
# The result: about 3.5x less memory, slightly less accurate numbers. The
# "-4bit" in a model name like "Qwen3-8B-4bit" means this was done to it.
def part4_quantization() -> None:
    heading("4. Quantization")

    w = mx.random.normal((4096, 4096)).astype(mx.bfloat16)
    print(f"Original bfloat16 matrix:      {w.nbytes / 1e6:6.1f} MB")

    for bits in [8, 4]:
        packed, scales, offsets = mx.quantize(w, group_size=64, bits=bits)
        size = packed.nbytes + scales.nbytes + offsets.nbytes
        restored = mx.dequantize(packed, scales, offsets, group_size=64, bits=bits)
        error = mx.mean(mx.abs(restored - w)) / mx.mean(mx.abs(w))
        print(f"{bits}-bit quantized:               {size / 1e6:6.1f} MB "
              f"({w.nbytes / size:.1f}x smaller), average error {error.item():.1%}")

    # (The error looks big because this matrix is random noise. Real model
    # weights quantize better, and models tolerate small errors well.)
    print("4-bit is the usual sweet spot on a Mac: a small quality loss for a big memory saving.")


# ---------------------------------------------------------------------------
# 5. WILL A MODEL FIT?
# ---------------------------------------------------------------------------
# Model sizes are quoted in parameters ("8B" = 8 billion weights). The rough
# memory needed is  parameters x bytes per weight, plus headroom for the
# KV cache (the model's memory of the conversation so far, which grows with
# context length) and for macOS itself.
def part5_will_it_fit() -> None:
    heading("5. Will a model fit on this machine?")

    limit = mx.device_info()["max_recommended_working_set_size"] / GB
    # Bytes per weight, including the per-group scales and offsets.
    formats = {"bf16": 2.0, "8-bit": 1.06, "4-bit": 0.56}
    print(f"GPU working-set limit: {limit:.0f} GB. Rough weight sizes (add ~20% for context):\n")
    print(f"{'model':>8} | " + " | ".join(f"{f:>12}" for f in formats))
    for billions in [0.6, 8, 30, 70]:
        cells = []
        for bytes_per in formats.values():
            size = billions * 1e9 * bytes_per / GB
            fits = size * 1.2 < limit
            cells.append(f"{size:5.1f} GB {'ok' if fits else 'NO':>3}")
        print(f"{billions:>7}B | " + " | ".join(cells))

    # MLX can tell you what it actually used. Handy for logging next to eval runs.
    print(f"\nPeak memory MLX has used in this script so far: {mx.get_peak_memory() / GB:.2f} GB")


# ---------------------------------------------------------------------------
# 6. RUNNING AN LLM WITH mlx-lm
# ---------------------------------------------------------------------------
# mlx-lm is a separate package built on MLX. It downloads models from
# Hugging Face, runs them, and can serve them over an OpenAI-style HTTP API.
# That server is how Inspect talks to a local model (see MLX_GUIDE.md).
def part6_llm(model_name: str) -> None:
    from mlx.utils import tree_flatten
    from mlx_lm import generate, load, stream_generate
    from mlx_lm.sample_utils import make_sampler

    heading(f"6. Running an LLM: {model_name}")

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
    # For evals this matters: temp 0 gives repeatable results; higher temps
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
    parser = argparse.ArgumentParser(description="A guided tour of Apple MLX.")
    parser.add_argument("--llm", action="store_true", help="also run part 6 (downloads a model)")
    parser.add_argument("--model", default="mlx-community/Qwen3-0.6B-4bit",
                        help="Hugging Face model for part 6 (default: a 350 MB test model)")
    args = parser.parse_args()

    part1_arrays()
    part2_lazy()
    part3_unified_memory()
    part4_quantization()
    part5_will_it_fit()
    if args.llm:
        part6_llm(args.model)
    else:
        print("\nSkipped part 6. Run with --llm to load and run a real model.")


if __name__ == "__main__":
    main()

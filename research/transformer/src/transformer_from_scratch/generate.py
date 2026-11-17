"""Generate text from a saved educational GPT checkpoint."""

import argparse
from pathlib import Path

import torch

from .checkpoint import load_checkpoint
from .tokenizer import ByteTokenizer


def generate_text(
    checkpoint_path: str | Path,
    prompt: str,
    *,
    max_new_tokens: int = 160,
    temperature: float = 0.8,
    top_k: int = 40,
    seed: int = 1337,
    device: str = "cpu",
) -> str:
    torch.manual_seed(seed)
    model, _payload = load_checkpoint(checkpoint_path, device=device)
    tokenizer = ByteTokenizer()
    prompt_ids = tokenizer.encode(prompt, add_bos=True)
    input_ids = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    output = model.generate(
        input_ids,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_k=top_k,
        eos_token_id=tokenizer.eos_token_id,
    )[0].tolist()
    return tokenizer.decode(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="outputs/best.pt")
    parser.add_argument("--prompt", default="The small machine")
    parser.add_argument("--max-new-tokens", type=int, default=160)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else (
        "cpu" if args.device == "auto" else args.device
    )
    print(generate_text(
        args.checkpoint, args.prompt, max_new_tokens=args.max_new_tokens,
        temperature=args.temperature, top_k=args.top_k, seed=args.seed, device=device,
    ))


if __name__ == "__main__":
    main()

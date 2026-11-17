"""Train a tiny GPT decoder on the bundled original text corpus."""

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path

import torch

from .config import GPTConfig
from .data import load_corpus, make_next_token_dataset, split_token_stream
from .model import GPTLanguageModel
from .tokenizer import ByteTokenizer
from .training import choose_device, seed_everything, train_model

MODULE_ROOT = Path(__file__).resolve().parents[2]


def write_loss_svg(history: dict[str, list[float]], path: Path) -> None:
    """Write a dependency-free SVG loss plot."""
    width, height = 760, 420
    left, right, top, bottom = 70, 25, 30, 55
    values = history["train_loss"] + history["validation_loss"]
    low, high = min(values), max(values)
    span = max(high - low, 1e-6)

    def coords(series: list[float]) -> str:
        points = []
        for index, value in enumerate(series):
            x = left + (width - left - right) * (index / max(len(series) - 1, 1))
            y = top + (height - top - bottom) * (1 - (value - low) / span)
            points.append(f"{x:.1f},{y:.1f}")
        return " ".join(points)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<text x="{width/2}" y="22" text-anchor="middle" font-family="sans-serif" font-size="17">Training and validation cross-entropy</text>
<path d="M{left},{top} V{height-bottom} H{width-right}" fill="none" stroke="#333" stroke-width="1.5"/>
<polyline points="{coords(history['train_loss'])}" fill="none" stroke="#2369bd" stroke-width="2.5"/>
<polyline points="{coords(history['validation_loss'])}" fill="none" stroke="#d97706" stroke-width="2.5"/>
<text x="{left}" y="{height-16}" font-family="sans-serif" font-size="12">epoch 1</text>
<text x="{width-right}" y="{height-16}" text-anchor="end" font-family="sans-serif" font-size="12">epoch {len(history['train_loss'])}</text>
<text x="18" y="{height/2}" transform="rotate(-90 18 {height/2})" text-anchor="middle" font-family="sans-serif" font-size="12">loss (nats/token)</text>
<line x1="{width-240}" y1="38" x2="{width-210}" y2="38" stroke="#2369bd" stroke-width="3"/><text x="{width-204}" y="42" font-family="sans-serif" font-size="12">train</text>
<line x1="{width-140}" y1="38" x2="{width-110}" y2="38" stroke="#d97706" stroke-width="3"/><text x="{width-104}" y="42" font-family="sans-serif" font-size="12">validation</text>
</svg>'''
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(MODULE_ROOT / "data" / "tiny_corpus.txt"))
    parser.add_argument("--output-dir", default=str(MODULE_ROOT / "outputs"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--amp", action="store_true", help="Enable CUDA autocast when running on CUDA")
    args = parser.parse_args()

    seed_everything(args.seed)
    device = choose_device(args.device)
    run_started = time.perf_counter()
    tokenizer = ByteTokenizer()
    config = GPTConfig(vocab_size=tokenizer.vocab_size)
    corpus = load_corpus(args.data)
    train_tokens, validation_tokens = split_token_stream(corpus, tokenizer)
    train_dataset = make_next_token_dataset(train_tokens, config.block_size)
    validation_dataset = make_next_token_dataset(validation_tokens, config.block_size)

    model = GPTLanguageModel(config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "best.pt"
    history = train_model(
        model, train_dataset, validation_dataset,
        epochs=args.epochs, batch_size=args.batch_size,
        learning_rate=args.learning_rate, seed=args.seed,
        device=device, checkpoint_path=str(checkpoint_path), amp=args.amp,
    )
    run_metadata = {
        "seed": args.seed,
        "device": str(device),
        "torch_version": torch.__version__,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "not reported by platform module",
        "cpu_count": os.cpu_count(),
        "torch_cpu_threads": torch.get_num_threads(),
        "cuda_available": torch.cuda.is_available(),
        "cuda_runtime_version": torch.version.cuda,
        "gpu_name": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "amp_requested": args.amp,
        "corpus_sha256": hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
        "config": config.to_dict(),
        "train_windows": len(train_dataset),
        "validation_windows": len(validation_dataset),
        "epochs_completed": len(history["train_loss"]),
        **history,
    }
    run_metadata["duration_seconds"] = round(time.perf_counter() - run_started, 3)
    (output_dir / "training_history.json").write_text(
        json.dumps(run_metadata, indent=2), encoding="utf-8"
    )
    write_loss_svg(history, output_dir / "loss_curve.svg")
    print(f"device={device} torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    print(f"train_windows={len(train_dataset)} validation_windows={len(validation_dataset)}")
    print(f"initial train loss={history['train_loss'][0]:.4f}; final train loss={history['train_loss'][-1]:.4f}")
    print(f"initial validation loss={history['validation_loss'][0]:.4f}; best validation loss={min(history['validation_loss']):.4f}")
    print(f"checkpoint={checkpoint_path}")


if __name__ == "__main__":
    main()

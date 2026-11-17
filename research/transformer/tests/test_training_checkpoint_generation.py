from pathlib import Path

import torch
from torch.utils.data import DataLoader

from transformer_from_scratch.checkpoint import load_checkpoint, save_checkpoint
from transformer_from_scratch.config import GPTConfig
from transformer_from_scratch.data import make_next_token_dataset
from transformer_from_scratch.generate import generate_text
from transformer_from_scratch.model import GPTLanguageModel
from transformer_from_scratch.tokenizer import ByteTokenizer
from transformer_from_scratch.training import evaluate, seed_everything, train_model


def tiny_datasets(block_size=8):
    tokenizer = ByteTokenizer()
    text = ("tiny models learn patterns. " * 80)
    ids = tokenizer.encode(text, add_bos=True, add_eos=True)
    split = int(len(ids) * 0.8)
    train = make_next_token_dataset(ids[:split], block_size)
    validation = make_next_token_dataset(ids[split - 1 :], block_size)
    return train, validation


def test_train_validation_and_checkpoint_round_trip(tmp_path: Path):
    seed_everything(123)
    config = GPTConfig(vocab_size=258, block_size=8, n_embd=16, n_head=4, n_layer=1, dropout=0.0)
    model = GPTLanguageModel(config)
    train, validation = tiny_datasets(block_size=config.block_size)
    val_before = evaluate(model, DataLoader(validation, batch_size=8), torch.device("cpu"))
    checkpoint = tmp_path / "tiny.pt"
    history = train_model(
        model, train, validation, epochs=2, batch_size=8,
        learning_rate=1e-3, seed=123, device="cpu", checkpoint_path=str(checkpoint),
    )
    assert len(history["train_loss"]) == 2
    assert len(history["validation_loss"]) == 2
    assert checkpoint.exists()
    loaded, payload = load_checkpoint(checkpoint)
    assert payload["config"]["block_size"] == config.block_size
    assert loaded(torch.randint(0, 258, (1, 8)))[0].shape == (1, 8, 258)
    assert evaluate(loaded, DataLoader(validation, batch_size=8), torch.device("cpu")) <= val_before + 0.5


def test_generation_appends_tokens_and_restores_training_mode():
    config = GPTConfig(vocab_size=258, block_size=8, n_embd=16, n_head=4, n_layer=1, dropout=0.0)
    model = GPTLanguageModel(config)
    model.train()
    prompt = torch.tensor([[256, 65, 66]])
    output = model.generate(prompt, max_new_tokens=4, temperature=1.0, top_k=5)
    assert output.shape[1] == prompt.shape[1] + 4
    assert model.training is True


def test_save_checkpoint_and_generate_text(tmp_path: Path):
    config = GPTConfig(vocab_size=258, block_size=16, n_embd=16, n_head=4, n_layer=1, dropout=0.0)
    model = GPTLanguageModel(config)
    checkpoint = tmp_path / "model.pt"
    save_checkpoint(checkpoint, model, metadata={"note": "unit test"})
    text = generate_text(checkpoint, "Hi", max_new_tokens=5, seed=7)
    assert isinstance(text, str)
    assert text.startswith("Hi")

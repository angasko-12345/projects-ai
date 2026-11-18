"""
tiny_llm.py - A minimal GPT-style language model, built from scratch with PyTorch.

What it does:
  1. Loads training text (input.txt if present, otherwise a built-in sample).
  2. Builds a character-level tokenizer.
  3. Trains a small decoder-only transformer to predict the next character.
  4. Generates new text, then lets you type prompts interactively.

Run:  python tiny_llm.py
"""

import os
import torch
import torch.nn as nn
import torch.nn.functional as F

# ----------------------------------------------------------------------------
# Hyperparameters (small enough to train on a CPU in a few minutes)
# ----------------------------------------------------------------------------
batch_size    = 32      # sequences per training step
block_size    = 256      # maximum context length (characters)
max_iters     = 300   # training steps
eval_interval = 300     # how often to report loss
eval_iters    = 100      # batches used to estimate loss
learning_rate = 3e-4
n_embd        = 256     # embedding dimension
n_head        = 8       # attention heads
n_layer       = 6       # transformer blocks
dropout       = 0.1
device        = "cuda" if torch.cuda.is_available() else "cpu"

torch.manual_seed(1337)

# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
SAMPLE_TEXT = """
The sun rose slowly over the quiet hills, and the village began to wake.
A baker opened his shop and the smell of fresh bread drifted down the street.
Children ran past the fountain, laughing, while an old man fed the birds.
The river ran cold and clear beneath the stone bridge, as it always had.

In the evening the sky turned gold, then red, then a deep and gentle blue.
People gathered in the square to talk about the day, the harvest, and the rain.
The baker sold the last of his bread, and the old man walked home alone.
The stars came out one by one, and the village fell asleep again.

A machine can learn to write by reading many words and guessing the next one.
It does not know the meaning of the sun or the river or the bread.
It only learns which letters tend to follow which, and it gets better with practice.
With enough data and enough time, its guesses begin to look like language.

The model reads a little, guesses a letter, and checks if it was right.
When it is wrong, it changes itself a little bit so it will be less wrong next time.
After thousands of small changes, it can write words, then lines, then stories.
This is how a language model learns: one small correction at a time. 1 + 1 = 2.
The solar system is made up of eight (8) planets, Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, and Neptune.
"""

if os.path.exists("input.txt"):
    with open("input.txt", encoding="utf-8") as f:
        text = f.read()
    print("Loaded training text from input.txt")
else:
    # Repeat the sample so there is enough data to train on.
    text = SAMPLE_TEXT * 20
    print("No input.txt found - using built-in sample text.")

# Character-level tokenizer
chars = sorted(set(text))
vocab_size = len(chars)
stoi = {ch: i for i, ch in enumerate(chars)}
itos = {i: ch for i, ch in enumerate(chars)}

def encode(s):
    """String -> list of token ids (unknown characters are skipped)."""
    return [stoi[c] for c in s if c in stoi]

def decode(ids):
    """List of token ids -> string."""
    return "".join(itos[i] for i in ids)

data = torch.tensor(encode(text), dtype=torch.long)
n = int(0.9 * len(data))
train_data = data[:n]
val_data = data[n:]

assert len(val_data) > block_size + 1, (
    "Not enough text for validation. Provide a longer input.txt."
)

def get_batch(split):
    """Sample a random batch of (input, target) sequences."""
    d = train_data if split == "train" else val_data
    ix = torch.randint(len(d) - block_size - 1, (batch_size,))
    x = torch.stack([d[i : i + block_size] for i in ix])
    y = torch.stack([d[i + 1 : i + block_size + 1] for i in ix])
    return x.to(device), y.to(device)

# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------
class Head(nn.Module):
    """A single head of causal (masked) self-attention."""

    def __init__(self, head_size):
        super().__init__()
        self.key = nn.Linear(n_embd, head_size, bias=False)
        self.query = nn.Linear(n_embd, head_size, bias=False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, C = x.shape
        k = self.key(x)    # (B, T, hs)
        q = self.query(x)  # (B, T, hs)
        # Attention scores, scaled to keep softmax stable
        wei = q @ k.transpose(-2, -1) * (k.shape[-1] ** -0.5)  # (B, T, T)
        # Causal mask: each position may only look at itself and the past
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))
        wei = F.softmax(wei, dim=-1)
        wei = self.dropout(wei)
        v = self.value(x)  # (B, T, hs)
        return wei @ v     # (B, T, hs)


class MultiHeadAttention(nn.Module):
    """Several attention heads in parallel, then projected back to n_embd."""

    def __init__(self, num_heads, head_size):
        super().__init__()
        self.heads = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(num_heads * head_size, n_embd)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        return self.dropout(self.proj(out))


class FeedForward(nn.Module):
    """Position-wise MLP."""

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.GELU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    """Transformer block: attention + MLP, each with pre-LayerNorm and a residual."""

    def __init__(self):
        super().__init__()
        head_size = n_embd // n_head
        self.ln1 = nn.LayerNorm(n_embd)
        self.sa = MultiHeadAttention(n_head, head_size)
        self.ln2 = nn.LayerNorm(n_embd)
        self.ffwd = FeedForward()

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x


class TinyGPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.token_embedding = nn.Embedding(vocab_size, n_embd)
        self.position_embedding = nn.Embedding(block_size, n_embd)
        self.blocks = nn.Sequential(*[Block() for _ in range(n_layer)])
        self.ln_f = nn.LayerNorm(n_embd)
        self.lm_head = nn.Linear(n_embd, vocab_size)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        tok = self.token_embedding(idx)                                   # (B, T, C)
        pos = self.position_embedding(torch.arange(T, device=idx.device))  # (T, C)
        x = tok + pos
        x = self.blocks(x)
        x = self.ln_f(x)
        logits = self.lm_head(x)                                          # (B, T, vocab)

        loss = None
        if targets is not None:
            loss = F.cross_entropy(logits.view(B * T, -1), targets.view(B * T))
        return logits, loss

    @torch.no_grad()
    def generate(self, idx, max_new_tokens, temperature=1.0):
        """Autoregressively sample new tokens, one at a time."""
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -block_size:]          # crop to the context window
            logits, _ = self(idx_cond)
            logits = logits[:, -1, :] / temperature  # last position only
            probs = F.softmax(logits, dim=-1)
            next_id = torch.multinomial(probs, num_samples=1)
            idx = torch.cat([idx, next_id], dim=1)
        return idx

# ----------------------------------------------------------------------------
# Training
# ----------------------------------------------------------------------------
@torch.no_grad()
def estimate_loss(model):
    model.eval()
    out = {}
    for split in ("train", "val"):
        losses = torch.zeros(eval_iters)
        for k in range(eval_iters):
            x, y = get_batch(split)
            _, loss = model(x, y)
            losses[k] = loss.item()
        out[split] = losses.mean().item()
    model.train()
    return out


def main():
    model = TinyGPT().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Device: {device} | Vocab size: {vocab_size} | Parameters: {n_params:,}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)

    for it in range(max_iters + 1):
        if it % eval_interval == 0:
            losses = estimate_loss(model)
            print(f"step {it:5d} | train loss {losses['train']:.4f} | val loss {losses['val']:.4f}")

        if it == max_iters:
            break

        xb, yb = get_batch("train")
        _, loss = model(xb, yb)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

    torch.save(model.state_dict(), "tiny_llm.pt")
    print("\nModel saved to tiny_llm.pt")

    # Unprompted sample
    model.eval()
    start_id = stoi["\n"] if "\n" in stoi else 0
    context = torch.tensor([[start_id]], dtype=torch.long, device=device)
    print("\n--- Sample generation ---")
    print(decode(model.generate(context, max_new_tokens=400, temperature=0.8)[0].tolist()))

    # Interactive prompting
    print("\n--- Interactive mode (press Enter on an empty line to quit) ---")
    while True:
        try:
            prompt = input("\nPrompt: ")
        except (EOFError, KeyboardInterrupt):
            break
        if prompt == "":
            break
        ids = encode(prompt)
        if not ids:
            print("(None of those characters appear in the training text. Try again.)")
            continue
        context = torch.tensor([ids], dtype=torch.long, device=device)
        out = model.generate(context, max_new_tokens=200, temperature=0.8)
        print(decode(out[0].tolist()))


if __name__ == "__main__":
    main()

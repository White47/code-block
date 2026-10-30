"""NanoGPT-like composition without importing torch during acceptance."""

import torch
from torch import nn

raise RuntimeError("Never import or execute this analyzed fixture")


class Block(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(width, 2)
        self.ffn = nn.Sequential(nn.Linear(width, width * 4), nn.GELU(), nn.Linear(width * 4, width))

    def forward(self, x):
        normalized = self.norm(x)
        attended, _ = self.attention(normalized, normalized, normalized)
        return x + attended + self.ffn(normalized)


class TinyGPT(nn.Module):
    def __init__(self, vocabulary, width, count):
        super().__init__()
        self.tokens = nn.Embedding(vocabulary, width)
        self.blocks = nn.ModuleList([Block(width) for _ in range(count)])
        self.head = nn.Linear(width, vocabulary)

    def forward(self, tokens):
        x = self.tokens(tokens)
        for block in self.blocks:
            x = block(x)
        return self.head(x)


def inference(model, tokens):
    with torch.no_grad():
        return model(tokens)

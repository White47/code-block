import torch.nn as nn


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.projection = nn.Linear(8, 8)

    def forward(self, x):
        return self.projection(x)


class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.ModuleList([Block() for _ in range(2)])

    def forward(self, x):
        for layer in self.layers:
            x = layer(x)
        return x


class Classifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(100, 8)
        self.encoder = Encoder()
        self.head = nn.Linear(8, 2)

    def forward(self, tokens):
        hidden = self.embedding(tokens)
        hidden = self.encoder(hidden)
        return self.head(hidden)


class Baseline(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(8, 8), nn.ReLU())

    def forward(self, x):
        return self.layers(x)

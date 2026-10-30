"""Explicit saved training stages; never imported/executed by tests."""

import torch

raise RuntimeError("Never import or execute this analyzed fixture")


def train_epoch(model, loader, optimizer, criterion, epochs):
    model.train()
    for _epoch in range(epochs):
        for inputs, targets in loader:
            optimizer.zero_grad()
            prediction = model(inputs)
            loss = criterion(prediction, targets)
            loss.backward()
            optimizer.step()
    torch.save(model.state_dict(), "OUTPUT_MUST_NOT_EXIST")
    return loss

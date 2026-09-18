import torch
import torch.nn as nn

from model import Baseline, Classifier


def train_epoch(loader, epochs):
    model = Classifier()
    baseline = Baseline()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    criterion = nn.CrossEntropyLoss()
    model.train()
    for epoch in range(epochs):
        for batch, target in loader:
            optimizer.zero_grad()
            prediction = model(batch)
            loss = criterion(prediction, target)
            loss.backward()
            optimizer.step()
    return model


def evaluate(loader):
    model = Classifier()
    model.eval()
    with torch.no_grad():
        for batch, target in loader:
            prediction = model(batch)
            loss = prediction
    return loss


def dynamic_call(callback, value):
    return callback(value)

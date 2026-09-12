"""Research-editable method: preserve the runner/evaluator and dataset assets."""

import torch.nn.functional as F
from torchvision import transforms


def training_transform():
    return transforms.Compose([
        transforms.RandomCrop(32, padding=4), transforms.RandomHorizontalFlip(),
        transforms.ToTensor(), transforms.Normalize((0.5,) * 3, (0.5,) * 3),
    ])


def training_loss(model, images, labels):
    return F.cross_entropy(model(images), labels)

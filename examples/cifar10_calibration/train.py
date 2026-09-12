"""One measured baseline/candidate seed. No downloads, test-set access or retry loop."""

import argparse
import json
import os
import random
import time
from pathlib import Path

from protocol import calibration_metrics, make_split, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=os.environ.get("SIMPLE_AR_OUTPUT_DIR"),
                        help="Defaults to the canonical executor's per-invocation output directory.")
    parser.add_argument("--seed", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--probe-steps", type=int, default=0,
                        help="Throughput probe only; does not emit experiment metrics.")
    args = parser.parse_args()
    if args.output is None:
        parser.error("--output is required outside the canonical executor")
    if not 1 <= args.epochs <= 30 or args.batch_size < 1 or args.probe_steps < 0:
        parser.error("epochs must be 1..30, batch-size positive, probe-steps nonnegative")
    import torch
    import torchvision
    from torch import nn
    from torch.utils.data import DataLoader, Subset
    from torchvision import datasets, models, transforms
    from method import training_loss, training_transform

    torch.set_num_threads(2)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    # Do not silently switch a requested CUDA experiment to CPU.
    if args.device == "cuda":
        torch.cuda.set_device(0)
        torch.cuda.manual_seed_all(args.seed)
        torch.cuda.reset_peak_memory_stats()
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    manifest_path = args.data_root / "low-data-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema"] != "cifar10-low-data.v1":
        raise ValueError("Unsupported asset manifest")
    for name, expected in manifest["files"].items():
        if sha256(args.data_root / datasets.CIFAR10.base_folder / name) != expected:
            raise ValueError(f"Shared data changed: {name}")
    train_data = datasets.CIFAR10(args.data_root, train=True, transform=training_transform())
    if manifest["split"] != make_split(train_data.targets):
        raise ValueError("Asset partition differs from the protected evaluation protocol")
    eval_transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5,) * 3, (0.5,) * 3)])
    eval_data = datasets.CIFAR10(args.data_root, train=True, transform=eval_transform)
    train_loader = DataLoader(Subset(train_data, manifest["split"]["train"]),
        batch_size=args.batch_size, shuffle=True, num_workers=0,
        generator=torch.Generator().manual_seed(args.seed))
    eval_loader = DataLoader(Subset(eval_data, manifest["split"]["validation"]),
        batch_size=args.batch_size, shuffle=False, num_workers=0)
    model = models.resnet18(weights=None, num_classes=10)
    model.conv1 = nn.Conv2d(3, 64, 3, stride=1, padding=1, bias=False)
    model.maxpool = nn.Identity()
    model.to(args.device)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1, momentum=0.9, weight_decay=5e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {"seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size,
        "device": args.device, "torch": torch.__version__, "torchvision": torchvision.__version__,
        "manifest_sha256": sha256(manifest_path), "method_sha256": sha256(Path(__file__).with_name("method.py")),
        "checkpoint_rule": "last_epoch", "probe_steps": args.probe_steps,
        "split": "validation", "official_test_used": False, "workers": 0, "cpu_threads": 2}
    (args.output / "conditions.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    started, steps = time.perf_counter(), 0
    history = []
    # A probe can span epochs, but never silently becomes a scientific run.
    epoch_count = max(args.epochs, (args.probe_steps + len(train_loader) - 1) // len(train_loader))
    for epoch in range(epoch_count):
        model.train()
        loss_sum, count = 0.0, 0
        for images, labels in train_loader:
            images, labels = images.to(args.device), labels.to(args.device)
            optimizer.zero_grad(set_to_none=True)
            loss = training_loss(model, images, labels)
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss")
            loss.backward()
            optimizer.step()
            loss_sum += loss.item() * len(labels)
            count += len(labels)
            steps += 1
            if args.probe_steps and steps >= args.probe_steps:
                elapsed = time.perf_counter() - started
                probe = {"kind": "throughput_probe", "steps": steps, "wall_seconds": elapsed,
                    "seconds_per_step": elapsed / steps,
                    "peak_vram_bytes": torch.cuda.max_memory_allocated() if args.device == "cuda" else None}
                (args.output / "probe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
                print(json.dumps(probe), flush=True)
                return
        scheduler.step()
        if args.probe_steps:
            continue
        model.eval()
        probabilities, targets = [], []
        with torch.inference_mode():
            for images, labels in eval_loader:
                probabilities.extend(model(images.to(args.device)).softmax(dim=1).cpu().tolist())
                targets.extend(labels.tolist())
        metrics = calibration_metrics(probabilities, targets)
        history.append({"epoch": epoch + 1, "train_loss": loss_sum / count, **metrics})
        (args.output / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
        print(f"epoch {epoch + 1}/{args.epochs}: val accuracy {metrics['accuracy']:.6f}", flush=True)
    metrics["wall_seconds"] = time.perf_counter() - started
    metrics["peak_vram_bytes"] = torch.cuda.max_memory_allocated() if args.device == "cuda" else None
    torch.save(model.state_dict(), args.output / "last.pt")
    (args.output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    for key in ("accuracy", "nll", "ece", "wall_seconds"):
        print(f"{key}: {metrics[key]}", flush=True)


if __name__ == "__main__":
    main()

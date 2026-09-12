"""Prepare shared CIFAR assets once; downloading requires --download."""

import argparse
import json
from pathlib import Path

from protocol import make_split, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    from torchvision.datasets import CIFAR10

    data = CIFAR10(args.data_root, train=True, download=args.download)
    content = {"schema": "cifar10-low-data.v1", "dataset_url": CIFAR10.url,
        "upstream_archive_md5": CIFAR10.tgz_md5,
        "files": {name: sha256(args.data_root / CIFAR10.base_folder / name)
                  for name, _ in CIFAR10.train_list},
        "split": make_split(data.targets)}
    manifest = args.data_root / "low-data-v1.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != content:
            raise ValueError("Existing asset manifest differs; inspect it instead of overwriting.")
    else:
        manifest.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared shared assets: {manifest.resolve()}")


if __name__ == "__main__":
    main()

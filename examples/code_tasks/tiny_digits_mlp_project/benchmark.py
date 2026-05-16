from __future__ import annotations

from digits_mlp.train import BenchmarkConfig, run_benchmark


def main() -> None:
    result = run_benchmark(BenchmarkConfig())
    print(f"accuracy: {result['accuracy']:.6f}")
    print(f"macro_f1: {result['macro_f1']:.6f}")
    print(f"train_time_sec: {result['train_time_sec']:.6f}")
    print(f"inference_time_ms: {result['inference_time_ms']:.6f}")
    print(f"parameter_count: {result['parameter_count']}")

    min_accuracy = 0.70
    max_train_time_sec = 5.0
    if result["accuracy"] < min_accuracy:
        raise SystemExit(f"accuracy below benchmark floor: {result['accuracy']:.4f}")
    if result["train_time_sec"] > max_train_time_sec:
        raise SystemExit(
            f"training exceeded local benchmark budget: {result['train_time_sec']:.4f}s"
        )


if __name__ == "__main__":
    main()

"""Server-side CLI/numerical acceptance on two official NIST StRD datasets.

This checks descriptive arithmetic and delivery, not an agent/science score.
Official certified values stay in this evaluator; they are not task inputs.
Uses no model, GPU or additional statistical dependency.
"""
import argparse
import csv
from decimal import Decimal, localcontext
import json
import math
from pathlib import Path
import re
from urllib.request import urlopen

from simple_ar.cli.main import main


def check(root: Path, name: str) -> dict:
    url = f"https://www.itl.nist.gov/div898/strd/univ/data/{name}.dat"
    cache = root / "inputs"
    cache.mkdir(parents=True, exist_ok=True)
    original = cache / f"{name}.dat"
    if not original.exists():
        with urlopen(url, timeout=30) as response:
            original.write_bytes(response.read(1024 * 1024))
    text = original.read_text(encoding="ascii")
    # Different official files have different header lengths. Never hard-code
    # line 61 or include the certified answers among the observations.
    data = re.split(r"(?m)^\s*Data:\s*Y\s*$", text)
    if len(data) != 2:
        raise ValueError(f"Could not locate the observations in {name}.")
    values = [line.strip() for line in data[1].splitlines() if re.fullmatch(r"[+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?", line.strip())]
    mean = Decimal(re.search(r"Sample Mean\s+ybar:\s*([\d.eE+\-]+)", text)[1])
    std = Decimal(re.search(r"Sample Standard Deviation \(denom\. = n-1\)\s+s:\s*([\d.eE+\-]+)", text)[1])
    with localcontext() as context:
        context.prec = 50
        numbers = [Decimal(value) for value in values]
        independent_mean = sum(numbers) / len(numbers)
        independent_std = (sum((value - independent_mean) ** 2 for value in numbers) / (len(numbers) - 1)).sqrt()
        assert abs(independent_mean - mean) < Decimal('1e-24')
        assert abs(independent_std - std) < Decimal('1e-24')
    supplied = cache / f"{name}.csv"
    with supplied.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['value'])
        writer.writerows([[value] for value in values])
    output = root / name
    main(['start', '--kind', 'data_analysis', '--goal', 'Describe the supplied numeric observations',
          '--data-file', str(supplied), '--value-column', 'value', '--observation-unit', 'one constructed NIST observation',
          '--output-root', str(output), '--interaction', 'autonomous', '--yes'])
    paths = list(output.glob('*/sessions/*/attempts/data_analysis-*/analysis.json'))
    if len(paths) != 1:
        raise ValueError('Expected exactly one completed analysis; use a new output directory for another evaluation.')
    result = json.loads(paths[0].read_text(encoding='utf-8'))
    record = result['records'][0]
    # Binary input rounding bound, declared independently of the computed
    # result; not an experimental confidence interval or relaxed score.
    tolerance = 2 * max(math.ulp(float(value)) for value in values)
    assert record['count'] == len(values)
    assert abs(record['mean'] - float(mean)) <= tolerance
    assert abs(record['sample_std'] - float(std)) <= tolerance
    return {'dataset': name, 'source': url, 'count': len(values),
            'mean': record['mean'], 'sample_std': record['sample_std'],
            'certified_mean': str(mean), 'certified_sample_std': str(std),
            'absolute_binary_rounding_tolerance': tolerance, 'status': 'passed',
            'scope': 'CLI delivery and descriptive arithmetic only', 'result': str(paths[0].relative_to(root))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True, help='New acceptance directory; retains fetched official data and outputs.')
    root = parser.parse_args().output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows = [check(root, name) for name in ('NumAcc1', 'NumAcc3')]
    (root / 'acceptance.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(rows, indent=2))

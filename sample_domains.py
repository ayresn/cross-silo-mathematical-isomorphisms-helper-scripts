"""Sample random domain pairs from the existing dataset. Records its seed."""
import json, random, sys
from collections import Counter

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 20260728
N_PAIRS = int(sys.argv[2]) if len(sys.argv) > 2 else 20

data = json.load(open("indexes/entries.json"))["entries"]
domains = sorted({d for e in data for d in (e["domain_a"], e["domain_b"]) if d})
existing = {frozenset((e["domain_a"], e["domain_b"])) for e in data}

rng = random.Random(SEED)
used, pairs = Counter(), []
while len(pairs) < N_PAIRS:
    a, b = rng.sample(domains, 2)
    key = frozenset((a, b))
    if key in existing or any(frozenset(p) == key for p in pairs):
        continue
    if used[a] >= 2 or used[b] >= 2:
        continue
    used[a] += 1; used[b] += 1
    pairs.append((a, b))

print(f"# seed={SEED}  n={N_PAIRS}  pool={len(domains)} domains")
for i, (a, b) in enumerate(pairs, 1):
    print(f"{i:03d}\t{a}\t{b}")
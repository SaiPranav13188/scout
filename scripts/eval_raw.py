"""Re-run the plain-code filters over saved raw payloads and print distribution numbers.

  uv run python scripts/eval_raw.py .devdata/raw/raw_jobs-2026-11-06.jsonl.gz

This measures what the filters do on real data. It is not precision/recall: that needs
the hand-labelled set from Phase 3 (docs/EVALS.md).
"""

import gzip
import json
import sys
from collections import Counter
from datetime import UTC, datetime

from scout_core.pipeline.evaluate import evaluate_job
from scout_core.sources.ats import SOURCES
from scout_core.types import RawFetch


def main(path: str) -> None:
    seen: set[tuple[str, str]] = set()
    jobs = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            raw = RawFetch(source=d["source"], board_token=d["board_token"], url=d["url"], status_code=200,
                           fetched_at=datetime.now(UTC), payload=d["payload"])
            for j in SOURCES[d["source"]].parse(raw):
                if (j.source, j.source_job_id) not in seen:
                    seen.add((j.source, j.source_job_id))
                    jobs.append(j)

    keep = 0
    reasons, buckets, trust, fresher_conf, families = Counter(), Counter(), Counter(), Counter(), Counter()
    for j in jobs:
        e = evaluate_job(j)
        buckets[e.location.bucket.value] += 1
        trust[e.trust.level.value] += 1
        if e.keep:
            keep += 1
            fresher_conf[e.fresher.confidence.value] += 1
            families[e.relevance.family] += 1
        else:
            reasons[e.drop_reasons[0].split(":")[0] + (": title" if "title contains" in e.drop_reasons[0]
                                                      else ": stated years" if "requires" in e.drop_reasons[0] else "")] += 1
    boards = len({(j.source, j.board_token) for j in jobs})
    print(json.dumps({
        "unique_jobs": len(jobs), "boards": boards, "kept": keep,
        "drop_reasons": dict(reasons.most_common()), "location_buckets": dict(buckets.most_common()),
        "trust_levels": dict(trust.most_common()), "kept_fresher_confidence": dict(fresher_conf),
        "kept_role_families": dict(families.most_common()),
    }, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])

r"""Chance-level nDCG@20 / Recall@50 for an evaluation set.

Reports how a random ranking scores on the same queries, so a model's numbers can be read
against the corpus composition instead of against zero.

    uv run python tools/evaluation/random_baseline.py --dataset <dataset.json> \
        --result docs/reports/P0-06-eval-random-baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import numpy as np  # noqa: E402
from corpus import write_json  # noqa: E402
from runtime_common import REPOSITORY_ROOT  # noqa: E402

from photo_search.evaluation.dataset import load_dataset  # noqa: E402
from photo_search.evaluation.metrics import score_query, summarise  # noqa: E402

DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-eval-random-baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260911)
    args = parser.parse_args()

    try:
        dataset = load_dataset(args.dataset.resolve())
        image_ids = [image.id for image in dataset.images]
        rng = np.random.default_rng(args.seed)
        scores = []
        for query in dataset.queries:
            per_repeat = []
            for _ in range(args.repeats):
                ranking = [image_ids[index] for index in rng.permutation(len(image_ids))]
                per_repeat.append(score_query(query.id, query.split, ranking, query.relevant))
            mean_ndcg = sum(score.ndcg_at_20 for score in per_repeat) / len(per_repeat)
            mean_recall = sum(score.recall_at_50 for score in per_repeat) / len(per_repeat)
            base = per_repeat[0]
            scores.append(
                base.__class__(
                    query_id=base.query_id,
                    split=base.split,
                    ranked_image_ids=(),
                    ndcg_at_20=mean_ndcg,
                    recall_at_50=mean_recall,
                    best_rank=base.best_rank,
                    relevant_in_top_20=base.relevant_in_top_20,
                    relevant_total=base.relevant_total,
                )
            )
        result = {
            "status": "ok",
            "task": "P0-06",
            "stage": "B",
            "dataset": str(args.dataset.resolve()),
            "repeats_per_query": args.repeats,
            "seed": args.seed,
            "corpus_images": len(image_ids),
            "metrics": summarise(scores),
            "note": (
                "chance level for this corpus: a random top-50 holds about 50/N of the "
                "corpus, so recall@50 approaches 50/N for any query"
            ),
        }
        write_json(args.result.resolve(), result)
        print(json.dumps({"status": "ok", "result": str(args.result.resolve()), **result["metrics"]["overall"]}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "fail", "exception_type": type(error).__name__, "message": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

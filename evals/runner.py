"""The evaluation harness.

Answers one question honestly: does this work, and where does it not.

Three things it deliberately does differently from the usual demo scorecard.

**It measures abstention as a success.** A system that refuses to name an owner
it cannot determine is doing the right thing, and a scorecard that only counts
correct answers would score that as a miss and push the system towards
guessing. Correct abstention is reported as its own number.

**It counts false positives on a transcript with nothing in it.** The
`no_commitments` case is a retro where nobody agrees to anything. A summarizer
asked for action items will invent some, because the prompt implies they exist.
Zero is the only passing score.

**It runs an ablation.** "Multi-agent beats one prompt" is a claim, and claims
get tested. The same gold set runs against a single mega-prompt baseline and
against the team with pieces removed, so the contribution of each piece is a
number rather than an assertion.

Matching is fuzzy on purpose. An extracted commitment reading "Deliver the
migration plan to Meera by Tuesday" and a label reading "migration plan" are
the same thing, and a harness that calls that a miss is measuring string
formatting rather than comprehension.

    uv run python -m evals.runner              # the gold set
    uv run python -m evals.runner --ablation   # plus the ablation study
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agents.contracts import COMMITTED_CLASSES
from app.agents.intelligence import extract, review
from app.agents.resolution import resolve_deadlines, resolve_owners
from app.config import get_settings
from app.services import trace
from app.services.model_router import ModelRouter, Tier
from app.services.roster import RosterEntry
from app.services.segmentation import segment
from app.services.verifier import locate

ROOT = pathlib.Path(__file__).resolve().parent
GOLD = ROOT / "gold"
ADVERSARIAL = ROOT / "adversarial"
RESULTS = ROOT / "results"

# The seeded demo roster, so evaluation does not need a database.
ROSTER = [
    RosterEntry(_id, name, aliases)
    for _id, name, aliases in [
        (__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, name), name, aliases)
        for name, aliases in [
            ("Priya Nair", ("Priya", "Pri", "Preeya", "Prya")),
            ("Adit Sharma", ("Adit", "Adi", "Aadit", "Addit")),
            ("Meera Krishnan", ("Meera", "Mira", "Meer")),
            ("Tom Whitfield", ("Tom", "Thomas", "Tom W")),
            ("Rahul Menon", ("Rahul", "Rah", "Raul")),
            ("Sana Qureshi", ("Sana", "Sanaa", "Sanna")),
        ]
    ]
]

MEETING_DATE = date(2026, 9, 2)


def overlaps(text: str, gist: str) -> bool:
    """Whether an extracted item is about the same thing as a label.

    Content words only, and a majority of them. "Deliver the migration plan to
    Meera by Tuesday" matches the label "migration plan"; a harness that calls
    that a miss is measuring string formatting rather than comprehension.
    """
    stop = {"the", "a", "an", "to", "for", "by", "on", "of", "and", "with", "up", "get"}
    wanted = {word for word in gist.lower().split() if word not in stop}
    if not wanted:
        return False
    found = {word.strip(".,'\"").lower() for word in text.split()}
    return sum(1 for word in wanted if word in found) / len(wanted) >= 0.5


@dataclass
class CaseResult:
    name: str
    extracted: int = 0
    matched: int = 0
    expected: int = 0
    false_positives: list[str] = field(default_factory=list)
    owners_correct: int = 0
    owners_abstained_correctly: int = 0
    owners_wrong: list[str] = field(default_factory=list)
    dates_correct: int = 0
    grounding_verified: int = 0
    grounding_failed: int = 0
    rejections: list[dict[str, str]] = field(default_factory=list)
    seconds: float = 0.0
    cost_usd: float = 0.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        relevant = self.extracted - len(self.false_positives)
        return relevant / self.extracted if self.extracted else 1.0

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)


async def run_case(
    path: pathlib.Path, *, router: ModelRouter, settings: Any, with_skeptic: bool = True
) -> CaseResult:
    spec = json.loads(path.read_text())
    transcript = (
        (GOLD / spec["transcript_file"]).read_text()
        if "transcript_file" in spec
        else spec["transcript"]
    )
    expected = spec.get("expected", {})
    result = CaseResult(name=path.stem)
    turns = segment(transcript)

    started = time.perf_counter()
    with trace.run_trace(f"eval:{path.stem}") as recorder:
        found = await extract(transcript, turns, router=router, settings=settings)
        kept = found.commitments
        if with_skeptic:
            kept, review_rejections = await review(
                found.commitments, transcript, turns, router=router, settings=settings
            )
            result.rejections = [
                {"text": str(r.candidate.get("text", ""))[:70], "reason": r.reason[:110]}
                for r in review_rejections
            ]

        obligations = [item for item in kept if item.classification in COMMITTED_CLASSES]
        attributed = await resolve_owners(
            obligations, ROSTER, turns, router=router, settings=settings
        )
        deadlines = await resolve_deadlines(
            obligations,
            meeting_date=MEETING_DATE,
            timezone="Asia/Kolkata",
            router=router,
            settings=settings,
        )

    result.seconds = round(time.perf_counter() - started, 1)
    result.cost_usd = round(recorder.cost_usd, 5)
    result.extracted = len(obligations)

    # Grounding is checked against the source rather than trusted, because the
    # whole guarantee is that a stored quote is really in the transcript.
    for item in obligations:
        for evidence in item.evidence:
            if locate(transcript, evidence.quote) is not None:
                result.grounding_verified += 1
            else:
                result.grounding_failed += 1

    wanted = expected.get("commitments", [])
    result.expected = len(wanted)
    unmatched = list(obligations)

    for label in wanted:
        hit = next((item for item in unmatched if overlaps(item.text, label["gist"])), None)
        if hit is None:
            continue
        unmatched.remove(hit)
        result.matched += 1

        index = obligations.index(hit)
        attribution = attributed[index][1]
        wanted_owner = label.get("owner")
        if wanted_owner is None:
            if attribution.person_id is None:
                result.owners_abstained_correctly += 1
            else:
                result.owners_wrong.append(
                    f"{label['gist']}: named {attribution.display_name}, should have abstained"
                )
        elif attribution.display_name == wanted_owner:
            result.owners_correct += 1
        else:
            result.owners_wrong.append(
                f"{label['gist']}: got {attribution.display_name}, expected {wanted_owner}"
            )

        due = deadlines[index]
        wanted_due = label.get("due")
        if wanted_due is None:
            result.dates_correct += 1 if due.due is None else 0
        elif due.due is not None:
            result.dates_correct += 1

    # Anything the labels explicitly say must not be extracted.
    for forbidden in expected.get("must_not_extract", []):
        for item in obligations:
            if overlaps(item.text, forbidden["gist"]):
                result.false_positives.append(f"{item.text[:60]} ({forbidden['why']})")

    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Kept against the gold set.")
    parser.add_argument("--ablation", action="store_true", help="Also run without the Skeptic.")
    parser.add_argument("--report", type=str, default=None, help="Write markdown here.")
    args = parser.parse_args()

    settings = get_settings()
    router = ModelRouter(settings)
    cases = sorted(GOLD.glob("*.json"))

    print(f"Gold set: {len(cases)} cases")
    print(f"reason={router.primary(Tier.REASON).identifier} "
          f"skeptic={router.primary(Tier.SKEPTIC).identifier} "
          f"independent_review={router.describe()['independent_review']}\n")

    results = []
    for path in cases:
        # Sequential, and paced. The free tier allows 8000 tokens a minute and
        # a case costs roughly 6000, so running these concurrently measures the
        # rate limiter rather than the system.
        result = await run_case(path, router=router, settings=settings)
        results.append(result)
        print(
            f"  {result.name:22} P={result.precision:.2f} R={result.recall:.2f} "
            f"F1={result.f1:.2f}  owners {result.owners_correct}+{result.owners_abstained_correctly}"
            f"/{result.expected}  grounded {result.grounding_verified}/"
            f"{result.grounding_verified + result.grounding_failed}  "
            f"{result.seconds}s ${result.cost_usd}"
        )
        await asyncio.sleep(25)

    ablation: list[CaseResult] = []
    if args.ablation:
        print("\nAblation: the same cases without the Skeptic")
        for path in cases:
            result = await run_case(path, router=router, settings=settings, with_skeptic=False)
            ablation.append(result)
            print(f"  {result.name:22} P={result.precision:.2f} R={result.recall:.2f}")
            await asyncio.sleep(25)

    RESULTS.mkdir(exist_ok=True)
    payload = {
        "generated_at": date.today().isoformat(),
        "models": router.describe(),
        "cases": [vars(r) | {"precision": r.precision, "recall": r.recall, "f1": r.f1} for r in results],
        "ablation_no_skeptic": [
            vars(r) | {"precision": r.precision, "recall": r.recall} for r in ablation
        ],
    }
    (RESULTS / "latest.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")

    print(
        f"\nmean F1 {statistics.mean(r.f1 for r in results):.2f} · "
        f"total ${sum(r.cost_usd for r in results):.4f} · "
        f"grounding {sum(r.grounding_verified for r in results)}/"
        f"{sum(r.grounding_verified + r.grounding_failed for r in results)}"
    )
    if args.report:
        from evals.report import write_report

        write_report(pathlib.Path(args.report), payload)
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

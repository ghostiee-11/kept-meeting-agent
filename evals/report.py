"""Turn evaluation results into a document a reviewer can read.

The failure gallery is not an afterthought. A scorecard with no failures in it
is either a system nobody stressed or a report nobody trusts, and the second is
more likely. Every wrong owner and every false positive is printed with the
label it missed.
"""

from __future__ import annotations

import pathlib
from typing import Any


def write_report(path: pathlib.Path, payload: dict[str, Any]) -> None:
    cases = payload["cases"]
    models = payload["models"]
    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    add("# Evaluation")
    add()
    add(f"Generated {payload['generated_at']} by `make eval`. Regenerate it and the")
    add("numbers change; that is the point of committing them with a date.")
    add()

    tiers = models.get("tiers", {})
    add("| | |")
    add("| --- | --- |")
    add(f"| Extraction model | `{_primary(tiers, 'reason')}` |")
    add(f"| Review model | `{_primary(tiers, 'skeptic')}` |")
    add(f"| Independent review | {'yes' if models.get('independent_review') else '**no**'} |")
    add(f"| Judge | {_primary(tiers, 'judge') or '**not configured**'} |")
    add()

    if not models.get("independent_review"):
        add("> The Skeptic is running on the same provider as the Analyst, because only")
        add("> one provider is configured. Adversarial review is therefore less")
        add("> independent than designed, and these precision numbers should be read")
        add("> as a floor rather than the intended result.")
        add()

    add("## Method")
    add()
    add("Three things this harness does that a demo scorecard usually does not.")
    add()
    add("**Abstention counts as success.** A system that refuses to name an owner it")
    add("cannot determine is behaving correctly. Scoring only correct answers would")
    add("mark that as a miss and push the system towards guessing, which is the exact")
    add("failure the design exists to prevent. Correct abstention is its own column.")
    add()
    add("**False positives are measured on an empty transcript.** `no_commitments` is a")
    add("retro where nobody agrees to anything. A summarizer asked for action items")
    add("will invent some, because the prompt implies they exist. Zero is the only")
    add("passing score.")
    add()
    add("**Matching is fuzzy.** An extracted item reading \"Deliver the migration plan")
    add("to Meera by Tuesday\" and a label reading \"migration plan\" are the same thing.")
    add("A harness that calls that a miss measures string formatting, not comprehension.")
    add()

    add("## Results")
    add()
    add("| Case | Precision | Recall | F1 | Owners right | Correctly abstained | Grounded | Seconds |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for case in cases:
        total_evidence = case["grounding_verified"] + case["grounding_failed"]
        grounded = (
            f"{case['grounding_verified']}/{total_evidence}" if total_evidence else "n/a"
        )
        add(
            f"| `{case['name']}` | {case['precision']:.2f} | {case['recall']:.2f} | "
            f"{case['f1']:.2f} | {case['owners_correct']} | "
            f"{case['owners_abstained_correctly']} | {grounded} | {case['seconds']} |"
        )
    add()

    verified = sum(c["grounding_verified"] for c in cases)
    failed = sum(c["grounding_failed"] for c in cases)
    add(
        f"**Grounding fidelity: {verified}/{verified + failed} quotes verified against the "
        "source.** Every stored quote is text that genuinely appears in the transcript, "
        "because the verifier is a gate rather than a prompt instruction."
    )
    add()

    empty = next((c for c in cases if c["name"] == "no_commitments"), None)
    if empty is not None:
        add(
            f"**False positives on a transcript with nothing in it: "
            f"{empty['extracted']}.** " +
            ("Correct." if empty["extracted"] == 0 else "This is a real miss, see below.")
        )
        add()

    ablation = payload.get("ablation_no_skeptic") or []
    if ablation:
        add("## Ablation: does the Skeptic pay for itself?")
        add()
        add("The claim that adversarial review improves precision is tested rather than")
        add("asserted. Same cases, same models, Skeptic removed.")
        add()
        add("| Case | Precision with | Precision without | Δ |")
        add("| --- | --- | --- | --- |")
        for full in cases:
            without = next((a for a in ablation if a["name"] == full["name"]), None)
            if without is None:
                continue
            delta = full["precision"] - without["precision"]
            add(
                f"| `{full['name']}` | {full['precision']:.2f} | "
                f"{without['precision']:.2f} | {delta:+.2f} |"
            )
        add()

    rejections = [(c["name"], r) for c in cases for r in c.get("rejections", [])]
    if rejections:
        add("## What the Skeptic threw out")
        add()
        add("Each with the reason it gave, so the judgment can be checked against the")
        add("transcript rather than taken on trust.")
        add()
        for name, rejection in rejections:
            add(f"- **{rejection['text']}** — {rejection['reason']} *(`{name}`)*")
        add()

    failures = [
        (c["name"], problem)
        for c in cases
        for problem in [*c["owners_wrong"], *[f"false positive: {f}" for f in c["false_positives"]]]
    ]
    add("## Failure gallery")
    add()
    if not failures:
        add("Nothing failed on this run. That is a small gold set rather than a solved")
        add("problem: the honest reading is that the cases here are covered, not that")
        add("the system is correct in general.")
    else:
        add("Real mistakes from the run above, printed rather than rounded away.")
        add()
        for name, problem in failures:
            add(f"- `{name}`: {problem}")
    add()

    add("## What is not measured here")
    add()
    add("- **Cross-meeting slippage** has labelled fixtures but needs the full graph and a")
    add("  database, so it is exercised by the integration path rather than this harness.")
    add("- **An LLM judge** would let the fuzzy matching above be replaced with something")
    add("  better calibrated. It needs a provider that did not produce the output, and")
    add("  only one provider is configured.")
    add("- **The gold set is small.** Four cases chosen for the behaviours they expose,")
    add("  not a sample large enough for confidence intervals.")
    add()

    path.write_text("\n".join(lines) + "\n")


def _primary(tiers: dict[str, Any], name: str) -> str | None:
    tier = tiers.get(name)
    return tier.get("primary") if tier else None

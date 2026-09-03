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
    add('**Matching is fuzzy.** An extracted item reading "Deliver the migration plan')
    add('to Meera by Tuesday" and a label reading "migration plan" are the same thing.')
    add("A harness that calls that a miss measures string formatting, not comprehension.")
    add()

    add("## Results")
    add()
    add(
        "| Case | Precision | Recall | F1 | Owners right | Correctly abstained | Grounded | Seconds |"
    )
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for case in cases:
        total_evidence = case["grounding_verified"] + case["grounding_failed"]
        grounded = f"{case['grounding_verified']}/{total_evidence}" if total_evidence else "n/a"
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
            f"{empty['extracted']}.** "
            + ("Correct." if empty["extracted"] == 0 else "This is a real miss, see below.")
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

    single = payload.get("baseline_single_prompt") or []
    if single:
        add("## Baseline: is a team better than one prompt?")
        add()
        add("The architecture's central claim, measured against the thing it argues")
        add("with: one prompt, one call, given the same job on the same model and")
        add("scored by the same matcher. What it does not get is what the argument is")
        add("about, since a single prompt has nowhere to put them: no adversarial")
        add("review, no roster, no calendar, no grounding gate.")
        add()
        add("| Case | Team F1 | One prompt F1 | Team owners right | One prompt owners right |")
        add("| --- | --- | --- | --- | --- |")
        for full in cases:
            flat = next((b for b in single if b["name"] == full["name"]), None)
            if flat is None:
                continue
            add(
                f"| `{full['name']}` | {full['f1']:.2f} | {flat['f1']:.2f} | "
                f"{full['owners_correct']}+{full['owners_abstained_correctly']} | "
                f"{flat['owners_correct']}+{flat['owners_abstained_correctly']} |"
            )
        add()
        team_cost = sum(c["cost_usd"] for c in cases)
        flat_cost = sum(b["cost_usd"] for b in single)
        if flat_cost:
            add(
                f"The team costs ${team_cost:.4f} against ${flat_cost:.4f} for one prompt, "
                f"roughly {team_cost / flat_cost:.1f} times as much. That multiple is the "
                "honest price of the columns to its left, and it is reported here rather "
                "than left for the reader to discover."
            )
            add()

    hostile = payload.get("adversarial") or []
    if hostile:
        add("## Adversarial suite")
        add()
        add("Hostile and degraded transcripts, scored on behaviour rather than on")
        add("extraction quality. The question is not whether the system found the")
        add("commitments but whether it stayed inside its own rules: invented nothing,")
        add("stored nothing ungrounded, and recorded an injection attempt instead of")
        add("acting on it.")
        add()
        add("| Case | What it tests | Extracted | Flagged | Result |")
        add("| --- | --- | --- | --- | --- |")
        for item in hostile:
            flags = ", ".join(item["flags"]) or "none"
            verdict = "pass" if item["passed"] else f"**FAIL** {item['note']}"
            add(
                f"| `{item['name']}` | {item['expectation']} | {item['extracted']} | "
                f"{flags} | {verdict} |"
            )
        add()
        add("Detection is a heuristic and is deliberately not the defence. The defence is")
        add("structural: the agents that read the transcript have no tools, and the agent")
        add("that writes to the outside world never reads it. The flag exists so a person")
        add("can see that somebody tried.")
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
    add("Real mistakes from the run above, printed rather than rounded away.")
    add()

    problems: list[str] = []
    for case in cases:
        missed = case["expected"] - case["matched"]
        if missed > 0:
            problems.append(
                f"`{case['name']}`: {missed} of {case['expected']} labelled commitments "
                "were not found. A missed promise is the more serious direction of "
                "error here, because nobody sees the row that is not there."
            )
        problems.extend(f"`{case['name']}`: {wrong}" for wrong in case["owners_wrong"])
        problems.extend(f"`{case['name']}`: invented {fp}" for fp in case["false_positives"])

    for item in problems or [
        "Nothing failed on this run, which is a small gold set rather than a solved problem."
    ]:
        add(f"- {item}")
    add()

    add("## What is not measured here")
    add()
    add("- **Cross-meeting slippage** has labelled fixtures but needs the full graph")
    add("  and a database, so it is exercised by `tests/test_historian_slippage.py`")
    add("  against a live model rather than by this harness.")
    judge = _primary(tiers, "judge")
    add(
        f"- **An LLM judge** ({judge}) is configured but not yet used to grade: the"
        if judge
        else "- **An LLM judge** is not configured, so fuzzy matching stands unchecked:"
    )
    add("  fuzzy matcher above is deterministic and cheap, and replacing it needs the")
    add("  judge calibrated against human labels first. Reporting an uncalibrated")
    add("  judge's agreement as a number would be worse than reporting none.")
    add("- **The gold set is small.** A handful of cases chosen for the behaviours")
    add("  they expose, not a sample large enough for confidence intervals. Treat")
    add("  a single point difference between two columns as noise.")
    add()

    path.write_text("\n".join(lines) + "\n")


def _primary(tiers: dict[str, Any], name: str) -> str | None:
    tier = tiers.get(name)
    return tier.get("primary") if tier else None

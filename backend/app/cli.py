"""Command line entry points.

argparse rather than a CLI framework: this needs two subcommands, and stdlib
covers two subcommands.

    python -m app.cli seed
    python -m app.cli seed --reset
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from sqlalchemy import delete, select

from app.agents.contracts import COMMITTED_CLASSES, RejectionRecord
from app.agents.intelligence import extract, review
from app.agents.resolution import enrich_decisions, resolve_deadlines, resolve_owners
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging, get_logger
from app.models.domain import Person, Workspace
from app.services import trace
from app.services.model_router import ModelRouter, Tier
from app.services.roster import RosterEntry
from app.services.search import SearchService
from app.services.segmentation import participants, segment
from app.services.temporal import today_in

log = get_logger(__name__)

DEMO_WORKSPACE_SLUG = "kept-demo"

# Aliases are the point of this roster. Real transcripts contain nicknames and
# transcription errors, and the Attributor resolving "Preeya" to Priya instead
# of inventing a seventh person is exactly the behaviour under test.
DEMO_ROSTER: list[dict[str, object]] = [
    {
        "name": "Priya Nair",
        "aliases": ["Priya", "Pri", "Preeya", "Prya"],
        "email": "priya@kept.demo",
        "role": "Engineering Lead",
        "team": "Platform",
    },
    {
        "name": "Adit Sharma",
        "aliases": ["Adit", "Adi", "Aadit", "Addit"],
        "email": "adit@kept.demo",
        "role": "Backend Engineer",
        "team": "Platform",
    },
    {
        "name": "Meera Krishnan",
        "aliases": ["Meera", "Mira", "Meer"],
        "email": "meera@kept.demo",
        "role": "Product Manager",
        "team": "Product",
    },
    {
        "name": "Tom Whitfield",
        "aliases": ["Tom", "Thomas", "Tom W"],
        "email": "tom@kept.demo",
        "role": "Design Lead",
        "team": "Product",
    },
    {
        "name": "Rahul Menon",
        "aliases": ["Rahul", "Rah", "Raul"],
        "email": "rahul@kept.demo",
        "role": "ML Engineer",
        "team": "Platform",
    },
    {
        "name": "Sana Qureshi",
        "aliases": ["Sana", "Sanaa", "Sanna"],
        "email": "sana@kept.demo",
        "role": "Customer Success Lead",
        "team": "Revenue",
    },
]


async def seed(*, reset: bool) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    async with factory() as session:
        workspace = await session.scalar(
            select(Workspace).where(Workspace.slug == DEMO_WORKSPACE_SLUG)
        )

        if workspace is None:
            workspace = Workspace(
                name="Kept Demo",
                slug=DEMO_WORKSPACE_SLUG,
                settings={"default_timezone": "Asia/Kolkata"},
            )
            session.add(workspace)
            await session.flush()
            log.info("seed.workspace_created", slug=DEMO_WORKSPACE_SLUG)

        if reset:
            await session.execute(delete(Person).where(Person.workspace_id == workspace.id))
            log.info("seed.roster_cleared")

        existing = set(
            (
                await session.scalars(
                    select(Person.email).where(Person.workspace_id == workspace.id)
                )
            ).all()
        )

        added = 0
        for entry in DEMO_ROSTER:
            if entry["email"] in existing:
                continue
            session.add(Person(workspace_id=workspace.id, **entry))
            added += 1

        await session.commit()
        log.info("seed.complete", people_added=added, people_total=len(DEMO_ROSTER))

    await engine.dispose()


async def load_roster() -> list[RosterEntry]:
    """The demo workspace roster, for owner matching."""
    settings = get_settings()
    if settings.database_url is None:
        return []

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    async with factory() as session:
        people = (await session.scalars(select(Person))).all()
    await engine.dispose()
    return [
        RosterEntry(person.id, person.name, tuple(person.aliases), person.role) for person in people
    ]


async def analyse(path: pathlib.Path, *, review_enabled: bool) -> None:
    """Run the Intelligence and Resolution teams over a transcript.

    Prints rejections and unresolved questions alongside the kept items,
    because the point of a separate reviewer and an abstaining resolver is that
    you can see what they set aside and check whether they were right.
    """
    transcript = path.read_text()
    settings = get_settings()
    router = ModelRouter(settings)
    turns = segment(transcript)
    roster = await load_roster()
    meeting_date = today_in("Asia/Kolkata")

    print(
        f"\n{path.name}: {len(turns)} turns, speakers: {', '.join(participants(turns)) or 'none'}"
    )
    print(
        f"models: reason={router.primary(Tier.REASON).identifier} "
        f"skeptic={router.primary(Tier.SKEPTIC).identifier}\n"
    )

    with trace.run_trace(f"cli:{path.stem}") as recorder:
        found = await extract(transcript, turns, router=router, settings=settings)

        kept = found.commitments
        review_rejections: list[RejectionRecord] = []
        if review_enabled:
            kept, review_rejections = await review(
                found.commitments, transcript, turns, router=router, settings=settings
            )

        obligations = [i for i in kept if i.classification in COMMITTED_CLASSES]
        search = SearchService(settings)
        attributed, deadlines, enrichments = await asyncio.gather(
            resolve_owners(obligations, roster, turns, router=router, settings=settings),
            resolve_deadlines(
                obligations,
                meeting_date=meeting_date,
                timezone="Asia/Kolkata",
                router=router,
                settings=settings,
                search=search,
            ),
            enrich_decisions(found.decisions, router=router, settings=settings, search=search),
        )

    print(f"DECISIONS ({len(found.decisions)})")
    for index, decision in enumerate(found.decisions):
        print(f"  {decision.statement}")
        print(f"    quote: {decision.evidence[0].quote[:78]!r}")
        if enriched := enrichments.get(index):
            print(f"    context: {enriched.summary[:150]}")
            for url in enriched.citations[:2]:
                print(f"      {url}")

    print(f"\nOBLIGATIONS ({len(obligations)} of {len(kept)} classified items)")
    questions: list[str] = []
    for (obligation, attribution), deadline in zip(attributed, deadlines, strict=True):
        owner = attribution.display_name or "UNOWNED"
        due = deadline.due.isoformat() if deadline.due else "NO DATE"
        flags = "".join(
            [
                " RETRACTED" if obligation.is_retracted else "",
                f" IF[{obligation.conditional_on}]" if obligation.conditional_on else "",
            ]
        )
        print(f"  {obligation.text[:76]}{flags}")
        print(
            f"      owner: {owner:16} ({attribution.confidence:.2f}) "
            f"due: {due:12} ({deadline.confidence:.2f} via {deadline.method})"
        )
        if attribution.person_id is None:
            questions.append(f"{obligation.text[:56]}: {attribution.reason}")
        if deadline.due is None and obligation.due_hint:
            questions.append(f'{obligation.text[:56]}: could not resolve "{obligation.due_hint}"')

    if questions:
        print(f"\nNEEDS A HUMAN ({len(questions)})")
        for question in questions:
            print(f"  {question}")

    print(f"\nBLOCKERS ({len(found.blockers)})")
    for blocker in found.blockers:
        print(f"  {blocker.description[:88]}")

    all_rejections = [*found.rejections, *review_rejections]
    print(f"\nREJECTED ({len(all_rejections)})")
    for rejection in all_rejections:
        text = rejection.candidate.get("text") or rejection.candidate.get("statement") or "?"
        print(f"  [{rejection.rejected_by}] {str(text)[:60]}")
        print(f"      {rejection.reason[:100]}")

    print(f"\ncost ${recorder.cost_usd:.5f} | tokens {recorder.tokens[0]}/{recorder.tokens[1]}")
    for agent, totals in recorder.by_agent().items():
        print(
            f"  {agent:24} {totals['calls']} calls  {totals['latency_ms']:6}ms  "
            f"${totals['cost_usd']:.5f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kept", description="Kept maintenance commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    seed_parser = subcommands.add_parser("seed", help="Create the demo workspace and roster.")
    seed_parser.add_argument(
        "--reset", action="store_true", help="Delete the existing roster first."
    )

    analyse_parser = subcommands.add_parser(
        "analyse", help="Run the Intelligence team over a transcript and print what it found."
    )
    analyse_parser.add_argument("transcript", help="Path to a transcript file.")
    analyse_parser.add_argument(
        "--no-review", action="store_true", help="Skip the Skeptic, to see raw Analyst output."
    )

    args = parser.parse_args(argv)
    configure_logging(level=get_settings().log_level, json_output=False)

    if args.command == "seed":
        asyncio.run(seed(reset=args.reset))
    elif args.command == "analyse":
        asyncio.run(analyse(pathlib.Path(args.transcript), review_enabled=not args.no_review))
    return 0


if __name__ == "__main__":
    sys.exit(main())

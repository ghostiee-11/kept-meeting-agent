"""Command line entry points.

argparse rather than a CLI framework: this needs two subcommands, and stdlib
covers two subcommands.

    python -m app.cli seed
    python -m app.cli seed --reset
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging, get_logger
from app.models.domain import Person, Workspace

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kept", description="Kept maintenance commands.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    seed_parser = subcommands.add_parser("seed", help="Create the demo workspace and roster.")
    seed_parser.add_argument(
        "--reset", action="store_true", help="Delete the existing roster first."
    )

    args = parser.parse_args(argv)
    configure_logging(level=get_settings().log_level, json_output=False)

    if args.command == "seed":
        asyncio.run(seed(reset=args.reset))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Sequence

from app.database.session import SessionLocal
from app.ingestion.nse.artifacts import ArtifactValidationError
from app.ingestion.nse.client import OfficialSourceError
from app.ingestion.nse.definitions import ArtifactType, SOURCE_DEFINITIONS
from app.ingestion.nse.service import NseIngestionService
from app.services.data_sources import DataSourceCoverageService


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _write(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2, default=str))


def _artifact_type(value: str) -> ArtifactType:
    try:
        return ArtifactType(value.upper())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("unsupported artifact type") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nse",
        description="Official/public NSE EOD ingestion for local personal research",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("audit", help="Show supported official source contracts")
    subparsers.add_parser("status", help="Show source and coverage status")
    subparsers.add_parser("coverage", help="Show data coverage")

    import_parser = subparsers.add_parser("import", help="Import an official file from data/nse/inbox")
    import_parser.add_argument("file")
    import_parser.add_argument("--type", required=True, type=_artifact_type)
    import_parser.add_argument("--date", required=True, type=_date)
    import_parser.add_argument("--dry-run", action="store_true")

    dry_parser = subparsers.add_parser("dry-run", help="Validate and plan a local artifact import")
    dry_parser.add_argument("file")
    dry_parser.add_argument("--type", required=True, type=_artifact_type)
    dry_parser.add_argument("--date", required=True, type=_date)

    security_parser = subparsers.add_parser("security-master", help="Sync the dated MII security file")
    security_parser.add_argument("--date", required=True, type=_date)
    security_parser.add_argument("--file")
    security_parser.add_argument("--dry-run", action="store_true")

    eod_parser = subparsers.add_parser("fetch-eod", help="Fetch/import one official CM UDiFF EOD artifact")
    eod_parser.add_argument("date", type=_date)
    eod_parser.add_argument("--dry-run", action="store_true")

    backfill_parser = subparsers.add_parser("backfill", help="Fetch a bounded resumable EOD date range")
    backfill_parser.add_argument("--from", dest="start", required=True, type=_date)
    backfill_parser.add_argument("--to", dest="end", required=True, type=_date)
    backfill_parser.add_argument("--dry-run", action="store_true")

    constituents_parser = subparsers.add_parser("constituents", help="Sync a current official index snapshot")
    constituents_parser.add_argument("--index", choices=("NIFTY_200", "NIFTY_500"), required=True)
    constituents_parser.add_argument("--as-of", required=True, type=_date)
    constituents_parser.add_argument("--file")
    constituents_parser.add_argument("--dry-run", action="store_true")

    actions_parser = subparsers.add_parser("corporate-actions", help="Import an official corporate-actions CSV")
    actions_parser.add_argument("file")
    actions_parser.add_argument("--as-of", required=True, type=_date)
    actions_parser.add_argument("--dry-run", action="store_true")

    holidays_parser = subparsers.add_parser("holidays", help="Import an official trading-holiday CSV")
    holidays_parser.add_argument("file")
    holidays_parser.add_argument("--as-of", required=True, type=_date)
    holidays_parser.add_argument("--dry-run", action="store_true")
    return parser


def _run(args: argparse.Namespace) -> object:
    if args.command == "audit":
        return {
            "zero_cost": True,
            "official_only": True,
            "sources": [
                {
                    "artifact_type": artifact_type.value,
                    "provider": definition.provider.value,
                    "owner": definition.owner,
                    "landing_page": definition.landing_page,
                    "artifact_kind": definition.artifact_kind,
                    "parser": f"{definition.parser_code} v{definition.parser_version}",
                    "automation_suitability": definition.automation_suitability,
                    "point_in_time_limit": definition.point_in_time_limit,
                }
                for artifact_type, definition in SOURCE_DEFINITIONS.items()
            ],
        }
    with SessionLocal() as session:
        service = NseIngestionService(session)
        coverage = DataSourceCoverageService(session)
        if args.command == "status":
            return {
                "sources": coverage.source_status().model_dump(mode="json"),
                "coverage": coverage.coverage().model_dump(mode="json"),
            }
        if args.command == "coverage":
            return coverage.coverage().model_dump(mode="json")
        if args.command in {"import", "dry-run"}:
            return service.import_local(
                args.type,
                args.date,
                args.file,
                dry_run=args.command == "dry-run" or args.dry_run,
            ).as_dict()
        if args.command == "security-master":
            if args.file:
                result = service.import_local(
                    ArtifactType.SECURITY_MASTER,
                    args.date,
                    args.file,
                    dry_run=args.dry_run,
                )
            else:
                result = service.fetch_and_import(
                    ArtifactType.SECURITY_MASTER, args.date, dry_run=args.dry_run
                )
            return result.as_dict()
        if args.command == "fetch-eod":
            return service.fetch_and_import(
                ArtifactType.EOD_BHAVCOPY, args.date, dry_run=args.dry_run
            ).as_dict()
        if args.command == "backfill":
            results = service.backfill(args.start, args.end, dry_run=args.dry_run)
            return {
                "requested_start": args.start,
                "requested_end": args.end,
                "artifact_count": len(results),
                "results": [item.as_dict() for item in results],
            }
        if args.command == "constituents":
            artifact_type = (
                ArtifactType.NIFTY_200_CONSTITUENTS
                if args.index == "NIFTY_200"
                else ArtifactType.NIFTY_500_CONSTITUENTS
            )
            if args.file:
                result = service.import_local(
                    artifact_type, args.as_of, args.file, dry_run=args.dry_run
                )
            else:
                result = service.fetch_and_import(
                    artifact_type, args.as_of, dry_run=args.dry_run
                )
            return result.as_dict()
        if args.command == "corporate-actions":
            return service.import_local(
                ArtifactType.CORPORATE_ACTIONS,
                args.as_of,
                args.file,
                dry_run=args.dry_run,
            ).as_dict()
        if args.command == "holidays":
            return service.import_local(
                ArtifactType.TRADING_HOLIDAYS,
                args.as_of,
                args.file,
                dry_run=args.dry_run,
            ).as_dict()
    raise ValueError("Unknown command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _write(_run(args))
        return 0
    except (ArtifactValidationError, OfficialSourceError, ValueError) as exc:
        _write({"status": "FAILED", "error": str(exc)[:500]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ResearchExperiment, ResearchExperimentRun


class ResearchExperimentRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_experiment(self, experiment: ResearchExperiment) -> ResearchExperiment:
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def add_run(self, run: ResearchExperimentRun) -> ResearchExperimentRun:
        self.session.add(run)
        self.session.flush()
        return run

    def get(self, experiment_id: UUID) -> ResearchExperiment | None:
        return self.session.get(ResearchExperiment, experiment_id)

    def list_page(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResearchExperiment], int, dict[UUID, ResearchExperimentRun]]:
        total = int(self.session.scalar(select(func.count()).select_from(ResearchExperiment)) or 0)
        experiments = list(
            self.session.scalars(
                select(ResearchExperiment)
                .order_by(ResearchExperiment.created_at.desc(), ResearchExperiment.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        if not experiments:
            return experiments, total, {}

        experiment_ids = [item.id for item in experiments]
        ranked = (
            select(
                ResearchExperimentRun.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=ResearchExperimentRun.experiment_id,
                    order_by=(
                        ResearchExperimentRun.executed_at.desc(),
                        ResearchExperimentRun.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(ResearchExperimentRun.experiment_id.in_(experiment_ids))
            .subquery()
        )
        latest = list(
            self.session.scalars(
                select(ResearchExperimentRun)
                .join(ranked, ResearchExperimentRun.id == ranked.c.run_id)
                .where(ranked.c.row_number == 1)
            )
        )
        return experiments, total, {item.experiment_id: item for item in latest}

    def latest_run(self, experiment_id: UUID) -> ResearchExperimentRun | None:
        return self.session.scalar(
            select(ResearchExperimentRun)
            .where(ResearchExperimentRun.experiment_id == experiment_id)
            .order_by(
                ResearchExperimentRun.executed_at.desc(),
                ResearchExperimentRun.id.desc(),
            )
            .limit(1)
        )

    def runs_page(
        self,
        experiment_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResearchExperimentRun], int]:
        total = int(
            self.session.scalar(
                select(func.count())
                .select_from(ResearchExperimentRun)
                .where(ResearchExperimentRun.experiment_id == experiment_id)
            )
            or 0
        )
        runs = list(
            self.session.scalars(
                select(ResearchExperimentRun)
                .where(ResearchExperimentRun.experiment_id == experiment_id)
                .order_by(
                    ResearchExperimentRun.executed_at.asc(),
                    ResearchExperimentRun.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return runs, total


__all__ = ["ResearchExperimentRepository"]

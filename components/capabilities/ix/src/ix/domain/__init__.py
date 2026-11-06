"""The parts of what ix returns, under one public name.

``ix`` exports the functions, the documents they return, the records an author writes and
the errors. Every type reachable from those (a report's findings and decisions, a plan's
trials and sensitivity, an experiment's subject and probe configs, the rows of a run's
tables) is exported here, and ``ix.domain`` is the only public path to them: the modules
inside it are not, so they can be divided or renamed without breaking a caller. What an
extension implements and is handed (the ports, the needs, what an adapter raises) is
:mod:`ix.extension`'s.
"""

from ix.domain.analysis import (
    Analysis,
    CalibrationCheck,
    Decision,
    Estimate,
    FailureCount,
    Finding,
    Interval,
    Outcome,
    Spread,
)
from ix.domain.design import RoleAssignment, SelectedSensor, SourcedValue
from ix.domain.experiment import (
    EnvironmentConfig,
    Guardrail,
    Level,
    Margin,
    MetricExpectation,
    MetricRef,
    ProbeConfig,
    ProbeFile,
    SensorConfig,
    Shape,
    SubjectConfig,
    Threshold,
)
from ix.domain.genre import AnalysisRef, GenreDesign, Repeats, Role
from ix.domain.kind import Confusion, Metric, Option
from ix.domain.plan import (
    Duration,
    ModelCallCount,
    OrderPlan,
    PlannedProbes,
    RegistrationStatus,
    Required,
    Sensitivity,
    Trials,
    Work,
)
from ix.domain.provenance import Content, Provenance, RuntimeOrigin
from ix.domain.registration import Amendment, Deviation, Registration
from ix.domain.report import RunSummary, TrialCounts
from ix.domain.run import Failure, Interaction, Reading, Trial
from ix.domain.summaries import ConfusionTable, LabelScore, ProbeScreen

__all__ = [
    "Amendment",
    "Analysis",
    "AnalysisRef",
    "CalibrationCheck",
    "Confusion",
    "ConfusionTable",
    "Content",
    "Decision",
    "Deviation",
    "Duration",
    "EnvironmentConfig",
    "Estimate",
    "Failure",
    "FailureCount",
    "Finding",
    "GenreDesign",
    "Guardrail",
    "Interaction",
    "Interval",
    "LabelScore",
    "Level",
    "Margin",
    "Metric",
    "MetricExpectation",
    "ModelCallCount",
    "MetricRef",
    "Option",
    "OrderPlan",
    "Outcome",
    "PlannedProbes",
    "ProbeConfig",
    "ProbeFile",
    "ProbeScreen",
    "Provenance",
    "Reading",
    "Registration",
    "RegistrationStatus",
    "Repeats",
    "Required",
    "Role",
    "RoleAssignment",
    "RunSummary",
    "RuntimeOrigin",
    "SelectedSensor",
    "Sensitivity",
    "SensorConfig",
    "Shape",
    "SourcedValue",
    "Spread",
    "SubjectConfig",
    "Threshold",
    "Trial",
    "TrialCounts",
    "Trials",
    "Work",
]

"""TLE-aware observation planning for one or more ground-station resources.

The package deliberately keeps orbit prediction, probability estimation and
schedule optimisation behind small interfaces.  That makes live data sources
(SatNOGS, CelesTrak and weather providers) replaceable without changing the
auditable planning records.
"""

from .models import (
    AntennaSpec,
    Blocker,
    GeoBox,
    GroundStation,
    ObservationPlan,
    Opportunity,
    OrbitSample,
    PassWindow,
    ProbabilityEstimate,
    ReceiverResource,
    SatelliteTarget,
    TransmissionRule,
)
from .engine import DynamicObservationPlanner, PlanningRun

__all__ = [
    "AntennaSpec",
    "Blocker",
    "GeoBox",
    "GroundStation",
    "ObservationPlan",
    "Opportunity",
    "OrbitSample",
    "PassWindow",
    "ProbabilityEstimate",
    "ReceiverResource",
    "SatelliteTarget",
    "TransmissionRule",
    "DynamicObservationPlanner",
    "PlanningRun",
]

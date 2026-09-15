import json
import hashlib
import importlib.metadata
import pickle
import platform
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from telemetry_yield.planning.adaptive_planner import AdaptiveOpportunityBuilder, AdaptiveReceptionPredictor, request_row, _sha
from telemetry_yield.planning.live_history import HistoryEvent, LiveHistoryStore
from telemetry_yield.planning.models import GroundStation, ReceiverResource, SatelliteTarget, TransmissionRule, Blocker, OrbitSample, PassWindow
from telemetry_yield.planning.opportunities import OpportunityBuilder
from telemetry_yield.planning.engine import DynamicObservationPlanner
from telemetry_yield.planning.scheduler import MilpScheduler
from test_observation_planning import opportunity, tle, FakePredictor, StaticTleProvider

AS_OF = datetime(2024,5,3,10,tzinfo=UTC)


def predictor(tmp_path):
    path = tmp_path/"history.sqlite"
    store = LiveHistoryStore(path)
    store.append([HistoryEvent("old",task,AS_OF-timedelta(days=2),AS_OF-timedelta(days=1),
        25544,12,"tx",30,145_800_000,1,"captured") for task in ("signal_present","decode_success_given_signal")])
    store.close()
    bundle = {"created_at":(AS_OF-timedelta(hours=1)).isoformat(),"research_only":True,
              "heads":{task:{"method":"last10_2","model":None} for task in ("signal_present","decode_success_given_signal")}}
    return AdaptiveReceptionPredictor(bundle,path,artifact_identity="abc123")


def test_sparse_inference_actual_receipt_cutoff_and_request_validation(tmp_path):
    engine = predictor(tmp_path)
    payload = {"norad_id":25544,"station_id":12,"start":(AS_OF+timedelta(hours=3)).isoformat()}
    first = engine.predict_many([payload],as_of=AS_OF)[0]
    assert 0<first["p_demod_artifact"]<1
    assert first["tasks"]["signal_present"]["pair_history_count"] == 1
    store = LiveHistoryStore(engine.database)
    # Reception happened in the past, but receipt after issue time is unavailable.
    store.append([HistoryEvent("late","signal_present",AS_OF-timedelta(days=2),AS_OF+timedelta(hours=1),
                              25544,12,None,None,None,0)])
    store.close()
    assert engine.predict_many([payload],as_of=AS_OF)[0] == first
    assert engine.predict_many([payload],as_of=AS_OF+timedelta(hours=1))[0]["tasks"]["signal_present"]["pair_history_count"] == 2
    for invalid in ({**payload,"outcome":1},{**payload,"duration_seconds":float("nan")},{**payload,"station_id":True}):
        with pytest.raises(ValueError):
            request_row(invalid)
    with pytest.raises(ValueError,match="creation"):
        engine.predict_many([payload],as_of=AS_OF-timedelta(days=1))


def test_missing_station_and_stale_history_are_explicit(tmp_path):
    engine = predictor(tmp_path)
    result = engine.predict_many([{"norad_id":99999,"start":(AS_OF+timedelta(days=40)).isoformat()}],as_of=AS_OF)[0]
    assert result["tasks"]["signal_present"]["pair_history_count"] == 0
    assert "station_id" in result["missing_optional_inputs"]
    assert any("extrapolation" in item for item in result["limitations"])


def test_rescoring_preserves_geometry_resources_and_transmission_factor(tmp_path):
    engine = predictor(tmp_path)
    op = opportunity("one",AS_OF+timedelta(hours=3),AS_OF+timedelta(hours=3,minutes=10),.9)
    target = SatelliteTarget(25544,"ISS",frequency_hz=op.frequency_hz,transmitter_uuid=op.transmitter_uuid,
                             transmission_rule=TransmissionRule(probability_when_allowed=.4))
    delegate = SimpleNamespace(build=lambda *args,**kwargs:(op,))
    builder = AdaptiveOpportunityBuilder(delegate,engine,as_of=AS_OF)
    newer, = builder.build([target],[],{},AS_OF,AS_OF+timedelta(days=1))
    assert newer.opportunity_id == op.opportunity_id
    assert newer.tle_fingerprint == op.tle_fingerprint
    assert newer.conflict_assets == op.conflict_assets
    assert newer.expected_unique_samples == pytest.approx(newer.nominal_unique_samples*newer.probability.p_success)
    assert newer.probability.p_signal_present == pytest.approx(.4*newer.metadata["adaptive_prediction"]["p_signal_present"])
    assert "NOT calibrated" in newer.metadata["uncertainty_semantics"]
    plan = MilpScheduler().schedule([newer],horizon_start=AS_OF,horizon_end=AS_OF+timedelta(days=1),
                                    tle_fingerprints={25544:op.tle_fingerprint},created_at=AS_OF)
    assert len(plan.assignments) == 1


def test_real_opportunity_builder_keeps_blockers_and_dynamic_tle_path(tmp_path):
    engine = predictor(tmp_path)
    start,end = AS_OF+timedelta(hours=3),AS_OF+timedelta(hours=3,minutes=10)
    samples = tuple(OrbitSample(start+timedelta(minutes=i),40,90,500,0,0,0) for i in range(11))
    window = PassWindow(25544,"gs",start,end,40,start+timedelta(minutes=5),500,1000,samples,tle().fingerprint)
    station = GroundStation("gs",52,21,100,(ReceiverResource("rx"),),satnogs_station_id=12)
    target = SatelliteTarget(25544,"ISS",frequency_hz=145_800_000,transmitter_uuid="tx")
    base = OpportunityBuilder(FakePredictor(window))
    builder = AdaptiveOpportunityBuilder(base,engine,as_of=AS_OF)
    planner = DynamicObservationPlanner(StaticTleProvider(),builder)
    run = planner.plan([target],[station],AS_OF,end+timedelta(hours=1),now=AS_OF)
    assert run.opportunities
    assert all(op.probability.model_version.startswith("adaptive-method-v3") for op in run.opportunities)
    blocker = Blocker("maintenance","gs",start,end)
    blocked = builder.build([target],[station],{25544:tle()},AS_OF,end+timedelta(hours=1),blockers=[blocker])
    assert blocked == ()


def test_month_ahead_requires_explicit_tentative_opt_in(tmp_path):
    engine = predictor(tmp_path)
    op = opportunity("later",AS_OF+timedelta(days=20),AS_OF+timedelta(days=20,minutes=10),.9)
    target = SatelliteTarget(25544,"ISS",frequency_hz=op.frequency_hz,transmitter_uuid=op.transmitter_uuid)
    delegate = SimpleNamespace(build=lambda *args,**kwargs:(op,))
    builder = AdaptiveOpportunityBuilder(delegate,engine,as_of=AS_OF)
    with pytest.raises(ValueError,match="tentative"):
        builder.build([target],[],{},AS_OF,AS_OF+timedelta(days=30))
    builder = AdaptiveOpportunityBuilder(delegate,engine,as_of=AS_OF,allow_extrapolation=True)
    result, = builder.build([target],[],{},AS_OF,AS_OF+timedelta(days=30))
    assert any("extrapolation" in item for item in result.metadata["adaptive_prediction"]["limitations"])


def test_loader_checks_content_before_loading_local_bundle(tmp_path):
    engine = predictor(tmp_path)
    stage = tmp_path/"labels-25000"
    stage.mkdir()
    (stage/"selected-model.pkl").write_bytes(pickle.dumps(engine.bundle))
    (stage/"results.json").write_text("{}")
    (stage/"integrity-audit.json").write_text(json.dumps({"available_checks_pass":True,"final_test_scored":False}))
    protocol = {"sources":[],"runtime":{"python":platform.python_version(),
        **{p:importlib.metadata.version(p) for p in ("numpy","scipy","scikit-learn")}}}
    (tmp_path/"protocol.json").write_text(json.dumps(protocol))
    complete = {"protocol_sha256":_sha(tmp_path/"protocol.json"),"files":{
        name:_sha(stage/name) for name in ("selected-model.pkl","results.json","integrity-audit.json")}}
    (stage/"complete.json").write_text(json.dumps(complete))
    (tmp_path/"current.json").write_text(json.dumps({"stage":25000,"complete_sha256":_sha(stage/"complete.json")}))
    loaded = AdaptiveReceptionPredictor.from_report(tmp_path,engine.database)
    payload = {"norad_id":25544,"start":(AS_OF+timedelta(hours=3)).isoformat()}
    assert loaded.predict_many([payload],as_of=AS_OF)[0]["p_demod_artifact"] == engine.predict_many([payload],as_of=AS_OF)[0]["p_demod_artifact"]
    (stage/"selected-model.pkl").write_bytes(b"corrupt")
    with pytest.raises(ValueError,match="artifact changed"):
        AdaptiveReceptionPredictor.from_report(tmp_path,engine.database)

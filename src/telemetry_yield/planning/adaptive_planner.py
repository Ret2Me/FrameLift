"""Audited local inference and an opt-in adapter for the existing TLE planner.

This module cannot submit SatNOGS jobs. It consumes only locally produced,
content-bound artifacts and captured-time reception history.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import pickle
import platform
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from . import adaptive_reception as adaptive
from .live_history import HistoryCursor, HistoryEvent, TASKS, utc
from .models import ProbabilityEstimate

OPTIONAL_NUMBERS = {"max_elevation_deg":(0,90),"duration_seconds":(0,None),"tle_age_hours":(0,None),
                    "frequency_hz":(0,None),"transmitter_baud":(0,None),
                    "rise_azimuth_deg":(0,360),"set_azimuth_deg":(0,360)}
OPTIONAL_STRINGS = ("transmitter_uuid","transmitter_mode")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda:stream.read(1024*1024),b""):
            digest.update(part)
    return digest.hexdigest()


def request_row(payload):
    allowed = {"norad_id","station_id","start",*OPTIONAL_NUMBERS,*OPTIONAL_STRINGS}
    if not isinstance(payload,dict) or set(payload)-allowed or {"norad_id","start"}-set(payload):
        raise ValueError("invalid prediction fields; outcomes are never accepted")
    row = dict(payload)
    if type(row["norad_id"]) is not int or row["norad_id"]<=0:
        raise ValueError("positive NORAD identifier required")
    row.setdefault("station_id",None)
    if row["station_id"] is not None and (type(row["station_id"]) is not int or row["station_id"]<=0):
        raise ValueError("station identifier must be positive or missing")
    if not isinstance(row["start"],str):
        raise ValueError("start must be a timezone-aware ISO timestamp")
    row["start"] = utc(datetime.fromisoformat(row["start"]))
    for name,(lower,upper) in OPTIONAL_NUMBERS.items():
        value = row.setdefault(name,None)
        if value is not None:
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
                raise ValueError(f"invalid {name}")
            if value<lower or (upper is not None and value>upper):
                raise ValueError(f"out-of-range {name}")
            if name in ("duration_seconds","frequency_hz","transmitter_baud") and value==0:
                raise ValueError(f"positive {name} required when supplied")
    for name in OPTIONAL_STRINGS:
        value = row.setdefault(name,None)
        if value is not None and (not isinstance(value,str) or not value.strip()):
            raise ValueError(f"invalid {name}")
    return row


def captured_events(db,task,as_of):
    # Read-only transaction shared by both heads; retractions and late uploads
    # are applied by HistoryCursor, not treated as extra observations.
    for stored in db.execute("SELECT observation_key,task,available_at,payload,sha256 FROM events WHERE task=? AND available_at<=? ORDER BY available_at,observation_key",
                             (task,as_of.isoformat())):
        key,kind,available,body,digest = stored
        if hashlib.sha256(body.encode()).hexdigest()!=digest:
            raise ValueError("captured history hash mismatch")
        event = HistoryEvent.parse(json.loads(body))
        if (event.observation_key,event.task,event.available_at.isoformat())!=(key,kind,available):
            raise ValueError("captured history index mismatch")
        if event.availability_kind!="captured" or event.available_at>as_of:
            raise ValueError("invalid live availability provenance")
        yield event


class AdaptiveReceptionPredictor:
    """A fixed selected bundle plus changing, availability-gated local history."""
    def __init__(self,bundle,database,*,artifact_identity):
        if set(bundle["heads"])!=set(TASKS) or not bundle.get("research_only"):
            raise ValueError("invalid adaptive bundle scope")
        self.bundle,self.database,self.artifact_identity = bundle,Path(database),artifact_identity
        self.created_at = utc(datetime.fromisoformat(bundle["created_at"]))
        for head in bundle["heads"].values():
            if head["method"] not in adaptive.CANDIDATES:
                raise ValueError("unknown selected method")
            if head["method"] not in adaptive.RULES and not isinstance(head["model"],adaptive.RecentModel):
                raise ValueError("selected learned method is missing its model")

    @classmethod
    def from_report(cls,report,database):
        """Only use a trusted locally generated report, never uploaded pickle files."""
        report = Path(report)
        protocol = json.loads((report/"protocol.json").read_text())
        runtime = {"python":platform.python_version(),**{p:importlib.metadata.version(p) for p in ("numpy","scipy","scikit-learn")}}
        if protocol["runtime"]!=runtime:
            raise ValueError("adaptive runtime changed")
        for source in protocol["sources"]:
            if _sha(source["path"])!=source["sha256"]:
                raise ValueError("frozen adaptive dependency changed")
        pointer = json.loads((report/"current.json").read_text())
        if type(pointer["stage"]) is not int or pointer["stage"]<25000:
            raise ValueError("invalid adaptive stage")
        out = report/f"labels-{pointer['stage']}"
        if _sha(out/"complete.json")!=pointer["complete_sha256"]:
            raise ValueError("selected manifest changed")
        complete = json.loads((out/"complete.json").read_text())
        if complete["protocol_sha256"]!=_sha(report/"protocol.json"):
            raise ValueError("selected protocol changed")
        for name,digest in complete["files"].items():
            if Path(name).name!=name or _sha(out/name)!=digest:
                raise ValueError("adaptive artifact changed")
        if not {"selected-model.pkl","results.json","integrity-audit.json"}.issubset(complete["files"]):
            raise ValueError("incomplete selected manifest")
        audit = json.loads((out/"integrity-audit.json").read_text())
        if not audit["available_checks_pass"] or audit["final_test_scored"]:
            raise ValueError("audited local research artifact required")
        with (out/"selected-model.pkl").open("rb") as stream:
            bundle = pickle.load(stream)
        return cls(bundle,database,artifact_identity=complete["files"]["selected-model.pkl"])

    def predict_many(self,payloads,*,as_of):
        as_of = utc(as_of)
        if as_of>datetime.now(UTC) or as_of<self.created_at:
            raise ValueError("issue time must not precede model creation or be in the future")
        rows = [request_row(p) for p in payloads]
        if any(row["start"]<=as_of for row in rows):
            raise ValueError("prediction must precede every target pass")
        if not self.database.is_file():
            raise ValueError("captured history store is missing")
        db = sqlite3.connect(f"file:{self.database.resolve()}?mode=ro",uri=True)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        results = [{"as_of":as_of.isoformat(),"pass_start":row["start"].isoformat(),"research_only":True,
            "artifact_sha256":self.artifact_identity,"tasks":{},
            "forecast_lead_hours":(row["start"]-as_of).total_seconds()/3600,
            "missing_optional_inputs":[key for key in (*OPTIONAL_NUMBERS,*OPTIONAL_STRINGS,"station_id") if row.get(key) is None],
            "limitations":["Development-selected methods, not final-test or prospective validation.",
                "Demodulation file proxy, not verified correct packets.",
                "Historical training assumes 24h label arrival; inference uses captured receipt times.",
                "Weather, power and antenna gain are not fitted inputs of this candidate."]} for row in rows]
        try:
            for task in TASKS:
                cursor = HistoryCursor(captured_events(db,task,as_of),task=task,availability_kind="captured")
                head = self.bundle["heads"][task]
                for row,result in zip(rows,results,strict=True):
                    h = cursor.snapshot(SimpleNamespace(**row),as_of=as_of,pass_start=row["start"])
                    probabilities = adaptive.history_probabilities(h)
                    if head["method"] not in adaptive.RULES:
                        probabilities = adaptive.candidate_probabilities(h,head["model"].predict(row,h))
                    result["tasks"][task] = {"probability":probabilities[head["method"]],"method":head["method"],
                        "active_model_features":([name for i,name in enumerate(adaptive.FEATURES)
                            if i not in head["model"].empty_columns] if head["method"] not in adaptive.RULES else []),
                        "pair_history_count":int(h["pair_count"]),"pair_last10_count":int(h["pair_last10_count"]),
                        "pair_days_since_last":h.get("pair_days_since_last"),"global_days_since_last":h.get("global_days_since_last"),
                        "history_sha256":hashlib.sha256(json.dumps(h,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()}
                del cursor
        finally:
            db.close()
        for result in results:
            result["p_signal_present"] = result["tasks"]["signal_present"]["probability"]
            result["p_demod_artifact_given_signal"] = result["tasks"]["decode_success_given_signal"]["probability"]
            result["p_demod_artifact"] = result["p_signal_present"]*result["p_demod_artifact_given_signal"]
            if not 2<=result["forecast_lead_hours"]<26:
                result["limitations"].append("Outside evaluated 2-26h lead; tentative extrapolation only.")
            if any(item["pair_history_count"]==0 for item in result["tasks"].values()):
                result["limitations"].append("Missing pair history: backoff to broader populations; cold-start quality unvalidated.")
            if any(item["global_days_since_last"] is None or item["global_days_since_last"]>30 for item in result["tasks"].values()):
                result["limitations"].append("Reception history is absent or older than 30 days; a fresh sync timestamp is not fresh reception coverage.")
        return results


class AdaptiveOpportunityBuilder:
    """Rescore feasible TLE opportunities, preserving blockers and radio rules.

    Construct with one explicit issue time per planning run. For later reception
    feedback, rebuild with a new issue time and request a history-driven replan.
    """
    def __init__(self,delegate,predictor,*,as_of,allow_extrapolation=False):
        self.delegate,self.predictor,self.as_of = delegate,predictor,utc(as_of)
        self.allow_extrapolation = allow_extrapolation

    def build(self,targets,stations,tles,horizon_start,horizon_end,*,blockers=(),evidence=()):
        opportunities = self.delegate.build(targets,stations,tles,horizon_start,horizon_end,
                                             blockers=blockers,evidence=evidence)
        rows,transmit = [],{}
        for target in targets:
            key = (target.norad_id,target.transmitter_uuid,target.frequency_hz)
            probability = target.transmission_rule.probability_when_allowed
            if key in transmit and transmit[key]!=probability:
                raise ValueError("ambiguous transmitter probability")
            transmit[key] = probability
        for op in opportunities:
            lead = (op.start-self.as_of).total_seconds()/3600
            if not self.allow_extrapolation and not 2<=lead<26:
                raise ValueError("month-ahead scoring requires explicit tentative extrapolation")
            features = op.metadata.get("feature_snapshot",{})
            rows.append({"norad_id":op.norad_id,"station_id":op.satnogs_station_id,"start":op.start.isoformat(),
                "max_elevation_deg":op.max_elevation_deg,"duration_seconds":op.duration_seconds,
                "frequency_hz":op.frequency_hz,"transmitter_mode":op.modulation,"transmitter_uuid":op.transmitter_uuid,
                "tle_age_hours":features.get("tle_age_hours"),"transmitter_baud":features.get("baud"),
                "rise_azimuth_deg":features.get("rise_azimuth_deg"),"set_azimuth_deg":features.get("set_azimuth_deg")})
        if not rows:
            return ()
        predictions = self.predictor.predict_many(rows,as_of=self.as_of)
        result = []
        for op,prediction in zip(opportunities,predictions,strict=True):
            allowed = transmit[(op.norad_id,op.transmitter_uuid,op.frequency_hz)]
            signal,decode = allowed*prediction["p_signal_present"],prediction["p_demod_artifact_given_signal"]
            estimate = ProbabilityEstimate(signal,decode,signal*decode,
                standard_deviation=allowed*.5,lower_90=0.,upper_90=allowed,
                missing_features=tuple(prediction["missing_optional_inputs"]),
                evidence_count=prediction["tasks"]["signal_present"]["pair_history_count"],
                model_version="adaptive-method-v3:"+prediction["artifact_sha256"][:16])
            old_contract = op.metadata.get("feature_use_contract",{})
            contract = {"schema_version":"probability-feature-use-v1","model_version":estimate.model_version,
                "active_probability_features":sorted({"availability_gated_reception_history"}.union(
                    *(set(v["active_model_features"]) for v in prediction["tasks"].values()))),
                "history_only_ignores_physical_inputs":all(v["method"] in adaptive.RULES for v in prediction["tasks"].values()),
                "active_feasibility_constraints":old_contract.get("active_feasibility_constraints",[]),
                "evaluation_only_features":[],"audit_status":"local_development_selected",
                "unused_predictive_inputs":["weather","transmit_power","antenna_gain","system_noise_temperature"],
                "receiver_antenna":old_contract.get("receiver_antenna",{})}
            result.append(replace(op,probability=estimate,expected_unique_samples=op.nominal_unique_samples*estimate.p_success,
                metadata={**op.metadata,"probability_model":estimate.model_version,"feature_use_contract":contract,
                    "adaptive_prediction":prediction,"transmission_probability_factor":allowed,
                    "uncertainty_semantics":"[0, transmission factor] support bounds and maximal SD, NOT calibrated 90% confidence.",
                    "planning_status":"research_shadow_tentative"}))
        return tuple(result)

"""Shared validation for persisted experiment source provenance."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
_REQUIRED_REVISIONS = {"daily_bars", "adj_factors", "universe", "trading_status", "trading_calendar", "price_limit_rules"}
_REQUIRED_DEPENDENCIES = {"daily_bars", "adj_factors", "trading_status", "trading_calendar"}

def validate_experiment_provenance(provenance: Any) -> None:
    """Fail closed unless declared identities are backed by retained receipts."""
    if not isinstance(provenance, Mapping):
        raise ValueError("Experiment data provenance must be a mapping")
    revisions = provenance.get("source_revisions")
    if not isinstance(revisions, Mapping):
        raise ValueError("Experiment data provenance source_revisions must be a mapping")
    missing_revisions = _REQUIRED_REVISIONS.difference(revisions)
    if missing_revisions:
        raise ValueError(f"Experiment data provenance is missing source revisions: {sorted(missing_revisions)}")
    normalized = {}
    for name in _REQUIRED_REVISIONS:
        identity = revisions[name]
        if not isinstance(identity, Mapping):
            raise ValueError(f"Experiment source revision {name} must be a structured identity")
        revision_id, fingerprint = identity.get("revision_id"), identity.get("contract_fingerprint")
        if not isinstance(revision_id, (str, int)) or isinstance(revision_id, bool) or not str(revision_id).strip():
            raise ValueError(f"Experiment source revision {name} has an invalid revision_id")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ValueError(f"Experiment source revision {name} has an invalid contract_fingerprint")
        normalized[name] = {"revision_id": revision_id, "contract_fingerprint": fingerprint}
        if name == "price_limit_rules":
            source = identity.get("source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Experiment price_limit_rules identity requires a source")
            normalized[name]["source"] = source.strip()
    receipts = provenance.get("source_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("Experiment provenance must retain adapter source_receipts")
    observed = {}
    for record in receipts:
        if not isinstance(record, Mapping):
            raise ValueError("Experiment source_receipts contains a malformed record")
        dataset, receipt = record.get("dataset"), record.get("receipt")
        if not isinstance(dataset, str) or not dataset.strip() or not isinstance(receipt, Mapping):
            raise ValueError("Experiment source_receipts record requires dataset and receipt")
        if receipt.get("replayable") is not True:
            raise ValueError(f"Experiment adapter receipt for {dataset} is not replayable")
        dependencies = receipt.get("dependencies")
        if not isinstance(dependencies, Mapping) or not dependencies:
            raise ValueError(f"Experiment adapter receipt for {dataset} has no dependencies")
        for name, identity in dependencies.items():
            if not isinstance(identity, Mapping):
                raise ValueError(f"Experiment receipt dependency {name} is malformed")
            revision_id, fingerprint = identity.get("revision_id"), identity.get("contract_fingerprint")
            if not isinstance(revision_id, (str, int)) or isinstance(revision_id, bool) or not str(revision_id).strip():
                raise ValueError(f"Experiment receipt dependency {name} has invalid revision_id")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                raise ValueError(f"Experiment receipt dependency {name} has invalid contract_fingerprint")
            value = {"revision_id": revision_id, "contract_fingerprint": fingerprint}
            if name in observed and observed[name] != value:
                raise ValueError(f"Experiment receipts disagree on dependency identity for {name}")
            observed[str(name)] = value
    missing = _REQUIRED_DEPENDENCIES.difference(observed)
    if missing:
        raise ValueError(f"Experiment receipts are missing dependencies: {sorted(missing)}")
    receipt_provenance = provenance.get("receipt_provenance")
    if not isinstance(receipt_provenance, Mapping) or dict(receipt_provenance.get("dependency_identities", {})) != observed:
        raise ValueError("Experiment receipt_provenance does not match retained adapter receipts")
    for name in _REQUIRED_DEPENDENCIES:
        if normalized[name] != observed[name]:
            raise ValueError(f"Experiment source revision {name} does not match retained receipt identity")
    if normalized["universe"] != observed["daily_bars"]:
        raise ValueError("Experiment universe revision does not match historical-universe receipt identity")
    acceptance = provenance.get("acceptance_source_receipts")
    if not isinstance(acceptance, Mapping):
        raise ValueError("Experiment provenance is missing acceptance_source_receipts")
    for name in ("historical_universe", "hfq"):
        if not isinstance(acceptance.get(name), Mapping) or acceptance[name].get("replayable") is not True:
            raise ValueError(f"Experiment provenance is missing replayable {name} acceptance receipt")
    limit = provenance.get("limit_evidence")
    if not isinstance(limit, Mapping) or limit.get("source_revision_identity") != dict(revisions["price_limit_rules"]):
        raise ValueError("Experiment limit evidence identity does not match price_limit_rules revision")
    if limit.get("revision") != revisions["price_limit_rules"]["revision_id"]:
        raise ValueError("Experiment limit evidence revision does not match price_limit_rules revision")

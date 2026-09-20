"""Deterministic guards for read-only, role-pinned PM ZCL diagnostics."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_pm_zcl_read_probe import READ_ALLOWLIST, validated_read, classify


def test_only_allowlisted_non_mutating_payloads():
    assert validated_read('haElectricalMeasurement','activePower') == {
        'read': {'cluster':'haElectricalMeasurement','attributes':['activePower']}}
    assert validated_read('seMetering','multiplier') == {
        'read': {'cluster':'seMetering','attributes':['multiplier']}}
    for cluster,attr in [('genOnOff','onOff'),('haElectricalMeasurement','state'),
                         ('seMetering','resetEnergy'),('haElectricalMeasurement','')]:
        with pytest.raises(ValueError,match='non-mutating'):validated_read(cluster,attr)


def test_payload_never_emits_relay_or_configuration_actions():
    for cluster,attrs in READ_ALLOWLIST.items():
        for attr in attrs:
            encoded=json.dumps(validated_read(cluster,attr)).lower()
            assert 'reporting' not in encoded and 'configure' not in encoded
            assert 'state_relay' not in encoded and 'resetenergy' not in encoded
            assert set(json.loads(encoded))=={'read'}


def test_unsupported_attribute_takes_precedence_over_unrelated_mqtt():
    errors=["z2m: Publish 'set' 'read' to 'example' failed (Status 'UNSUPPORTED_ATTRIBUTE')"]
    assert classify(errors,[(1,{'power':0})])=='unsupported_attribute'
    assert classify(['ZCL command failed (Status TIMEOUT)'],[])=='zcl_error'


def test_mqtt_observation_is_not_independent_zcl_success_proof():
    assert classify([],[(1,{'power':0})])=='mqtt_observed_after_read_response_unproven'
    assert classify([],[])=='no_read_response_proven'

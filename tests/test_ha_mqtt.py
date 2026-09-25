"""Unit tests for the HA MQTT probe (pure helpers only; no SSH/MQTT)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))

import ha_mqtt


def test_sub_command_quotes_topic_and_credentials():
    cmd = ha_mqtt.sub_command('user1', 'pw1', 'broker', 'some/topic', count=2, wait=9)
    assert '-u user1' in cmd and '-P pw1' in cmd
    assert '-t some/topic' in cmd and '-C 2' in cmd and '-W 9' in cmd
    assert 'pw1' in cmd  # runtime only; never asserted in committed tests


def test_pub_command_single_quotes_json_payload():
    cmd = ha_mqtt.pub_command('u', 'p', 'b', 't/get', '{"a": ""}')
    assert "-m '{\"a\": \"\"}'" in cmd


def test_find_device_matches_ieee_or_name():
    devices = [{'ieee_address': '0x1', 'friendly_name': 'Alpha'},
               {'ieee_address': '0x2', 'friendly_name': 'Beta'}]
    assert ha_mqtt.find_device(devices, '0x2')['friendly_name'] == 'Beta'
    assert ha_mqtt.find_device(devices, 'Alpha')['ieee_address'] == '0x1'


def test_find_device_rejects_ambiguous_or_missing():
    devices = [{'ieee_address': '0x1', 'friendly_name': 'Alpha'}]
    for bad in ('0x9', 'Unknown'):
        try:
            ha_mqtt.find_device(devices, bad)
        except RuntimeError:
            continue
        raise AssertionError('expected RuntimeError for %r' % bad)


def test_summarize_device_keeps_stable_key_set():
    summary = ha_mqtt.summarize_device({'friendly_name': 'X', 'extra': 1})
    assert summary['friendly_name'] == 'X'
    assert 'extra' not in summary
    assert 'interview_state' in summary and 'software_build_id' in summary


def test_parser_requires_subcommand_and_join_args():
    parser = ha_mqtt.build_parser()
    args = parser.parse_args(['join', '--via', 'SomeRouter', '--seconds', '60'])
    assert args.func is ha_mqtt.cmd_join
    assert args.via == 'SomeRouter' and args.seconds == 60


def test_join_scope_defaults_to_network_wide():
    parser = ha_mqtt.build_parser()
    args = parser.parse_args(['join', '--seconds', '60'])
    assert args.via is None and args.seconds == 60

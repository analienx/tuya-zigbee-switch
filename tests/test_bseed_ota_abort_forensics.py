"""Offline regression: preserve abort evidence without guessing a root cause."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_ota_abort_forensics import analyze, parse_campaign, parse_sdk


def log(tmp_path):
    path=tmp_path/'private.jsonl'
    events=[
        {'when':'2026-09-20T20:39:43+02:00','event':'ota_request_sent',
         'value':{'default_maximum_data_size':50}},
        {'when':'2026-09-20T20:41:27+02:00','event':'z2m_log',
         'value':{'message':'OTA update at 1.86%, 5434 seconds remaining'}},
        {'when':'2026-09-20T20:42:30+02:00','event':'ota_final',
         'value':{'phase':'update_error','response':{'status':'error','error':'reason: ABORT'}}}]
    path.write_text('\n'.join(map(json.dumps,events)),encoding='utf8')
    return path


def test_info_only_abort_is_not_claimed_as_verified_timeout_or_offset(tmp_path):
    report=analyze(log(tmp_path))
    assert report['device_abort'] and report['last_progress_percent']==1.86
    assert report['seconds_from_last_progress_to_terminal']==63
    assert report['last_repeated_requested_offset'] is None
    assert report['block_request_count'] is None
    assert report['device_requested_block_sizes']=='not captured'
    assert report['root_cause']=='unproven' and not report['retry_authorized']


def test_raw_repeated_offsets_are_descriptive_not_acknowledgement(tmp_path):
    raw=tmp_path/'trace.log'
    raw.write_text('Image block request fileOffset=0 maxDataSize=48\n'
        'Payload offsets fileOffset=0 dataSize=48\n'
        'Image block request fileOffset=48 maxDataSize=48\n'
        'Image block request fileOffset=48 maxDataSize=48\n',encoding='utf8')
    result=analyze(log(tmp_path),raw)
    assert result['raw_trace_available']
    assert result['block_request_count']==3
    assert result['device_requested_block_sizes']==[48]
    assert result['repeated_offsets']==[48]
    assert result['last_repeated_requested_offset']==48
    assert result['prepared_payload_sizes']==[48]
    assert result['root_cause']=='unproven'


def test_reject_incomplete_or_multi_campaign(tmp_path):
    path=log(tmp_path)
    path.write_text(path.read_text(encoding='utf8')+'\n'+path.read_text(encoding='utf8'))
    with pytest.raises(ValueError,match='one OTA request'):parse_campaign(path)


def test_sdk_contract_reads_exact_installed_sdk_header(tmp_path):
    header=tmp_path/'ota.h'
    header.write_text('#define OTA_IMAGE_MAX_DATA_SIZE 48\n'
        '#define OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME 5 // seconds\n'
        '#define OTA_MAX_IMAGE_BLOCK_RETRIES 10\n')
    assert parse_sdk(header)=={'OTA_IMAGE_MAX_DATA_SIZE':48,
        'OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME':5,'OTA_MAX_IMAGE_BLOCK_RETRIES':10}

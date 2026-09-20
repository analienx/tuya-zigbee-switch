"""Offline single-transaction OTA ABORT forensics; never accesses a live device.

Consumes private campaign JSONL and optional pre-existing Zigbee2MQTT raw trace.
No unsupported root-cause inference or automatic retry authorization.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import re

PROGRESS = re.compile(r'\bat ([0-9]+(?:\.[0-9]+)?)%')
OFFSET = re.compile(r'\b(?:fileOffset|file_offset|offset)\s*[=:]\s*(\d+)')
SIZE = re.compile(r'\b(?:dataSize|data_size)\s*[=:]\s*(\d+)')
REQUEST_MAX = re.compile(r'\b(?:maxDataSize|max_data_size)\s*[=:]\s*(\d+)')
SDK_NAMES = ('OTA_IMAGE_MAX_DATA_SIZE', 'OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME',
             'OTA_MAX_IMAGE_BLOCK_RETRIES')


def parse_sdk(header):
    content = Path(header).read_text(encoding='utf8')
    found = {}
    for name in SDK_NAMES:
        match = re.search(r'^\s*#define\s+' + name + r'\s+(\d+)\b', content, re.M)
        if not match: raise ValueError('Missing SDK constant: ' + name)
        found[name] = int(match.group(1))
    return found


def parse_campaign(source):
    events = [json.loads(line) for line in Path(source).read_text(encoding='utf8').splitlines() if line.strip()]
    starts = [x for x in events if x.get('event') == 'ota_request_sent']
    finals = [x for x in events if x.get('event') == 'ota_final']
    if len(starts) != 1 or len(finals) != 1:
        raise ValueError('Require one OTA request and exactly one terminal result')
    progress = []
    for event in events:
        text = str(event.get('value', {}).get('message', '')) if isinstance(event.get('value'), dict) else ''
        match = PROGRESS.search(text)
        if event.get('event') == 'z2m_log' and match:
            progress.append((event['when'], float(match.group(1))))
    final = finals[0]
    return starts[0], final, progress


def parse_raw_trace(source):
    if source is None: return {'available': False, 'requests': [], 'payload_sizes': [], 'requested_sizes': []}
    requests, sizes, request_maxima = [], [], []
    for line in Path(source).read_text(encoding='utf8', errors='replace').splitlines():
        if 'block' not in line.lower() and 'payload offsets' not in line.lower():
            continue
        offset = OFFSET.search(line)
        size = SIZE.search(line)
        requested = REQUEST_MAX.search(line)
        if offset and ('request' in line.lower() or 'imageblockreq' in line.lower()):
            requests.append(int(offset.group(1)))
            if requested: request_maxima.append(int(requested.group(1)))
        if size and 'payload offsets' in line.lower(): sizes.append(int(size.group(1)))
    return {'available': True, 'requests': requests, 'payload_sizes': sizes,
            'requested_sizes': request_maxima}


def analyze(campaign, raw=None, sdk=None):
    start, final, progress = parse_campaign(campaign)
    response = final.get('value', {}).get('response', {})
    error = str(response.get('error', ''))
    last_at, last_percent = progress[-1] if progress else (None, None)
    stalled_seconds = None
    if last_at:
        stalled_seconds = round((dt.datetime.fromisoformat(final['when']) -
                                 dt.datetime.fromisoformat(last_at)).total_seconds(), 2)
    trace = parse_raw_trace(raw)
    requests = trace.pop('requests'); sizes = trace.pop('payload_sizes')
    requested_sizes = trace.pop('requested_sizes')
    repeated = [x for x, y in zip(requests, requests[1:]) if x == y]
    result = {'status': response.get('status'), 'device_abort': 'ABORT' in error,
              'terminal_phase': final.get('value', {}).get('phase'),
              'last_progress_percent': last_percent, 'last_progress_at': last_at,
              'seconds_from_last_progress_to_terminal': stalled_seconds,
              'server_maximum_data_size': start['value']['default_maximum_data_size'],
              'raw_trace_available': trace['available'],
              'device_requested_block_sizes': sorted(set(requested_sizes)) if requested_sizes else 'not captured',
              'block_request_count': len(requests) if trace['available'] else None,
              'repeated_offsets': repeated, 'prepared_payload_sizes': sorted(set(sizes)),
              'sdk_constants': parse_sdk(sdk) if sdk else None,
              'last_repeated_requested_offset': None, 'root_cause': 'unproven',
              'retry_authorized': False, 'hardware_accepted': False}
    if len(requests)>1 and requests[-1]==requests[-2]:
        result['last_repeated_requested_offset'] = requests[-1]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-log', required=True)
    parser.add_argument('--raw-log', help='Optional pre-existing device-specific Z2M debug trace')
    parser.add_argument('--sdk-header', help='Read-only path to actual Telink OTA SDK header')
    parser.add_argument('--output', required=True, help='New PRIVATE JSON path outside repo')
    args = parser.parse_args(argv)
    destination = Path(args.output).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(repo) or destination.exists():
        parser.error('Refusing public or existing forensic evidence output')
    report = analyze(args.campaign_log, args.raw_log, args.sdk_header)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf8') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({'output': str(destination), **report}, indent=2))


if __name__ == '__main__': main()

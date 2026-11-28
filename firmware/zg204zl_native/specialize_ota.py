"""Specialize the pinned SDK for this sensor's existing OTA-client role.

Run after `make install`, on GitHub-hosted Actions only. This retains every
server-to-client receive path and all client download/CRC/NV/restart code.
The sensor does not serve firmware to other devices.
"""
import argparse
import hashlib
import json
from pathlib import Path

PINS = {
    'zigbee/ota/ota.c': '8e1a92ec8ccc605c92aff20eb6aac68ebb8ab75b30c10dc709a85ced829f5848',
    'zigbee/zcl/ota_upgrading/zcl_ota.c': 'd559b5db965fec064d20e5c0800d68974fd43aebd902879c94d86d4f30c87b94',
}


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('OTA specialization anchor changed')
    return text.replace(old, new, 1)


def sources(raw):
    for name, expected in PINS.items():
        if hashlib.sha256(raw[name]).hexdigest() != expected:
            raise ValueError(f'{name}: SDK hash mismatch')
    texts = {name: data.decode().replace('\r', '') for name, data in raw.items()}
    name = 'zigbee/ota/ota.c'
    text = replace(texts[name],
        'g_otaCtx.isOtaServer = (type == OTA_TYPE_SERVER) ? TRUE : FALSE;',
        '(void)type; /* This board is an OTA client only. */\n    g_otaCtx.isOtaServer = FALSE;')
    text = replace(text, 'if (g_otaCtx.isOtaServer) {', 'if (0) { /* no OTA server */')
    text = replace(text, 'if (!g_otaCtx.isOtaServer) {', 'if (1) { /* OTA client */')
    begin = text.index('if (pAddrInfo->dirCluster == ZCL_FRAME_CLIENT_SERVER_DIR)',
                       text.index('status_t zcl_otaCb('))
    end = text.index('        } else {', begin)
    text = text[:begin] + ('if (pAddrInfo->dirCluster == ZCL_FRAME_CLIENT_SERVER_DIR) {\n'
                          '            status = ZCL_STA_UNSUP_CLUSTER_COMMAND;\n') + text[end:]
    texts[name] = text
    name = 'zigbee/zcl/ota_upgrading/zcl_ota.c'
    texts[name] = replace(texts[name], 'return zcl_ota_clientCmdHandler(pInMsg);',
                          'return ZCL_STA_UNSUP_CLUSTER_COMMAND; /* no OTA server */')
    return {name: text.encode() for name, text in texts.items()}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    args = p.parse_args()
    app = (args.source / 'src/app_main.c').read_text()
    if app.count('ota_init(OTA_TYPE_CLIENT,') != 1 or app.count('ota_init(') != 1:
        raise SystemExit('Board OTA-client role no longer proven')
    paths = {name: args.source / 'SDK_z' / name for name in PINS}
    output = sources({name: path.read_bytes() for name, path in paths.items()})
    for name, data in output.items():
        paths[name].write_bytes(data)
    print(json.dumps({'role': 'OTA_CLIENT_ONLY', 'sha256': {
        name: hashlib.sha256(data).hexdigest() for name, data in output.items()}}, indent=2))

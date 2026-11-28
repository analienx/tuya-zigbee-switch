"""Fail-closed native-control overlay; execute only on GitHub-hosted Actions."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'zg204zl_reliability'))
import apply as baseline

VERSION = 0x01493001
EXTRA_PINS = {
    'make_z/src.mk': '351a56cd7c64e40ea98b8acca42c73c9ce2bc936',
    'src/app_EpCfg.c': '3e709468af63f091a50a68bac4bfdf360fa562cb',
    'src/app_onoff.h': 'd2af60ec574a03344cbb6b9792a35205908a72dc',
    'src/board_zg204zl.h': '29d92bd62f520786d4f60c6879d82aa86783647d',
    'src/patch_z_sdk/ev_rtc.c': 'f5c56bc905917360d84f5f2d47afd2893d3da826',
    'src/zcl_appCb.c': 'a15e84f25990b58202a16a49e24671ca8b76bda8',
}


def sources(root):
    edits = baseline.modified_sources(root, 'v0148')
    for name, sha in EXTRA_PINS.items():
        raw = (root / name).read_bytes()
        if baseline.git_blob_sha(raw) != sha:
            raise ValueError(f'{name}: upstream blob mismatch')
        edits[name] = raw
    texts = {k: v.decode('utf8') for k, v in edits.items()}

    def replace(name, old, new):
        texts[name] = baseline.replace_once(texts[name], old, new, name)

    replace('src/version_cfg.h', '#define APP_BUILD\t\t\t\t0x48', '#define APP_BUILD\t\t\t\t0x49')
    replace('src/board_zg204zl.h', '#define DEV_SERVICES', '#define USE_RETRY_ONOFF 0\n\n#define DEV_SERVICES')
    replace('src/app_cfg.h', '\tNV_ITEM_APP_DHUM_CFG\n', '\tNV_ITEM_APP_DHUM_CFG,\n\tNV_ITEM_APP_NATIVE_CFG\n')
    replace('src/patch_z_sdk/ev_rtc.c', 'u32 rtcSeconds;', 'u32 rtcSeconds;\nu32 rtcMs;')
    replace('src/patch_z_sdk/ev_rtc.c', '    rtcRemainingMs += updateTime;',
            '    rtcMs += updateTime;\n    rtcRemainingMs += updateTime;')
    for name in ('src/app_main.c', 'src/app_EpCfg.c', 'src/sensor_pir.c', 'src/zcl_appCb.c'):
        replace(name, '#include "tl_common.h"', '#include "tl_common.h"\n#include "nc_port_tlsr.h"')
    replace('src/app_main.c', '\tpopulate_hw_version();', '\tpopulate_hw_version();\n\tnc_init();')
    replace('src/app_main.c', ', zcl_rx_handler, afTestOnOffCb);', ', nc_rx, nc_aps_confirm);')
    replace('src/app_main.c', '\tapp_rejoinService();', '\tnc_service();\n\tapp_rejoinService();')
    replace('src/app_main.c', '#if USE_RETRY_ONOFF\n\t\t\ttaskRetryOnOff();\n#endif', '')
    # A motion event remains eligible even when reported occupancy was already 1.
    replace('src/sensor_pir.c', '\t\t\t\tapp_rejoinOnMotion();', '\t\t\t\tapp_rejoinOnMotion();\n\t\t\t\tnc_motion();')
    start = texts['src/sensor_pir.c'].index('#ifdef ZCL_ILLUMINANCE_LEVEL_SENSING\n#if !USE_SENSOR_XBR818',
                                             texts['src/sensor_pir.c'].index('void task_pir'))
    end = texts['src/sensor_pir.c'].index('\n\t\t\t\t}\n#if USE_BLINK_LED', start)
    texts['src/sensor_pir.c'] = texts['src/sensor_pir.c'][:start] + texts['src/sensor_pir.c'][end:]
    old = ('\tif(g_zcl_onOffAttrs.remoteOnOff) {\n'
           '\t\t// old cmd = ZCL_CMD_ONOFF_ON -> ZCL_CMD_ONOFF_OFF\n'
           '\t\tremoteCmdOnOff(SENSOR_DEVICE_ENDPOINT, ZCL_CMD_ONOFF_OFF);\n\t}\n')
    replace('src/sensor_pir.c', old, '\tnc_absence();\n')
    # ZG204ZL compiles out the other-board XBR818 branch; do not leave references
    # to the removed transport even in that source branch.
    texts['src/sensor_pir.c'] = texts['src/sensor_pir.c'].replace(
        'remoteCmdOnOff(SENSOR_DEVICE_ENDPOINT, ZCL_CMD_ONOFF_OFF);', 'nc_absence();')
    replace('src/sensor_pir.c', '\tzcl_onOffAttr_t *pOnOff = zcl_onoffAttrGet();\n', '')
    name = 'src/zcl_appCb.c'
    start = texts[name].index('#if USE_RETRY_ONOFF\nextern u32 rtcSeconds;')
    end = texts[name].index('/*******************************************************************\n * @brief\tcmdOnOff_set', start)
    texts[name] = texts[name][:start] + texts[name][end:]
    start = texts[name].index('#if USE_REMOTE_ONOFF && (DEV_SERVICES & SERVICE_PIR)',
                               texts[name].index('void cmdOnOff_set'))
    end = texts[name].index('#endif', start) + len('#endif')
    texts[name] = texts[name][:start] + '\t\tnc_master(status);' + texts[name][end:]
    replace(name, '\tif(clusterId == ZCL_CLUSTER_GEN_ON_OFF) {\n\t\tzcl_onoffConfig_save(0);',
            '\tif(clusterId == ZCL_CLUSTER_GEN_ON_OFF) {\n\t\tzcl_onoffConfig_save(0);\n'
            '\t\tnc_master(g_zcl_onOffAttrs.onOff);')
    name = 'src/app_onoff.h'
    for old in ('void remoteCmdOnOff(u8 srcEp, u8 cmd);\n', 'void taskRetryOnOff(void);\n'):
        replace(name, old, '')
    replace('src/app_EpCfg.c', 'const u16 sensorDevice_outClusterList[] =\n{',
            'const u16 sensorDevice_outClusterList[] =\n{\n    0x0008, 0x000a,')
    replace('src/app_EpCfg.c', 'const u16 sensorDevice_inClusterList[] =\n{',
            'const u16 sensorDevice_inClusterList[] =\n{\n    NC_CLUSTER,')
    replace('src/app_EpCfg.c', 'const zcl_specClusterInfo_t g_sensorDeviceClusterList[] =\n{',
            'const zcl_specClusterInfo_t g_sensorDeviceClusterList[] =\n{\n'
            '    {NC_CLUSTER, NC_MANUFACTURER, 2, nc_attrs, nc_cluster_register, NULL},')
    replace('src/app_EpCfg.c', 'const u8 SENSOR_DEVICE_CB_CLUSTER_NUM =',
            'typedef char nc_cluster_capacity[(sizeof(g_sensorDeviceClusterList) / '
            'sizeof(g_sensorDeviceClusterList[0]) + 1 <= ZCL_CLUSTER_NUM_MAX) ? 1 : -1];\n'
            'const u8 SENSOR_DEVICE_CB_CLUSTER_NUM =')
    objects = ''.join('$(OUT_PATH)$(SRC_DIR)/' + p.stem + '.o \\\n'
                      for p in sorted((HERE / 'native').glob('*.c')) if p.name != 'nc_all.c')
    replace('make_z/src.mk', 'OBJS += \\\n', 'OBJS += \\\n' + objects)
    output = {k: v.encode() for k, v in texts.items()}
    for path in (HERE / 'native').iterdir():
        if path.suffix in ('.c', '.h'):
            output['src/' + path.name] = path.read_bytes()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != baseline.UPSTREAM_COMMIT:
        raise ValueError('Wrong immutable upstream checkout')
    output = sources(args.source)
    for name, data in output.items():
        (args.source / name).write_bytes(data)
    print(json.dumps({'file_version': f'0x{VERSION:08x}', 'upstream': actual,
                      'outputs': {k: hashlib.sha256(v).hexdigest() for k, v in output.items()}}, indent=2))


if __name__ == '__main__':
    main()

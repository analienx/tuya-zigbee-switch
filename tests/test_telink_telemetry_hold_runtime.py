"""Production report wrappers linked through --wrap, with explicit SDK peers.

Host tests do not model RF delivery. Real SDK headers/linkage are checked by
the hosted six-role firmware builds; this harness checks recipient isolation.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_report_fanout_coalescing_and_current_value_flush(tmp_path):
    header = r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint8_t status_t;
typedef union {u16 shortAddr; u8 extAddr[8];} tl_zb_addr_t;
typedef struct {tl_zb_addr_t dstAddr; u16 profileId; u8 dstAddrMode,dstEp;} epInfo_t;
typedef struct {u8 extAddr[8],dstEp;} aps_binding_extaddr_t;
typedef struct {u16 clusterId;u8 srcEp,dstAddrMode;
 union {u16 groupAddr; aps_binding_extaddr_t dstExtAddrInfo;};u8 used;} aps_binding_entry_t;
typedef struct {u16 id;u8 type;u8 *data;} zclAttrInfo_t;
typedef struct {u16 attrID;u8 dataType;u8 *attrData;} zclReport_t;
typedef struct {u8 numAttr;zclReport_t attrList[2];} zclReportCmd_t;
typedef struct {u8 extAddress[8];} mac_t;
extern mac_t g_zbMacPib;
extern aps_binding_entry_t g_apsBindingTbl[4];
extern u8 APS_BINDING_TABLE_SIZE;
#define APS_DSTADDR_EP_NOTPRESETNT 0
#define APS_SHORT_GROUPADDR_NOEP 1
#define APS_SHORT_DSTADDR_WITHEP 2
#define APS_LONG_DSTADDR_WITHEP 3
#define ZCL_CLUSTER_MS_ELECTRICAL_MEASUREMENT 0x0b04
#define ZCL_CLUSTER_SE_METERING 0x0702
#define ZCL_FRAME_SERVER_CLIENT_DIR 1
#define MANUFACTURER_CODE_NONE 0xffff
#define SUCCESS 0
#define TRUE 1
#define ZCL_SEQ_NUM 33
u8 zb_getNwkAddrByExtAddr(u8 *ext,u16 *nwk);
zclAttrInfo_t *zcl_findAttribute(u8 ep,u16 cluster,u16 attr);
bool telink_zigbee_hal_hold_coordinator_telemetry(void);
void telink_zigbee_hal_telemetry_tick(void);
status_t zcl_report(u8,epInfo_t*,u8,u8,u8,u16,u16,u16,u8,u8*);
status_t zcl_reportAttrs(u8,epInfo_t*,u8,u8,u8,u16,u16,zclReportCmd_t*);
extern unsigned coord,peer,group,other,batch;
extern u8 latest,live[2];extern bool hold;
extern uint32_t now;
'''
    sdk = r'''
#include "fake_sdk.h"
#include <assert.h>
mac_t g_zbMacPib={{1,2,3,4,5,6,7,8}};
aps_binding_entry_t g_apsBindingTbl[4];u8 APS_BINDING_TABLE_SIZE=4;
unsigned coord,peer,group,other,batch;u8 latest,live[2]={1,2};bool hold;
uint32_t now;
uint32_t hal_millis(void){return now;}
bool telink_zigbee_hal_hold_coordinator_telemetry(void){return hold;}
u8 zb_getNwkAddrByExtAddr(u8 *ext,u16 *nwk){
 if(ext[0]==1){*nwk=0;return 0;} if(ext[0]==2){*nwk=0x1234;return 0;}return 0xff;}
zclAttrInfo_t *zcl_findAttribute(u8 ep,u16 cluster,u16 attr){
 static zclAttrInfo_t a[2]; if(ep!=1 || attr>1)return NULL;
 a[attr]=(zclAttrInfo_t){attr,0x20,&live[attr]};return &a[attr];}
status_t zcl_report(u8 src,epInfo_t *dst,u8 disable,u8 direction,u8 seq,u16 mfr,
 u16 cluster,u16 attr,u8 type,u8 *data){
 if(dst->dstAddrMode==2 && dst->dstAddr.shortAddr==0){coord++;latest=*data;}
 else if(dst->dstAddrMode==3 && dst->dstAddr.extAddr[0]==2)peer++;
 else if(dst->dstAddrMode==1)group++;else other++;
 return 0;}
status_t zcl_reportAttrs(u8 src,epInfo_t *dst,u8 disable,u8 direction,u8 seq,u16 mfr,
 u16 cluster,zclReportCmd_t *attrs){batch++;return 0;}
'''
    cases = r'''
#include "fake_sdk.h"
#include <assert.h>
static void send(epInfo_t *dst,u16 cluster,u16 attr){
 assert(zcl_report(1,dst,1,1,9,0xffff,cluster,attr,0x20,&live[attr])==0);}
static void flush(void){telink_zigbee_hal_telemetry_tick();now+=61000;
 for(unsigned i=0;i<35;i++){telink_zigbee_hal_telemetry_tick();now+=1000;}}
int main(void){
 epInfo_t dst={0};dst.profileId=0x0104;
 for(unsigned i=0;i<3;i++){
  g_apsBindingTbl[i].used=1;g_apsBindingTbl[i].srcEp=1;
  g_apsBindingTbl[i].clusterId=0x0b04;
  g_apsBindingTbl[i].dstAddrMode=i==2?1:3;
  g_apsBindingTbl[i].dstExtAddrInfo.extAddr[0]=i+1;
  g_apsBindingTbl[i].dstExtAddrInfo.dstEp=1;}
 hold=true;send(&dst,0x0b04,0);live[0]=9;send(&dst,0x0b04,0);
 assert(coord==0 && peer==2 && group==2);flush();assert(coord==0);
 send(&dst,6,0);assert(other==1); /* relay reports bypass all holding */
 hold=false;flush();assert(coord==1 && latest==9 && peer==2 && group==2);
 hold=true;send(&dst,0x0b04,0);g_apsBindingTbl[0].used=0;
 hold=false;flush();assert(coord==1); /* never resurrect an unbound destination */
 dst.dstAddrMode=2;dst.dstAddr.shortAddr=0;dst.dstEp=7;
 hold=true;send(&dst,0x0702,0);live[0]=12;hold=false;flush();
 assert(coord==2 && latest==12); /* explicit coordinator recipient retained */
 dst.dstAddrMode=0;g_apsBindingTbl[0].used=1;
 zclReportCmd_t attrs={2,{{0,0x20,&live[0]},{1,0x20,&live[1]}}};
 unsigned p=peer,g=group;hold=true;
 assert(zcl_reportAttrs(1,&dst,1,1,9,0xffff,0x0b04,&attrs)==0);
 assert(peer==p+2 && group==g+2 && batch==0 && coord==2);
 live[1]=22;hold=false;flush();assert(coord==4);
 assert(zcl_reportAttrs(1,&dst,1,1,9,0xffff,0x0b04,&attrs)==0 && batch==1);
 /* Unknown IEEE addresses are preserved, never guessed to be coordinator. */
 dst.dstAddrMode=3;dst.dstAddr.extAddr[0]=99;hold=true;
 send(&dst,0x0b04,0);assert(other==2);
 return 0;
}
'''
    (tmp_path / 'fake_sdk.h').write_text(header)
    source = (ROOT / 'src/telink/hal/zigbee_telemetry.c').read_text()
    (tmp_path / 'telemetry.c').write_text(source.replace(
        '#include "telink_zigbee_hal.h"', '#include "fake_sdk.h"'))
    (tmp_path / 'sdk.c').write_text(sdk)
    (tmp_path / 'case.c').write_text(cases)
    binary = tmp_path / 'run'
    built = subprocess.run(['cc', '-std=c11', '-Wall', '-Werror',
                            '-I', str(ROOT / 'src'), '-I', str(tmp_path),
                            str(tmp_path / 'telemetry.c'), str(tmp_path / 'sdk.c'),
                            str(tmp_path / 'case.c'), '-Wl,--wrap=zcl_report',
                            '-Wl,--wrap=zcl_reportAttrs', '-o', str(binary)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr


def test_coordinator_hold_requires_fresh_proof(tmp_path):
    source = (ROOT / 'src/telink/hal/zigbee_network.c').read_text()
    body = 'bool telink_zigbee_hal_hold_coordinator_telemetry(void)' + source.split(
        'bool telink_zigbee_hal_hold_coordinator_telemetry(void)', 1)[1].split(
        'void telink_zigbee_hal_network_init', 1)[0]
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#define ZB_ROUTER_ROLE 1
#define ZB_COORDINATOR_ROLE 0
#define ROUTER_PROBE_REJECT_THRESHOLD 8
static bool joined=true, router_positive_seen;
static unsigned router_probe_failures,router_probe_rejections;
static uint32_t router_last_positive_ms;
static bool zb_isDeviceJoinedNwk(void){return joined;}
'''
    cases = r'''
int main(void){
 assert(!telink_zigbee_hal_hold_coordinator_telemetry());
 router_probe_failures=2;assert(telink_zigbee_hal_hold_coordinator_telemetry());
 router_probe_failures=0;assert(telink_zigbee_hal_hold_coordinator_telemetry());
 router_positive_seen=true;router_last_positive_ms=1;
 assert(!telink_zigbee_hal_hold_coordinator_telemetry());
 joined=false;assert(telink_zigbee_hal_hold_coordinator_telemetry());
 joined=true;assert(telink_zigbee_hal_hold_coordinator_telemetry());
 router_last_positive_ms=2;assert(!telink_zigbee_hal_hold_coordinator_telemetry());
 return 0;}
'''
    code = tmp_path / 'hold.c'
    code.write_text(prelude + body + cases)
    binary = tmp_path / 'hold'
    built = subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', str(code),
                            '-o', str(binary)], capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr

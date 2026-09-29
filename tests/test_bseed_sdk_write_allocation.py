"""Compile the actual pinned SDK handlers and inject allocation/send failures."""
from pathlib import Path
import re
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from patch_telink_zcl import patch_source


def sdk_source():
    # Populated from the same pinned SDK tag as tools.mk, by public Actions.
    return (ROOT / 'build/test-sdk/zcl.c').read_text()


def test_patch_fails_closed_on_sdk_drift():
    source = sdk_source()
    with pytest.raises(ValueError):
        patch_source(source.replace('if (!pWriteRspCmd)', 'if (pWriteRspCmd == NULL)'))
    with pytest.raises(ValueError):
        patch_source(patch_source(source))


def test_actual_sdk_write_handlers_do_not_dispatch_unapplied_commands(tmp_path):
    source = patch_source(sdk_source())
    handlers = []
    for name in ('zcl_writeHandler', 'zcl_writeUndividedHandler'):
        start = source.index('_CODE_ZCL_ status_t ' + name + '(zclIncoming_t *pCmd)\n{')
        handlers.append(source[start:source.index('\n}', start) + 2])
    # Use the actual SDK's final application dispatch and ownership transfer.
    dispatch = re.search(r'    if \(zcl_vars.hookFn && toAppFlg && inMsg.attrCmd\) \{.*?\n    \}',
                         source, re.S).group()
    shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef u8 status_t;
#define _CODE_ZCL_
#define TRUE true
#define FALSE false
#define ZCL_STA_SUCCESS 0
#define ZCL_STA_INSUFFICIENT_SPACE 0x89
#define ZCL_STA_CMD_HAS_RESP 0xff
#define ZCL_CMD_WRITE 2
#define ZCL_CMD_WRITE_UNDIVIDED 3
#define ZCL_CMD_WRITE_NO_RSP 5
#define APS_SHORT_DSTADDR_WITHEP 2
#define APS_TX_OPT_ACK_TX 1
#define APS_TX_OPT_SECURITY_ENABLED 2
#define SECURITY_IN_APSLAYER 1
#define TL_SETSTRUCTCONTENT(x,v) memset(&(x),v,sizeof(x))
typedef struct {u16 attrID; u8 value;} zclWriteRec_t;
typedef struct {u8 numAttr; zclWriteRec_t attrList[2];} zclWriteCmd_t;
typedef struct {u8 status; u16 attrID;} zclWriteRspStatus_t;
typedef struct {u8 numAttr; zclWriteRspStatus_t attrList[2];} zclWriteRspCmd_t;
typedef struct {u16 cluster_id, profile_id, src_short_addr; u8 dst_ep,src_ep,security_status;} indInfo_t;
typedef struct {indInfo_t indInfo;} message_t;
typedef struct {
    message_t *msg;
    struct {u8 cmd,seqNum; u16 manufCode; struct {struct {u8 dir;} bf;} frmCtrl;} hdr;
    void *attrCmd;
} zclIncoming_t;
typedef struct {u8 dstEp,dstAddrMode,txOptions;u16 profileId;struct {u16 shortAddr;} dstAddr;} epInfo_t;
static int alloc_fail, parse_fail, send_fail, parse_frees, rsp_frees, callbacks, applied;
static void *parsed;
static zclWriteCmd_t *zcl_parseInWriteCmd(zclIncoming_t *cmd) {
    if (parse_fail) return NULL;
    zclWriteCmd_t *p = calloc(1, sizeof(*p));
    p->numAttr = 1; p->attrList[0].attrID = 0xff00; p->attrList[0].value = 1;
    parsed = p; return p;
}
static void *ev_buf_allocate(u16 n) {return alloc_fail ? NULL : calloc(1,n);}
static void ev_buf_free(void *p) {if (p == parsed) parse_frees++; else rsp_frees++; free(p);}
static u8 zcl_attrWrite(u8 ep,u16 cluster,zclWriteRec_t *rec,bool write) {
    if (write) applied++;
    return ZCL_STA_SUCCESS;
}
static u8 zcl_writeRsp(u8 ep,epInfo_t *dst,u16 cluster,u16 mfr,bool specific,u8 dir,u8 seq,zclWriteRspCmd_t *rsp) {
    return send_fail ? ZCL_STA_INSUFFICIENT_SPACE : ZCL_STA_SUCCESS;
}
static void hook(zclIncoming_t *cmd) {assert(applied == 1); callbacks++;}
static struct {void (*hookFn)(zclIncoming_t *);} zcl_vars = {hook};
'''
    main = r'''
int main(void) {
    for (int kind=0; kind<3; kind++) {
        for (int failure=0; failure<4; failure++) {
            alloc_fail = failure == 1; parse_fail = failure == 2; send_fail = failure == 3;
            parse_frees = rsp_frees = callbacks = applied = 0; parsed = NULL;
            message_t msg = {0};
            zclIncoming_t inMsg = {.msg=&msg};
            inMsg.hdr.cmd = kind==0 ? ZCL_CMD_WRITE : kind==1 ? ZCL_CMD_WRITE_UNDIVIDED : ZCL_CMD_WRITE_NO_RSP;
            u8 status = kind==1 ? zcl_writeUndividedHandler(&inMsg) : zcl_writeHandler(&inMsg);
            bool rejected = parse_fail || (alloc_fail && kind!=2);
            assert(applied == (rejected ? 0 : 1));
            if (rejected) {
                assert(status == ZCL_STA_INSUFFICIENT_SPACE);
                assert(inMsg.attrCmd == NULL);
            }
            bool toAppFlg = true;
''' + dispatch + r'''
            assert(callbacks == (rejected ? 0 : 1));
            assert(parse_frees == (parse_fail ? 0 : 1));
            assert(rsp_frees == (!parse_fail && !alloc_fail && kind!=2 ? 1 : 0));
        }
    }
    return 0;
}
'''
    c = tmp_path / 'sdk_write.c'
    c.write_text(shim + '\n'.join(handlers) + main)
    binary = tmp_path / 'sdk_write'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-parameter',
                    str(c), '-o', str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)

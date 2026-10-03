from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "src" / "telink" / "patch_sdk"


def test_zdp_override_registers_router_mgmt_rtg():
    sdk_mk = (ROOT / "src/telink/sdk.mk").read_text()
    makefile = (ROOT / "src/telink/Makefile").read_text()
    zdp = (PATCH / "zdp.c").read_text()

    assert "$(SDK_PATH)/zigbee/zdo/zdp.c" not in sdk_mk
    assert "patch_sdk/zdp.c" in makefile
    assert "patch_sdk/zdo_mgmt_rtg.c" in makefile
    assert re.search(r"#ifdef\s+ZB_ROUTER_ROLE(?:(?!#endif).)*MGMT_RTG_REQ_CLID(?:(?!#endif).)*zdo_mgmtRtgIndicate(?:(?!#endif).)*#endif", zdp, re.S)


def test_handler_is_read_only_and_paged():
    handler = (PATCH / "zdo_mgmt_rtg.c").read_text()
    assert "MGMT_RTG_RSP_CLID" in handler
    assert "ROUTING_TABLE_SIZE" in handler
    assert "g_routingTab[i]" in handler
    assert "mgmt_rtg_page_count" in handler
    for forbidden in ("entry- =", "entry- =", "entry- =", "g_routingTab[i] ="):
        assert forbidden not in handler


def test_codec_wire_layout_and_pagination(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc")
    assert cc is not None, "host C compiler is required for codec regression test"

    test_c = tmp_path / "test_mgmt_rtg_codec.c"
    exe = tmp_path / "test_mgmt_rtg_codec"
    test_c.write_text(
        r'''#include <assert.h>
#include <stdint.h>
#include "mgmt_rtg_codec.h"
int main(void) {
    uint8_t out[MGMT_RTG_DESCRIPTOR_SIZE] = {0};
    uint8_t flags = mgmt_rtg_encode_status(4, true, true, true);
    assert(flags == 0x3c);
    mgmt_rtg_encode_descriptor(out, 0x1234, flags, 0xabcd);
    assert(out[0] == 0x34 && out[1] == 0x12 && out[2] == 0x3c);
    assert(out[3] == 0xcd && out[4] == 0xab);
    assert(mgmt_rtg_page_count(20, 0) == 9);
    assert(mgmt_rtg_page_count(20, 9) == 9);
    assert(mgmt_rtg_page_count(20, 18) == 2);
    assert(mgmt_rtg_page_count(20, 20) == 0);
    return 0;
}
'''
    )
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(test_c),
            str(PATCH / "mgmt_rtg_codec.c"),
            "-I",
            str(PATCH),
            "-o",
            str(exe),
        ],
        check=True,
    )
    subprocess.run([str(exe)], check=True)
def test_full_router_handler_sparse_pages_and_buffer_ownership(tmp_path):
    shim = r'''
#ifndef TEST_ZB_COMMON
#define TEST_ZB_COMMON
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
#define NWK_ROUTE_STATE_ACTIVE 0
#define NWK_ROUTE_STATE_DISCOVERY_INACTIVE 3
#define MGMT_RTG_RSP_CLID 0x8032
#define ZDO_SUCCESS 0
#define SHORT_ADDR_MODE 2
typedef struct {u16_placeholder;} unused_placeholder;
typedef struct {uint16_t dstAddr,nextHopAddr;uint8_t status,noRouteCache,manyToOne,routeRecordRequired;} nwk_routingTabEntry_t;
static nwk_routingTabEntry_t g_routingTab[48];
static uint16_t ROUTING_TABLE_SIZE=48;
typedef struct {uint8_t *asdu;uint16_t asduLength,src_short_addr;} aps_data_ind_t;
typedef struct {uint8_t storage[256];} zb_buf_t;
typedef struct {uint8_t *zdu;uint16_t cluster_id;uint8_t zduLen;void *buff_addr;uint8_t dst_addr_mode;uint16_t dst_nwk_addr;void *zdoRspReceivedIndCb;} zdo_zdp_req_t;
static unsigned sends,frees,allocs;
static uint8_t wire[64],wire_len,send_status;
static uint16_t wire_dst;
#define TL_SETSTRUCTCONTENT(p,v) memset(&(p),(v),sizeof(p))
static void *initial_alloc(zb_buf_t *p,unsigned len) {
    assert(len<=50); allocs++;
    memset(p,0xaa,sizeof(*p)); /* metadata must be consumed before reuse */
    return p->storage;
}
#define TL_BUF_INITIAL_ALLOC(p,n,ptr,type) ((ptr)=(type)initial_alloc(p,n))
static uint8_t zdo_send_req(zdo_zdp_req_t *req) {
    assert(req->cluster_id==MGMT_RTG_RSP_CLID);
    assert(req->dst_addr_mode==SHORT_ADDR_MODE && req->zdoRspReceivedIndCb==NULL);
    assert(req->zduLen<=50);
    sends++; wire_len=req->zduLen;wire_dst=req->dst_nwk_addr;
    memcpy(wire,req->zdu,req->zduLen);
    return send_status; /* native function copies data; caller releases request */
}
static void zb_buf_free(zb_buf_t *p) {(void)p;frees++;}
#endif
'''.replace('typedef struct {u16_placeholder;} unused_placeholder;\n','')
    (tmp_path/'zb_common.h').write_text(shim)
    code = '#include "' + (PATCH/'zdo_mgmt_rtg.c').as_posix() + '"\n' + r'''
static void request(unsigned start,unsigned length,bool null) {
    zb_buf_t buf={0}; uint8_t payload[]={0x71,(uint8_t)start};
    aps_data_ind_t *ind=(void*)&buf;
    ind->asdu=null ? NULL : payload;ind->asduLength=length;ind->src_short_addr=0x4567;
    unsigned old_free=frees,old_send=sends,old_alloc=allocs;
    nwk_routingTabEntry_t original[48];memcpy(original,g_routingTab,sizeof(original));
    zdo_mgmtRtgIndicate(&buf);
    assert(frees==old_free+1);
    assert(!memcmp(original,g_routingTab,sizeof(original)));
    if(null || length<2) {assert(sends==old_send && allocs==old_alloc);return;}
    assert(sends==old_send+1 && wire_dst==0x4567 && wire[0]==0x71 && wire[1]==0);
    assert(wire[3]==start);
}
int main(void) {
    for(unsigned i=0;i<48;i++) {
        g_routingTab[i].dstAddr=g_routingTab[i].nextHopAddr=0xfffe;
        g_routingTab[i].status=NWK_ROUTE_STATE_DISCOVERY_INACTIVE;
    }
    request(0,0,false);request(0,1,false);request(0,2,true);
    request(0,2,false);assert(wire[2]==0 && wire[4]==0 && wire_len==5);
    for(unsigned i=0;i<20;i++) {
        unsigned slot=i*2+1;
        g_routingTab[slot]=(nwk_routingTabEntry_t){0x1200+i,0x3400+i,i%5,1,1,1};
    }
    assert(telink_route_entry_count(false)==20 && telink_route_entry_count(true)==4);
    request(0,2,false);assert(wire[2]==20 && wire[4]==9 && wire_len==50);
    for(unsigned i=0;i<9;i++) {
        uint8_t *p=wire+5+i*5;
        assert(p[0]==i && p[1]==0x12 && p[2]==((i%5)|0x38));
        assert(p[3]==i && p[4]==0x34);
    }
    request(9,2,false);assert(wire[4]==9 && wire[5]==9 && wire_len==50);
    request(18,2,false);assert(wire[4]==2 && wire[5]==18 && wire_len==15);
    request(20,2,false);assert(wire[4]==0 && wire_len==5);
    request(255,2,false);assert(wire[4]==0 && wire_len==5);
    send_status=1;request(0,2,false); /* failure still frees exactly once */
    return 0;
}
'''
    c=tmp_path/'handler.c';c.write_text(code)
    exe=tmp_path/'handler'
    subprocess.run(['cc','-std=c99','-Wall','-Wextra','-Werror','-DZB_ROUTER_ROLE=1',
                    '-I',str(tmp_path),'-I',str(PATCH),str(c),
                    str(PATCH/'mgmt_rtg_codec.c'),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)

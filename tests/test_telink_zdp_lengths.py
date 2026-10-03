"""Execute the real ZDP dispatch functions with bounded receive-buffer fakes."""
from pathlib import Path
import re
import subprocess
from tests.test_telink_counter_persistence import sdk_file

ROOT = Path(__file__).resolve().parents[1]


def extract(source, name):
    match = re.search(r'(?:static )?(?:bool|void) ' + name + r'\([^;]+?\)\s*\{', source)
    assert match, name
    begin = source.index('{', match.start()) + 1
    pos, depth = begin, 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


def test_zdp_native_dispatch_length_counts_and_buffer_reuse(tmp_path):
    source = (ROOT/'src/telink/patch_sdk/zdp.c').read_text()
    header = sdk_file('zigbee/zdo/zdp.h')
    ids = '\n'.join('#define ' + name + ' ' + value for name, value in
                    re.findall(r'(\w+_CLID)\s*=\s*(0x[0-9A-Fa-f]+)', header))
    functions = '\n'.join(extract(source, name) for name in (
        'zdp_clusterListsValid', 'zdp_requestLengthValid',
        'zdp_serverCmdHandler', 'zdp_clientCmdHandler'))
    shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define ZB_ROUTER_ROLE 1
#define MAX_REQUESTED_CLUSTER_NUMBER 8
#define SHORT_GROUPADDR_NODSTENDPOINT 1
#define LONG_EXADDR_DSTENDPOINT 3
#define TRUE 1
#define ZDO_SUCCESS 0
#define ZDO_NOT_SUPPORTED 0x84
#define ZDO_NOT_AUTHORIZED 0x8d
#define APS_SHORT_DSTADDR_WITHEP 2
#define SHORT_ADDR_MODE 2
#define SECURITY_IN_APSLAYER 1
#define ZB_NWK_IS_ADDRESS_BROADCAST(a) ((a)>=0xfff8)
typedef unsigned zdo_status_t;
typedef struct {u8 *asdu;u16 asduLength,src_short_addr,cluster_id,dst_addr;u8 dst_addr_mode,security_status;} aps_data_ind_t;
typedef struct {u16 src_addr,clusterId;u8 seq_num,status;u16 length;u8 *zpdu;} zdo_zdpDataInd_t;
typedef struct {u8 *zdu;u16 cluster_id;u8 zduLen;void *buff_addr;u8 dst_addr_mode;u16 dst_nwk_addr;void *zdoRspReceivedIndCb;} zdo_zdp_req_t;
typedef struct {u8 bytes[256];} zb_buf_t;
typedef struct {u16 clusterId,restricted;void (*func)(void *);} zdp_funcList_t;
static struct {u8 aps_zdo_restricted_mode;} aps;
static struct {u16 managerAddr;} nwk;
#define APS_IB() aps
#define NWK_NIB() nwk
#define TL_SETSTRUCTCONTENT(p,v) memset(&(p),(v),sizeof(p))
static unsigned frees,dispatches,callbacks,parent_notifications,addr_notifications,sends;
static u8 wire[2];
static void zb_buf_free(zb_buf_t *p) {(void)p;frees++;}
static void native_request(void *p) {dispatches++;zb_buf_free(p);}
static const zdp_funcList_t g_zdpClientFunc[]={{MGMT_LQI_REQ_CLID,0,native_request},{BIND_REQ_CLID,1,native_request}};
static void zdo_parentAnnounceNotify(void *p) {(void)p;parent_notifications++;}
static void zdo_remoteAddrNotify(void *p) {(void)p;addr_notifications++;}
static void zdp_cb_process(u8 seq,void *p) {
    zdo_zdpDataInd_t *ind=p;assert(seq==0x42 && ind->src_addr==0x2345);callbacks++;
}
static void *initial_alloc(zb_buf_t *p,unsigned n) {assert(n==2);memset(p,0xaa,sizeof(*p));return p->bytes;}
#define TL_BUF_INITIAL_ALLOC(p,n,ptr,type) ((ptr)=(type)initial_alloc(p,n))
static void zdo_send_req(zdo_zdp_req_t *r) {
    assert(r->cluster_id==0x8777 && r->dst_nwk_addr==0x2345 && r->zduLen==2);
    memcpy(wire,r->zdu,2);sends++;
}
'''
    checks = r'''
static zb_buf_t buf;static u8 payload[100];
static aps_data_ind_t *prepare(u16 cid,unsigned n,bool null) {
    memset(&buf,0,sizeof(buf));memset(payload,0,sizeof(payload));payload[0]=0x42;
    aps_data_ind_t *p=(void*)&buf;p->cluster_id=cid;p->asdu=null ? NULL : payload;
    p->asduLength=n;p->src_short_addr=0x2345;return p;
}
static void fixed(u16 cid,unsigned minimum) {
    for(unsigned n=0;n<minimum;n++) assert(!zdp_requestLengthValid(prepare(cid,n,false)));
    assert(zdp_requestLengthValid(prepare(cid,minimum,false)));
    assert(!zdp_requestLengthValid(prepare(cid,100,true)));
}
int main(void) {
    fixed(NWK_ADDR_REQ_CLID,11);fixed(IEEE_ADDR_REQ_CLID,5);
    fixed(NODE_DESC_REQ_CLID,3);fixed(POWER_DESC_REQ_CLID,3);
    fixed(ACTIVE_EP_REQ_CLID,3);fixed(SYSTEM_SERVER_DISCOVERY_REQ_CLID,3);
    fixed(SIMPLE_DESC_REQ_CLID,4);fixed(DEVICE_ANNCE_CLID,12);
    fixed(MGMT_LQI_REQ_CLID,2);fixed(MGMT_RTG_REQ_CLID,2);
    fixed(MGMT_BIND_REQ_CLID,2);fixed(MGMT_LEAVE_REQ_CLID,10);
    fixed(MGMT_PERMIT_JOINING_REQ_CLID,3);fixed(PARENT_ANNCE_CLID,2);
    aps_data_ind_t *p=prepare(PARENT_ANNCE_CLID,9,false);payload[1]=1;
    assert(!zdp_requestLengthValid(p));p->asduLength=10;assert(zdp_requestLengthValid(p));
    for(unsigned k=0;k<2;k++) {
        unsigned count_offset=k ? 14:5;
        p=prepare(k ? END_DEVICE_BIND_REQ_CLID : MATCH_DESC_REQ_CLID,count_offset+2,false);
        assert(zdp_requestLengthValid(p));
        payload[count_offset]=9;assert(!zdp_requestLengthValid(p));
        payload[count_offset]=2;assert(!zdp_requestLengthValid(p));
        p->asduLength=count_offset+9;payload[count_offset+5]=1;assert(zdp_requestLengthValid(p));
        payload[count_offset+5]=2;assert(!zdp_requestLengthValid(p));
        payload[count_offset+5]=9;assert(!zdp_requestLengthValid(p));
    }
    for(unsigned k=0;k<2;k++) {
        p=prepare(k ? BIND_REQ_CLID : UNBIND_REQ_CLID,15,false);payload[12]=1;
        assert(zdp_requestLengthValid(p));p->asduLength=14;assert(!zdp_requestLengthValid(p));
        payload[12]=3;p->asduLength=22;assert(zdp_requestLengthValid(p));
        p->asduLength=21;assert(!zdp_requestLengthValid(p));
        payload[12]=2;p->asduLength=100;assert(!zdp_requestLengthValid(p));
    }
    p=prepare(MGMT_NWK_UPDATE_REQ_CLID,7,false);
    for(unsigned d=0;d<=5;d++) {payload[5]=d;assert(zdp_requestLengthValid(p));}
    payload[5]=0xfe;assert(zdp_requestLengthValid(p));
    payload[5]=0xff;assert(!zdp_requestLengthValid(p));
    p->asduLength=9;assert(zdp_requestLengthValid(p));
    payload[5]=6;assert(!zdp_requestLengthValid(p));
    for(unsigned cid=0;cid<3;cid++) {
        const u16 ids[]={NWK_ADDR_RSP_CLID,PARENT_ANNCE_RSP_CLID,0x8777};
        for(unsigned n=0;n<2;n++) {prepare(ids[cid],n,false);zdp_serverCmdHandler(&buf);}
        prepare(ids[cid],100,true);zdp_serverCmdHandler(&buf);
    }
    assert(frees==9 && callbacks==0 && parent_notifications==0 && addr_notifications==0);
    prepare(NWK_ADDR_RSP_CLID,2,false);payload[1]=0x84;zdp_serverCmdHandler(&buf);
    assert(callbacks==1 && addr_notifications==0);
    prepare(NWK_ADDR_RSP_CLID,11,false);zdp_serverCmdHandler(&buf);
    assert(callbacks==1 && addr_notifications==0);
    prepare(NWK_ADDR_RSP_CLID,12,false);zdp_serverCmdHandler(&buf);
    assert(callbacks==2 && addr_notifications==1);
    prepare(PARENT_ANNCE_RSP_CLID,10,false);payload[2]=1;zdp_serverCmdHandler(&buf);
    assert(parent_notifications==0);
    prepare(PARENT_ANNCE_RSP_CLID,11,false);payload[2]=1;zdp_serverCmdHandler(&buf);
    assert(parent_notifications==1);
    unsigned f=frees;
    prepare(MGMT_LQI_REQ_CLID,1,false);zdp_clientCmdHandler(&buf);assert(frees==f+1 && dispatches==0);
    prepare(MGMT_LQI_REQ_CLID,2,false);zdp_clientCmdHandler(&buf);assert(frees==f+2 && dispatches==1);
    prepare(0x777,1,false);zdp_clientCmdHandler(&buf);
    assert(sends==1 && wire[0]==0x42 && wire[1]==0x84 && frees==f+3);
    prepare(0x777,1,false);((aps_data_ind_t*)&buf)->dst_addr_mode=2;
    ((aps_data_ind_t*)&buf)->dst_addr=0xffff;zdp_clientCmdHandler(&buf);
    assert(sends==1 && frees==f+4);
    return 0;
}
'''
    c=tmp_path/'zdp.c';c.write_text(ids+shim+functions+checks)
    exe=tmp_path/'zdp'
    subprocess.run(['cc','-std=c99','-Wall','-Wextra','-Werror',str(c),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)

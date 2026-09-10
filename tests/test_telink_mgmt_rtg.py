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

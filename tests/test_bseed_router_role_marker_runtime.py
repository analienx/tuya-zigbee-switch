"""Run the actual Router role-marker handling against NVM fault injection."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def marker_binary(tmp_path_factory):
    source = (ROOT / 'src/app.c').read_text()
    start = 'void process_device_type_change() {'
    role = start + source.split('#else\n' + start, 1)[1].split('\n#endif', 1)[0]
    harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hal/nvm.h"
#include "device_config/device_type.h"
#include "device_config/nvm_items.h"
static enum device_type_t stored_role = DEVICE_TYPE_END_DEVICE;
static hal_nvm_status_t read_status = HAL_NVM_SUCCESS;
static unsigned writes, network_resets;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_DEVICE_TYPE && size == sizeof(stored_role));
    if (read_status == HAL_NVM_SUCCESS) memcpy(data, &stored_role, size);
    return read_status;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_DEVICE_TYPE && size == sizeof(stored_role));
    writes++;
    memcpy(&stored_role, data, size);
    return HAL_NVM_SUCCESS;
}
void schedule_network_reset(uint16_t ms) { assert(ms == 1); network_resets++; }
'''
    cases = r'''
int main(int argc, char **argv) {
    assert(argc == 2 && CURRENT_DEVICE_TYPE == DEVICE_TYPE_ROUTER);
    if (!strcmp(argv[1], "unreadable-marker")) {
        /* A pending Client->Router transition must survive a read fault. */
        read_status = HAL_NVM_ERROR;
        process_device_type_change();
        assert(writes == 0 && network_resets == 0);
        assert(stored_role == DEVICE_TYPE_END_DEVICE);
        read_status = HAL_NVM_SUCCESS;
        process_device_type_change();
        assert(writes == 1 && network_resets == 1);
        assert(stored_role == DEVICE_TYPE_ROUTER);
    } else if (!strcmp(argv[1], "missing-marker")) {
        read_status = HAL_NVM_NOT_FOUND;
        process_device_type_change();
        assert(writes == 1 && network_resets == 0);
        assert(stored_role == DEVICE_TYPE_ROUTER);
    } else if (!strcmp(argv[1], "client-to-router")) {
        process_device_type_change();
        assert(writes == 1 && network_resets == 1);
        assert(stored_role == DEVICE_TYPE_ROUTER);
    } else if (!strcmp(argv[1], "same-role")) {
        stored_role = DEVICE_TYPE_ROUTER;
        process_device_type_change();
        assert(writes == 0 && network_resets == 0);
    } else assert(0);
    return 0;
}
'''
    binary = tmp_path_factory.mktemp('router-marker') / 'router-marker'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Werror', '-DHAL_STUB=1',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=harness + role + cases, text=True,
                   capture_output=True, check=True)
    return binary


@pytest.mark.parametrize('case', [
    'unreadable-marker', 'missing-marker', 'client-to-router', 'same-role',
])
def test_router_role_marker_contract(marker_binary, case):
    subprocess.run([str(marker_binary), case], check=True, timeout=10)

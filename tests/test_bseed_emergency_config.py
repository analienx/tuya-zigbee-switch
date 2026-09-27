"""A broken compiled board default enters a no-GPIO state, without changing NVM."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('board', ['-DBSEED_PM_B28WRPVX=1', '-DDEVICE_CONFIG_GUARD_BSEED_TS011F_NONPM=1'])
def test_emergency_state_is_exact_minimal_and_not_a_persistable_board_config(tmp_path, board):
    code = r'''
#include <assert.h>
#include "device_config/config_nv.c"
static unsigned writes;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    writes++;
    return HAL_NVM_SUCCESS;
}
int main(void) {
    load_config_copy("unsafe;board;RB5;");
    assert(device_config_prepare_for_parse());
    assert(device_config_str.size == strlen("unknown;TS0012-CUSTOM;"));
    assert(memcmp(device_config_str.data, "unknown;TS0012-CUSTOM;", device_config_str.size) == 0);
    assert(emergency_config_is_minimal(device_config_str.data, device_config_str.size));
    /* Recovery cannot be extended with an output pin or persisted as approved. */
    assert(!emergency_config_is_minimal((const uint8_t *)"unknown;TS0012-CUSTOM;RB5;", 25));
    assert(!device_config_is_valid(device_config_str.data, device_config_str.size));
    device_config_write_to_nv();
    assert(writes == 0);
    return 0;
}
'''
    binary = tmp_path / 'minimal-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', board,
                    '-DDEFAULT_CONFIG=bad;compiled;RB5;', '-ffunction-sections', '-fdata-sections',
                    '-Wl,--gc-sections', '-I', str(ROOT / 'src'), str(ROOT / 'src/stub/hal/gpio.c'), '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, capture_output=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)

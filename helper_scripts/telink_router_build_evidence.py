"""Read native Router ELF and its actual preprocessor profile on hosted CI."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile

CAPACITIES = ('TL_ZB_NEIGHBOR_TABLE_SIZE', 'TL_ZB_CHILD_TABLE_SIZE',
              'ROUTING_TABLE_SIZE', 'ZB_BUF_POOL_SIZE', 'MAC_TX_QUEUE_SIZE',
              'APS_TX_CACHE_TABLE_SIZE', 'NWK_ROUTE_RECORD_TABLE_SIZE')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--macros', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    macros = dict(re.findall(r'^#define (\w+) (.+)$', args.macros.read_text(), re.M))
    if macros.get('ZB_ROUTER_ROLE') != '1':
        raise ValueError('native profile is not a Router')
    if macros.get('PM_ENABLE', '0') != '0' or macros.get('ZB_MAC_RX_ON_WHEN_IDLE') != '1':
        raise ValueError('Router power/Rx-on invariant violated')
    with args.elf.open('rb') as handle:
        elf = ELFFile(handle)
        table = elf.get_section_by_name('.symtab')
        symbols = {s.name: s for s in table.iter_symbols()}
        capacities = {}
        for name in CAPACITIES:
            symbol = symbols[name]
            section = elf.get_section(symbol['st_shndx'])
            offset = symbol['st_value'] - section['sh_addr']
            data = section.data()[offset:offset + symbol['st_size']]
            if symbol['st_size'] not in (1, 2) or len(data) != symbol['st_size']:
                raise ValueError('unexpected native capacity layout: ' + name)
            capacities[name] = int.from_bytes(data, 'little')
        bounds = {name: symbols[name]['st_value'] for name in
                  ('_ram_end_', '_stack_end_', '_ramcode_size_align_256_')}
        if bounds['_ram_end_'] >= bounds['_stack_end_']:
            raise ValueError('native linker RAM guard failed')
        hook = symbols['hal_telink_flash_complete']
        hook_section = elf.get_section(hook['st_shndx']).name
        if hook_section != '.ram_code':
            raise ValueError('flash instrumentation hook must execute from RAM')
        data_section = elf.get_section_by_name('.data')
        bss = elf.get_section_by_name('.bss')
        report = {
            'schema': 1,
            'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'power': {'ZB_ROUTER_ROLE': 1, 'PM_ENABLE': 0, 'ZB_MAC_RX_ON_WHEN_IDLE': 1},
            'nativeInitializedCapacities': capacities,
            'ram': {'dataBytes': data_section['sh_size'], 'bssBytes': bss['sh_size'],
                    **bounds,
                    'headroomBeyondLinkerReservedStackBytes': bounds['_stack_end_'] - bounds['_ram_end_'],
                    'linkerMinimumStackBytes': 2048,
                    'note': 'Static linker margin; runtime stack peak and burst admission need hardware.'},
            'flashHookSection': hook_section,
        }
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

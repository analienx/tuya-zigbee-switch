"""Narrow Telink 3.7.2.0 write-allocation fix, applied to a build-local copy.

The SDK dispatches attrCmd to the application even when a handler fails. If
response allocation failed before any writes, release the parsed command and
clear that dispatch pointer. Do not suppress callbacks on later send failures:
those happen after attributes have already been applied.
"""
import argparse
from pathlib import Path


def patch_source(source):
    source = source.replace('\r\n', '\n')
    for name, indent in (('zcl_writeHandler', '        '),
                         ('zcl_writeUndividedHandler', '    ')):
        signature = '_CODE_ZCL_ status_t ' + name + '(zclIncoming_t *pCmd)\n{'
        if source.count(signature) != 1:
            raise ValueError('Unexpected Telink SDK write handler: ' + name)
        start = source.index(signature)
        end = source.index('\n}', start) + 2
        body = source[start:end]
        old = (indent + 'if (!pWriteRspCmd) {\n' +
               indent + '    return ZCL_STA_INSUFFICIENT_SPACE;\n' + indent + '}')
        new = (indent + 'if (!pWriteRspCmd) {\n' +
               indent + '    ev_buf_free((void *)pWriteCmd);\n' +
               indent + '    pCmd->attrCmd = NULL;\n' +
               indent + '    return ZCL_STA_INSUFFICIENT_SPACE;\n' + indent + '}')
        if body.count(old) != 1:
            raise ValueError('Unexpected Telink SDK allocation path: ' + name)
        source = source[:start] + body.replace(old, new) + source[end:]
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Never modify the cached SDK source')
    result = patch_source(args.source.read_text(encoding='utf8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding='utf8')


if __name__ == '__main__':
    main()

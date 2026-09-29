"""Synthetic OTA fixture with real Telink framing and CRC, never deployable."""
import binascii
import struct


def image_for(candidate, firmware_size=14000):
    fw = bytearray(firmware_size)
    struct.pack_into('<I', fw, 2, candidate['version'])
    fw[6:12] = b'\x5d\x02KNLT'
    struct.pack_into('<I', fw, 24, len(fw))
    build = candidate['build'].encode()
    fw[39] = len(build)
    fw[40:40+len(build)] = build
    struct.pack_into('<I', fw, len(fw)-4, binascii.crc32(fw[:-4]) ^ 0xffffffff)
    payload = struct.pack('<HI', 0, len(fw)) + fw
    return struct.pack('<I5HIH32sI', 0x0beef11e, 0x100, 56, 0, 4417,
                       candidate['type'], candidate['version'], 2, b'', 56+len(payload)) + payload

"""Hosted tests compile the same native modules copied into the TC32 image."""
import ctypes as C
import datetime as dt
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'native.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-std=c99', '-Os', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-DNC_HOST', '-I', str(ROOT / 'native'),
                        str(ROOT / 'native/nc_all.c'), '-o', str(lib)], check=True)
        cls.fw = C.CDLL(str(lib))
        cls.fw.nc_configure.argtypes = [C.c_void_p, C.c_uint]
        cls.fw.nc_configure.restype = C.c_uint8
        cls.fw.nc_time_response.argtypes = [C.c_uint8, C.c_void_p, C.c_uint]
        cls.fw.nc_receive.argtypes = [C.c_uint16, C.c_uint8, C.c_uint16, C.c_int, C.c_int,
                                      C.c_void_p, C.c_uint16, C.c_void_p, C.c_uint]
        cls.fw.host_time.argtypes = cls.fw.host_advance.argtypes = [C.c_uint32]

    def setUp(self):
        self.fw.host_reset()

    def config(self, flags=0, start=1080, end=420, level=152, fade_in=15, fade_out=30):
        raw = struct.pack('<4B4H', 1, flags, level, 0, start, end, fade_in, fade_out)
        return self.fw.nc_configure(raw, len(raw))

    def frame(self, i):
        out = C.create_string_buffer(32)
        n = self.fw.host_frame(i, out)
        return out.raw[:n]

    def event(self, name):
        getattr(self.fw, name)()
        self.fw.nc_service()

    def advance(self, ms):
        self.fw.host_advance(ms)
        self.fw.nc_service()

    def ack(self, i, status=0):
        self.fw.host_ack(i, status)
        self.fw.nc_service()

    def diag(self):
        out = C.create_string_buffer(12)
        self.fw.host_diag(out)
        return out.raw

    @staticmethod
    def clock_payload(utc=849459600, tz=3600, status=13, dst=None, valid=3600):
        dst = dst or (0xffffffff, 0xffffffff, 0)
        values = (utc, status, tz, *dst, utc + valid)
        ids = (0, 1, 2, 3, 4, 5, 9)
        types = (0xe2, 0x18, 0x2b, 0x23, 0x23, 0x2b, 0xe2)
        return b''.join(struct.pack('<HB', i, 0) + bytes([t]) +
                        (bytes([v]) if t == 0x18 else struct.pack('<I', v & 0xffffffff))
                        for i, t, v in zip(ids, types, values))

    def receive_clock(self, payload=None, request=0, source=0, ep=1, profile=0x104,
                      unicast=1, secured=1, sequence=None):
        sequence = self.frame(request)[1] if sequence is None else sequence
        self.fw.host_ack(request, 0)
        wire = bytes([0x18, sequence, 1]) + (payload if payload is not None else self.clock_payload())
        return self.fw.nc_receive(source, ep, profile, unicast, secured, None, 10, wire, len(wire))

    def test_native_fade_and_legacy_frames(self):
        self.assertEqual(self.config(), 0)
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion')
        self.assertEqual(self.frame(0), bytes.fromhex('110004980f00'))
        self.fw.host_ack(0, 0)
        self.event('nc_absence')
        self.assertEqual(self.frame(1), bytes.fromhex('110104001e00'))
        self.fw.host_reset()
        self.fw.host_bind(0, 123, 1, 1, 0)
        self.event('nc_motion')
        self.assertEqual(self.frame(0), bytes.fromhex('010001'))

    def test_level_requires_committed_config_and_deduplicates(self):
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.fw.host_bind(1, 123, 1, 1, 0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_cluster(0), 6)
        self.assertEqual(self.fw.host_count(), 1)

    def test_config_validation_and_idempotence(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.fw.host_writes(), 1)
        self.assertEqual(self.config(fade_out=1201), 0x87)
        self.assertEqual(self.config(flags=1, start=0, end=0), 0x87)
        self.assertEqual(self.config(level=255), 0x87)
        self.fw.host_lux_target(65535)
        self.assertEqual(self.config(flags=2), 0x87)

    def test_bright_motion_is_reportable_but_does_not_send(self):
        self.config(flags=2)
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.fw.host_dark(0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 0)
        self.assertEqual(self.fw.host_reason(), 5)
        self.assertEqual(self.fw.host_lux_reads(), 1)

    def test_actual_runtime_fits_budget(self):
        self.assertLessEqual(self.fw.host_size() + 16, 512)

    def test_lost_off_bright_motion_rearms_off_at_absence(self):
        self.config(flags=2)
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.ack(0)
        self.event('nc_absence'); self.ack(1, 0xa7)
        self.advance(2000); self.ack(2, 0xa7)
        self.advance(10000); self.ack(3, 0xa7)
        self.assertTrue(self.diag()[7] & 2)
        self.fw.host_dark(0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 4)
        self.event('nc_absence')
        self.assertEqual(self.frame(4)[2:4], bytes([4, 0]))

    def test_motion_serializes_after_old_off_and_ignores_report_confirm(self):
        self.config(); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion')
        self.fw.host_wrong_ack(0); self.fw.nc_service()
        self.assertEqual(self.fw.host_inflight(), 1)
        self.ack(0); self.event('nc_absence')
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 2)
        self.ack(1, 0xa7)
        self.assertEqual(self.frame(2)[3], 152)
        self.fw.host_reject(1, 0x87)
        self.ack(2)
        self.advance(30000)
        self.assertEqual(self.fw.host_count(), 3)

    def test_group_level_deduplicates_and_repeats_once(self):
        self.config(fade_in=50)
        self.fw.host_bind(0, 123, 0, 1, 1)
        self.fw.host_bind(1, 123, 0, 2, 1)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 1)
        self.assertEqual(self.fw.host_cluster(0), 8)
        self.assertEqual(self.fw.host_ack_option(0), 0)
        self.ack(0); self.advance(2000)
        self.assertEqual(int.from_bytes(self.frame(1)[4:], 'little'), 30)
        self.ack(1)
        self.advance(61000); self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 2)

    def test_group_off_first_hop_success_is_not_later_reoff(self):
        self.config(flags=2)
        self.fw.host_bind(0, 123, 0, 2, 1)
        self.event('nc_motion'); self.ack(0); self.advance(2000); self.ack(1)
        self.event('nc_absence'); self.ack(2)
        self.advance(2000); self.ack(3, 0xa7)
        self.advance(2000); self.ack(4, 0xa7)
        self.advance(10000); self.ack(5, 0xa7)
        self.assertFalse(self.diag()[7] & 2)
        self.fw.host_dark(0)
        self.event('nc_motion'); self.event('nc_absence')
        self.assertEqual(self.fw.host_count(), 6)

    def test_stale_on_is_not_replayed_after_network_returns(self):
        self.config(); self.fw.host_bind(0, 123, 1, 2, 0)
        self.fw.host_joined(0); self.event('nc_motion')
        self.advance(61000); self.fw.host_joined(1); self.fw.nc_service()
        self.assertEqual(self.fw.host_count(), 0)
        self.assertTrue(self.diag()[7] & 4)
        self.event('nc_absence')
        self.assertEqual(self.frame(0)[3], 0)

    def test_maximum_two_inflight_and_binding_removal(self):
        self.config()
        for i in range(3): self.fw.host_bind(i, 100+i, 1, 2, 0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_inflight(), 2)
        self.assertEqual(self.fw.host_count(), 2)
        self.fw.host_bind(0, 0, 0, 0, 0)
        self.ack(0)
        self.assertEqual(self.fw.host_count(), 3)
        self.assertEqual(self.fw.host_inflight(), 2)

    def test_legacy_unknown_threshold_migrates_to_lux_disabled(self):
        self.fw.host_lux_target(65535); self.fw.nc_init()
        self.fw.host_bind(0, 123, 1, 1, 0); self.fw.host_dark(0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 1)
        self.assertEqual(self.fw.host_lux_reads(), 0)

    def test_nv_and_low_voltage_failures_do_not_commit_ram(self):
        self.fw.host_battery(2199)
        self.assertEqual(self.config(), 1)
        self.assertEqual(self.fw.host_writes(), 0)
        self.fw.host_battery(3000); self.fw.host_nv_fail(1)
        self.assertEqual(self.config(), 0xc0)
        self.assertFalse(self.diag()[7] & 1)

    def test_zcl_error_stays_inflight_until_aps_completion(self):
        self.config(); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.fw.host_reject(0, 0x87)
        self.assertEqual(self.fw.host_inflight(), 1)
        self.ack(0)
        self.assertEqual(self.diag()[6], 0x87)
        self.advance(20000)
        self.assertEqual(self.fw.host_count(), 1)

    def test_master_and_synchronized_time_status(self):
        for status, valid in [(13, True), (2, True), (0, False)]:
            with self.subTest(status=status):
                self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
                self.assertEqual(self.fw.host_fast_polls(), 1)
                self.receive_clock(self.clock_payload(status=status))
                self.assertEqual(self.fw.nc_local_minute() >= 0, valid)

    def test_time_peer_checks_and_no_traffic_when_disabled(self):
        for bad in ({'source':1}, {'ep':2}, {'profile':1}, {'unicast':0},
                    {'secured':0}, {'sequence':99}):
            with self.subTest(bad=bad):
                self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
                self.receive_clock(**bad)
                self.assertEqual(self.fw.nc_local_minute(), -1)
        self.config(flags=0); self.advance(600000)
        count = self.fw.host_count()
        self.advance(600000)
        self.assertEqual(self.fw.host_count(), count)

    def test_all_truncated_time_records_are_rejected(self):
        payload = self.clock_payload()
        for length in range(len(payload)):
            self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
            self.receive_clock(payload[:length])
            self.assertEqual(self.fw.nc_local_minute(), -1, length)

    def test_time_over_millisecond_wrap_and_expiry(self):
        self.fw.host_time(0xfffffff0); self.config(flags=1); self.fw.nc_service()
        self.receive_clock()
        self.assertGreaterEqual(self.fw.nc_local_minute(), 0)
        self.advance(1500)
        self.assertGreaterEqual(self.fw.nc_local_minute(), 0)
        self.advance(3900000)
        self.assertEqual(self.fw.nc_local_minute(), -1)

    def test_invalid_time_types_duplicate_ids_and_dst_partial(self):
        good = self.clock_payload()
        corrupt = [good + good[:8], good + b'\x00', good[:3]+b'\x23'+good[4:],
                   good[:21]+struct.pack('<HB',3,0x86)+good[29:]]
        for payload in corrupt:
            self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
            self.receive_clock(payload)
            self.assertEqual(self.fw.nc_local_minute(), -1)

    def test_pending_motion_is_only_resumed_while_fresh(self):
        self.config(flags=1); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.fw.host_advance(2000)
        self.receive_clock()
        self.fw.nc_service()
        self.assertEqual(self.fw.host_cluster(1), 8)

    def test_late_clock_does_not_replay_old_motion(self):
        self.config(flags=1); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.fw.host_ack(0, 0)
        self.advance(4000); self.advance(30000)
        self.receive_clock(request=1); self.fw.nc_service()
        self.assertEqual(self.fw.host_count(), 2)
        self.assertEqual(self.fw.host_phase(), 0)

    def test_shared_config_wire_vectors(self):
        for vector in json.loads((ROOT / 'vectors.json').read_text())['configurations']:
            self.fw.host_reset()
            raw = bytes.fromhex(vector['hex'])
            self.assertEqual(self.fw.nc_configure(raw, len(raw)), 0)
            actual = C.create_string_buffer(12)
            self.fw.host_config(actual)
            self.assertEqual(actual.raw, raw)

    def test_real_herdsman_frames_dst_and_cache_expiry(self):
        fixture = os.environ.get('HERDSMAN_TIME_VECTORS')
        if not fixture:
            self.fail('Hosted test requires real herdsman vectors, not hand-authored substitutes')
        for vector in json.loads(Path(fixture).read_text()):
            with self.subTest(iso=vector['iso']):
                self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
                raw = bytes.fromhex(vector['hex'])
                self.fw.nc_receive(0, 1, 0x104, 1, 1, None, 10, raw, len(raw))
                self.assertEqual(self.fw.nc_local_minute(), vector['localMinute'])

    def test_saved_lux_condition_does_not_turn_into_legacy_bypass(self):
        self.config(flags=2)
        self.fw.host_lux_target(65535)
        self.fw.nc_init()
        self.fw.host_bind(0, 123, 1, 3, 0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 0)
        self.assertEqual(self.fw.host_reason(), 4)
        self.assertTrue(self.diag()[7] & 1)

    def test_missing_confirm_quarantines_until_actual_stack_completion(self):
        self.config(); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.ack(0); self.event('nc_absence')
        self.advance(30000)
        self.assertTrue(self.diag()[7] & 8)
        self.event('nc_motion'); self.advance(120000)
        self.assertEqual(self.fw.host_count(), 2)  # old OFF can still be retrying
        self.assertEqual(self.fw.host_inflight(), 1)
        self.ack(1)  # genuine old completion releases it; newer ON already expired
        self.assertEqual(self.fw.host_count(), 2)
        self.assertFalse(self.diag()[7] & 8)

    def test_time_timeout_still_owns_stack_slot_until_real_completion(self):
        self.config(flags=1); self.fw.nc_service()
        self.assertEqual(self.fw.host_inflight(), 1)
        self.advance(4000); self.advance(60000)
        self.assertEqual(self.fw.host_count(), 1)
        self.assertTrue(self.diag()[7] & 8)
        self.config(flags=0); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 1)
        self.ack(0)
        self.assertEqual(self.fw.host_count(), 2)
        self.assertEqual(self.frame(1)[3], 152)

    def test_full_binding_table_replacement_is_seen_in_one_scan(self):
        self.config()
        for i in range(12): self.fw.host_bind(i, 100+i, 1, 2, 0)
        self.event('nc_motion')
        for i in range(12): self.ack(i)
        self.assertEqual(self.fw.host_count(), 12)
        for i in range(12): self.fw.host_bind(i, 200+i, 1, 2, 0)
        self.fw.nc_service()
        self.assertEqual(self.fw.host_count(), 14)

    def test_group_off_all_first_hops_failed_rearms_at_next_absence(self):
        self.config(flags=2); self.fw.host_bind(0, 123, 0, 2, 1)
        self.event('nc_motion'); self.ack(0); self.advance(2000); self.ack(1)
        self.event('nc_absence'); self.ack(2, 0xa7)
        self.advance(2000); self.ack(3, 0xa7)
        self.advance(10000); self.ack(4, 0xa7)
        self.assertTrue(self.diag()[7] & 2)
        self.fw.host_dark(0); self.event('nc_motion'); self.event('nc_absence')
        self.assertEqual(self.fw.host_count(), 6)
        self.assertEqual(self.frame(5)[3], 0)

    def test_failed_unicast_off_does_not_reoff_successful_group(self):
        self.config(flags=2)
        self.fw.host_bind(0, 123, 1, 2, 0); self.fw.host_bind(1, 321, 0, 2, 1)
        self.event('nc_motion'); self.ack(0); self.ack(1)
        self.advance(2000); self.ack(2)
        self.event('nc_absence'); self.ack(3, 0xa7); self.ack(4)
        self.advance(2000); self.ack(5, 0xa7); self.ack(6)
        self.advance(10000); self.ack(7, 0xa7)
        self.fw.host_dark(0); self.event('nc_motion'); self.event('nc_absence')
        self.assertEqual(self.fw.host_count(), 9)
        self.assertEqual(self.fw.host_ack_option(8), 1)  # unicast only

    def test_nv_readback_failure_can_persist_but_does_not_commit_ram(self):
        self.fw.host_read_fail(1)
        self.assertEqual(self.config(), 0xc0)
        self.assertFalse(self.diag()[7] & 1)
        self.fw.host_read_fail(0); self.fw.nc_init()
        self.assertTrue(self.diag()[7] & 1)  # caller must inspect; NV may have changed

    def test_cycle_snapshot_and_next_cycle_new_settings(self):
        self.config(); self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion'); self.ack(0)
        self.config(level=254, fade_out=10)
        self.event('nc_absence'); self.ack(1)
        self.assertEqual(int.from_bytes(self.frame(1)[4:], 'little'), 30)
        self.advance(1000); self.event('nc_motion'); self.ack(2)
        self.assertEqual(self.frame(2)[3], 152)
        self.event('nc_absence'); self.ack(3)
        self.advance(5000); self.event('nc_motion')
        self.assertEqual(self.frame(4)[3], 254)

    def test_fuzz_time_envelope_and_records_with_undefined_behavior_checks(self):
        import random
        rng = random.Random(164)
        for _ in range(1500):
            self.fw.host_reset(); self.config(flags=1); self.fw.nc_service()
            payload = rng.randbytes(rng.randrange(100))
            self.receive_clock(payload)
            self.assertEqual(self.fw.nc_local_minute(), -1)

    def test_all_four_time_lux_modes_and_window_edges(self):
        # UTC time in this fixture is 19:00 local. Time endpoints are half-open.
        for flags in range(4):
            for dark in (0, 1):
                for inside in (False, True):
                    with self.subTest(flags=flags, dark=dark, inside=inside):
                        self.fw.host_reset()
                        self.config(flags=flags, start=1140 if inside else 0,
                                    end=1200 if inside else 1140)
                        self.fw.host_bind(0, 123, 1, 2, 0)
                        if flags & 1:
                            self.fw.nc_service()
                            self.receive_clock(self.clock_payload(utc=820605600))
                        count = self.fw.host_count()
                        self.fw.host_dark(dark); self.event('nc_motion')
                        admitted = (not flags & 1 or inside) and (not flags & 2 or dark)
                        self.assertEqual(self.fw.host_count() - count, int(admitted))

    def test_seeded_decisions_match_independent_model(self):
        import random
        from host.model import Controller
        events = ('motion', 'motion', 'absence', 'advance', 'bright', 'dark',
                  'enable', 'disable', 'config')
        for seed in range(20):
            rng = random.Random(seed)
            self.fw.host_reset(); self.config()
            self.fw.host_bind(0, 123, 1, 2, 0)
            model = Controller(); now = cursor = 0
            for step in range(1000):
                event = rng.choice(events)
                with self.subTest(seed=seed, step=step, event=event):
                    if event == 'advance':
                        ms = rng.randrange(20000); now += ms
                        self.fw.host_advance(ms)
                    elif event in ('bright', 'dark'):
                        model.dark = event == 'dark'; self.fw.host_dark(model.dark)
                    elif event == 'config':
                        model.config = (bool(rng.getrandbits(1)), rng.randrange(1, 255),
                                        rng.randrange(1201), rng.randrange(1201))
                        lux, level, fade_in, fade_out = model.config
                        self.assertEqual(self.config(flags=2*lux, level=level,
                                                     fade_in=fade_in, fade_out=fade_out), 0)
                    expected = model.decide(event, now)
                    if event in ('enable', 'disable'):
                        self.fw.host_master(event == 'enable')
                    elif event == 'motion': self.fw.nc_motion()
                    elif event == 'absence': self.fw.nc_absence()
                    self.fw.nc_service()
                    self.assertEqual(self.fw.host_generation(), model.generation)
                    self.assertEqual(self.fw.host_phase(), model.phase)
                    count = self.fw.host_count()
                    self.assertEqual(count - cursor, int(expected is not None))
                    if expected is not None:
                        frame = self.frame(cursor)
                        self.assertEqual(frame[2], 4)
                        self.assertEqual(frame[3], model.snapshot[1] if expected else 0)
                        self.assertEqual(int.from_bytes(frame[4:], 'little'),
                                         model.snapshot[2 if expected else 3])
                        self.ack(cursor)
                    self.assertLessEqual(self.fw.host_inflight(), 2)
                    cursor = count


if __name__ == '__main__':
    unittest.main()

"""Offline recovery-gate tests; no real socket, MQTT or programmer access."""
import hashlib
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_ota_campaign as campaign
from bseed_nonpm_recovery_gate import verify_recovery


def fixture(tmp_path):
    profile = dict(non_pm=True, manufacturer='o1jzcxou', model='TS011F-BS',
        preflash_role='EndDevice', postflash_role='EndDevice', block_bytes=32,
        ieee='0x0011223344556677', sha256='a'*64,
        preflash_build='1.1.2-bseedcli4')
    content = bytearray(512*1024)
    content[8:12] = b'KNLT'
    content[100:100+len(profile['preflash_build'])] = profile['preflash_build'].encode()
    content[200:200+len(b'o1jzcxou;TS011F-BS;')] = b'o1jzcxou;TS011F-BS;'
    backups = [tmp_path / f'readback_{i}.bin' for i in (1,2)]
    for b in backups: b.write_bytes(content)
    record = dict(schema=1, target_ieee=profile['ieee'], board='OUTLET_BSEED_TS011F',
        chip='TLSR8258', programming_method='TLSR8258-SWire',
        candidate_sha256=profile['sha256'], board_inspected=True,
        mains_isolation_verified=True, low_voltage_programming_verified=True,
        programmer_readback_tested=True, backup_restore_procedure_reviewed=True,
        readback_files=[str(b) for b in backups], flash_capacity_bytes=len(content),
        backup_sha256=hashlib.sha256(content).hexdigest())
    file = tmp_path / 'recovery_record.json'
    file.write_text(json.dumps(record)); profile['recovery_evidence']=str(file)
    return profile,record,file,backups


def test_full_readback_gate_and_unloaded_confirmation(tmp_path):
    profile,record,file,backups = fixture(tmp_path)
    with pytest.raises(ValueError,match='Physical load'):
        verify_recovery(profile)
    assert verify_recovery(profile,confirm_unloaded=True)['readback_sha256'] == record['backup_sha256']
    profile['block_bytes']=50
    with pytest.raises(ValueError,match='32 bytes'):
        verify_recovery(profile,confirm_unloaded=True)
    profile['block_bytes']=32;profile['model']='TS011F-BS-PM'
    with pytest.raises(ValueError,match='identity/role'):
        verify_recovery(profile,confirm_unloaded=True)


def test_missing_evidence_and_foreign_identity_fail_closed(tmp_path):
    profile,record,file,_=fixture(tmp_path)
    profile.pop('recovery_evidence')
    with pytest.raises(ValueError,match='Recovery evidence'):
        verify_recovery(profile,confirm_unloaded=True)
    profile['recovery_evidence']=str(file)
    for field,value,error in [('target_ieee','0x0','IEEE'),
                               ('candidate_sha256','b'*64,'different candidate'),
                               ('programming_method','generic clip','SWire'),
                               ('mains_isolation_verified',False,'attestation')]:
        modified=dict(record);modified[field]=value;file.write_text(json.dumps(modified))
        with pytest.raises(ValueError,match=error):
            verify_recovery(profile,confirm_unloaded=True)


def test_modified_and_unrelated_readbacks_rejected(tmp_path):
    profile,record,file,backups=fixture(tmp_path)
    raw=bytearray(backups[1].read_bytes());raw[12345]^=1;backups[1].write_bytes(raw)
    with pytest.raises(ValueError,match='readbacks must match'):
        verify_recovery(profile,confirm_unloaded=True)
    backups[1].write_bytes(backups[0].read_bytes())
    record['readback_files']=[str(backups[0]),str(backups[0])]
    file.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='independent'):
        verify_recovery(profile,confirm_unloaded=True)
    record['readback_files']=[str(b) for b in backups]
    for offset,count,reason in [(8,4,'boot slot'),(100,len(profile['preflash_build']),'Client build'),
                                 (200,len(b'o1jzcxou;TS011F-BS;'),'board config')]:
        content=bytearray(backups[0].read_bytes());content[offset:offset+count]=b'\x00'*count
        for b in backups:b.write_bytes(content)
        record['backup_sha256']=hashlib.sha256(content).hexdigest()
        file.write_text(json.dumps(record))
        with pytest.raises(ValueError,match=reason):
            verify_recovery(profile,confirm_unloaded=True)
        pristine=fixture(tmp_path)
        profile,record,file,backups=pristine


def test_wrapper_flash_blocks_before_subprocess_without_hardware_evidence(tmp_path,monkeypatch):
    from unittest.mock import patch
    cfg,record,file,_=fixture(tmp_path)
    cfg.update(device='BedroomSocketCabinetRight',expect_relay='OFF',require_pm=False,
               workdir=str(tmp_path / 'campaign'))
    cfg.pop('recovery_evidence')
    src=tmp_path/'private_profile.json';src.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(src),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee'],'--confirm-load-unplugged'])
    with patch.object(campaign,'load_profile',return_value=cfg), patch.object(campaign.subprocess,'call') as call:
        with pytest.raises(ValueError,match='Recovery evidence'):
            campaign.main()
        call.assert_not_called()


def test_runner_cannot_bypass_wrapper_flash_gate(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    import bseed_targeted_z2m_ota as runner
    args=SimpleNamespace(mode='flash',max_block_bytes=32,check_timeout_seconds=90,
        non_pm=True,manufacturer='o1jzcxou',model='TS011F-BS',role='EndDevice',
        ieee='0x0011223344556677',sha256='a'*64,preflash_build='1.1.2-bseedcli4',
        hardware_evidence=None,confirm_load_unplugged=True,accept_nonrecoverable_ota_risk=False,
        device="BedroomSocketCabinetRight",relay_get_key="state_relay",expect_relay="OFF",
        preflash_relay_physical_mode="follow_state")
    with patch.object(runner,'arguments',return_value=args),patch.object(runner,'verify_image') as image:
        with pytest.raises(ValueError,match='Recovery evidence'):
            runner.main()
        image.assert_not_called()


def test_noninvasive_waiver_covers_signed_off_rc2_canary():
    profile = dict(non_pm=True, manufacturer='o1jzcxou', model='TS011F-BS',
        preflash_role='EndDevice', postflash_role='EndDevice', block_bytes=32,
        device='BedroomSocketCabinetRight', ieee='0xa4c13824a7005afb',
        sha256='e6fb2cca2a244a42ab5e8da166ed35ec438434220a46c89a37bc98086c326d1b',
        preflash_build='1.1.2-bseedcli4', postflash_build='1.1.2-bseedcli5-rc2',
        require_pm=False, relay_get_key='state_relay', expect_relay='OFF',
        preflash_relay_physical_mode='follow_state')
    out = verify_recovery(profile, confirm_unloaded=True,
                          accept_nonrecoverable_ota=True)
    assert out['method'] == 'non-invasive single OTA canary'
    assert out['recovery_available'] is False
    bad = dict(profile, sha256='0'*64)
    with pytest.raises(ValueError, match='signed-off'):
        verify_recovery(bad, confirm_unloaded=True,
                        accept_nonrecoverable_ota=True)
    pm = dict(profile, require_pm=True)
    with pytest.raises(ValueError, match='refuses PM'):
        verify_recovery(pm, confirm_unloaded=True,
                        accept_nonrecoverable_ota=True)

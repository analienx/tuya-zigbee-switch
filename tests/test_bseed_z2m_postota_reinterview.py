"""Offline same-role post-OTA interview gates; no MQTT calls or device mutations."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_z2m_postota_reinterview import identity,evaluate


def target(**changes):
    data={'ieee_address':'0x0011223344556677','friendly_name':'Canary',
          'manufacturer':'b28wrpvx','model_id':'TS011F-BS-PM','type':'Router',
          'network_address':31000,'interview_state':'SUCCESSFUL',
          'software_build_id':'old'}
    data.update(changes);return data


def test_identity_rejects_wrong_device_even_if_expected_build():
    for changed in ({'ieee_address':'0xBAD'},{'friendly_name':'Neighbor'},
                    {'manufacturer':'stock'},{'model_id':'other'}, {'type':'EndDevice'}):
        with pytest.raises(ValueError,match='mismatch'):
            identity(target(**changed),'0x0011223344556677','Canary','b28wrpvx','TS011F-BS-PM','Router')


def test_interview_ok_but_old_build_is_not_accepted():
    before=target(); after=target(interview_state='SUCCESSFUL')
    assert evaluate(before,after,{'status':'ok'},'new')=='build_mismatch_after_interview'


def test_transfer_is_not_acceptance_even_after_successful_interview():
    assert evaluate(target(),target(software_build_id='new'),{'status':'ok'},'new')=='build_refreshed_postflash_unverified'


def test_failed_interview_never_passes():
    for response in (None,{'status':'error'},{'status':'timeout'}):
        assert evaluate(target(),target(software_build_id='new'),response,'new')=='interview_failed'


def test_reinterview_detects_unexpected_network_address_change():
    assert evaluate(target(),target(network_address=40000,software_build_id='new'),{'status':'ok'},'new')=='network_address_changed_requires_audit'


def test_reinterview_detects_incomplete_interview_and_absent_target():
    assert evaluate(target(),None,{'status':'ok'},'new')=='target_disappeared'
    assert evaluate(target(),target(interview_state='PENDING',software_build_id='new'),{'status':'ok'},'new')=='interview_incomplete'


def test_campaign_requires_exact_image_and_transaction():
    from bseed_z2m_postota_reinterview import validate_campaign
    import copy
    record={'ieee':'0x0011223344556677','device':'Canary','sha256':'a'*64,
            'phase':'ota_transfer_ok_postflash_unverified','token':'matched',
            'response':{'status':'ok','transaction':'matched',
                        'data':{'id':'0x0011223344556677'}}}
    assert validate_campaign(record,record['ieee'],record['device'],record['sha256'])
    for key,bad in [('ieee','0xBAD'),('device','Neighbor'),('sha256','b'*64),
                    ('phase','update_error')]:
        altered=copy.deepcopy(record);altered[key]=bad
        with pytest.raises(ValueError):
            validate_campaign(altered,record['ieee'],record['device'],record['sha256'])
    for key,bad in [('status','error'),('transaction','foreign'),('data',{}),
                    ('data',{'id':'0xBAD'})]:
        altered=copy.deepcopy(record);altered['response'][key]=bad
        with pytest.raises(ValueError):
            validate_campaign(altered,record['ieee'],record['device'],record['sha256'])
    with pytest.raises(ValueError):
        validate_campaign(dict(record,response=None),record['ieee'],record['device'],record['sha256'])


def test_fresh_target_interview_and_inventory_are_both_mandatory():
    from bseed_z2m_postota_reinterview import require_fresh_interview
    assert require_fresh_interview([{'status':'started'},{'status':'successful'}],True)
    for events,fresh in [([],True),([{'status':'started'}],True),
                         ([{'status':'failed'}],True),([{'status':'successful'}],False)]:
        with pytest.raises(RuntimeError):require_fresh_interview(events,fresh)

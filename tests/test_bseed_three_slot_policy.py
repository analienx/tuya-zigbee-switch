import json,datetime as dt,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_network_campaign_lock import acquire,authority_path,dual_slot_path,update,read_lock,require_profile_authority

N='0x00124b0000000004'
A='0xa4c1380000000001'
B='0xa4c1380000000002'
C='0xa4c1380000000003'
D='0xa4c1380000000004'
SHA='1'*64

def campaign(root,ieee,peers,maxslots=3):
 return dict(workdir=str(root/('w'+ieee[-1])),network_lock_dir=str(root/'authority'),
     network_id=N,network_lock_shared=True,
     parallel_ota_max=maxslots,concurrent_ota_ieees=peers,
     device='S'+ieee[-1],ieee=ieee,
     preflash_role='Router',postflash_role='Router',force_test_transition=False,
     source_rejoin_strategy='none',allow_join_all_fallback=False,
     block_bytes=48,response_delay_ms=1800)

def start(root,ieee,peers,*,maxslots=3,legacy=None,active=None):
 p=campaign(root,ieee,peers,maxslots=maxslots)
 path=require_profile_authority(p)
 wk=Path(p['workdir'])/'ACTIVE_LOCK.json'
 kwargs=dict(network_id=N,token='token-'+ieee,device=p['device'],
             ieee=ieee,image_sha256=SHA,
             parallel_allowed_ieees=peers if maxslots>1 else None,
             parallel_ota_max=maxslots,work_lock_path=str(wk))
 if legacy:kwargs['legacy_peer_work_lock']=str(legacy)
 if active:kwargs['active_peer_work_lock']=str(active)
 record=acquire(path,**kwargs)
 update(path,record['token'],'ota_running')
 wk.parent.mkdir(parents=True,exist_ok=True)
 wk.write_text(json.dumps(dict(ieee=ieee,token=record['token'],sha256=SHA,
                            phase='ota_running',network_lock=str(path))))
 (wk.parent/'LIVE_STATUS.json').write_text(json.dumps({
   'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
   'ieee':ieee,'token':record['token'],'phase':'ota_running',
   'update':{'state':'updating','progress':1.0}}))
 return path,wk

def test_three_slots_can_run_without_legacy(tmp_path):
 pa,wa=start(tmp_path,A,[])
 pb,wb=start(tmp_path,B,[A])
 pc,wc=start(tmp_path,C,[A,B])
 assert all(read_lock(p)['phase']=='ota_running' for p in (pa,pb,pc))
 with pytest.raises(RuntimeError,match='occupied'):
  start(tmp_path,D,[A,B,C])

def test_running_legacy_and_two_slot_can_accept_third(tmp_path):
 legacy,wlegacy=start(tmp_path,A,[],maxslots=1)
 second,wsecond=start(tmp_path,B,[A],maxslots=2,legacy=wlegacy)
 assert read_lock(second)['mixed_legacy_peer_ieee']==A
 third,wthird=start(tmp_path,C,[A,B],maxslots=3,
                  legacy=wlegacy,active=wsecond)
 r=read_lock(third)
 assert r['parallel_ota_max']==3 and r['admission_peer_tokens']=={
     A:'token-'+A,B:'token-'+B}

def test_three_slot_refuses_unlisted_and_stale_peers(tmp_path):
 pa,wa=start(tmp_path,A,[])
 with pytest.raises(RuntimeError,match='allowlist'):
  start(tmp_path,B,[])
 pa_data=json.loads((wa.parent/'LIVE_STATUS.json').read_text())
 pa_data['observed_at']='2026-01-01T00:00:00+00:00'
 (wa.parent/'LIVE_STATUS.json').write_text(json.dumps(pa_data))
 with pytest.raises(RuntimeError,match='freshly updating'):
  start(tmp_path,B,[A])

def test_boolean_and_six_not_valid(tmp_path):
 bad=campaign(tmp_path,A,[],maxslots=6)
 with pytest.raises(ValueError):require_profile_authority(bad)
 bad['parallel_ota_max']=True
 with pytest.raises(ValueError):require_profile_authority(bad)


E='0xa4c1380000000005'
F='0xa4c1380000000006'


def start5(root,ieee,peers):
 p=campaign(root,ieee,peers,maxslots=5)
 p['response_delay_ms']=2400
 path=require_profile_authority(p)
 wk=Path(p['workdir'])/'ACTIVE_LOCK.json'
 record=acquire(path,network_id=N,token='token-'+ieee,device=p['device'],ieee=ieee,
                image_sha256=SHA,parallel_allowed_ieees=peers,parallel_ota_max=5,
                work_lock_path=str(wk))
 update(path,record['token'],'ota_running')
 wk.parent.mkdir(parents=True,exist_ok=True)
 wk.write_text(json.dumps(dict(ieee=ieee,token=record['token'],sha256=SHA,
                            phase='ota_running',network_lock=str(path))))
 (wk.parent/'LIVE_STATUS.json').write_text(json.dumps({
   'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
   'ieee':ieee,'token':record['token'],'phase':'ota_running',
   'update':{'state':'updating','progress':1.0}}))
 return path,wk


def test_five_slots_with_one_fixed_allowlist_per_campaign(tmp_path):
 group=[A,B,C,D,E]
 peers=lambda me:[x for x in group if x!=me]
 # A fixed profile may list peers that are not running yet.
 for ieee in group:start5(tmp_path,ieee,peers(ieee))
 with pytest.raises(RuntimeError,match='occupied'):
  start5(tmp_path,F,group[:4])
 assert read_lock(dual_slot_path(authority_path(tmp_path/'authority',N),E))['parallel_ota_max']==5


def test_a_peer_between_attempts_keeps_its_slot_and_must_be_allowed(tmp_path):
 pa,wa=start5(tmp_path,A,[B,C])
 update(pa,'token-'+A,'update_error')  # A failed and will resume; it still holds a slot
 lock=json.loads(wa.read_text());lock['phase']='update_error';wa.write_text(json.dumps(lock))
 start5(tmp_path,B,[A,C])
 with pytest.raises(RuntimeError,match='allowlist'):
  start5(tmp_path,C,[B])


def test_capacity_is_the_smallest_slot_count_of_any_participant(tmp_path):
 start(tmp_path,A,[B,C,D],maxslots=3)
 start5(tmp_path,B,[A,C,D])
 start5(tmp_path,C,[A,B,D])
 with pytest.raises(RuntimeError,match='occupied'):
  start5(tmp_path,D,[A,B,C])


def test_four_and_five_slots_need_slower_pacing(tmp_path):
 p=campaign(tmp_path,A,[B],maxslots=5)
 with pytest.raises(ValueError,match='2400'):
  require_profile_authority(p)
 p['response_delay_ms']=2400
 require_profile_authority(p)

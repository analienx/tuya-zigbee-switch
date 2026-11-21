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
 with pytest.raises(RuntimeError,match='three|Three|occupied'):
  start(tmp_path,D,[A,B])

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

def test_boolean_and_four_not_valid(tmp_path):
 bad=campaign(tmp_path,A,[],maxslots=4)
 with pytest.raises(ValueError):require_profile_authority(bad)
 bad['parallel_ota_max']=True
 with pytest.raises(ValueError):require_profile_authority(bad)

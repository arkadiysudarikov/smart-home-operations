import sys, unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import garage_camera_occupancy as camera
class CameraOccupancyTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime.now(timezone.utc)
  self.controller=SimpleNamespace(garage_light_config=lambda:{'camera_motion':{'enabled':True}},write_garage_light_hold_state=Mock(),schedule_garage_light_hold_check=Mock())
 def test_continuous_camera_activity_never_hits_phone_hold_cap(self):
  s={'active':True,'holdSeconds':300,'lastActivityAt':(self.now-timedelta(hours=2)).isoformat()}
  with patch.object(camera,'read',return_value={'occupied':True}): self.assertTrue(camera.guard(self.controller,s,self.now))
  self.assertEqual(s['status'],'camera-occupied');self.assertTrue(s['cameraWasOccupied']);self.assertEqual(s['lastActivityAt'],self.now.isoformat(timespec='seconds'))
 def test_camera_clear_starts_new_countdown_once(self):
  s={'active':True,'holdSeconds':300,'cameraWasOccupied':True}
  with patch.object(camera,'read',return_value={'occupied':False}):
   self.assertTrue(camera.guard(self.controller,s,self.now));self.assertFalse(camera.guard(self.controller,s,self.now+timedelta(seconds=300)))
  self.assertEqual(s['holdUntil'],(self.now+timedelta(seconds=300)).isoformat(timespec='seconds'))
 def test_camera_failure_preserves_light_and_retries(self):
  s={'active':True}
  with patch.object(camera,'read',return_value={'occupied':None,'error':'unavailable'}):self.assertTrue(camera.guard(self.controller,s,self.now))
  self.assertTrue(s['active']);self.assertEqual(s['status'],'camera-unavailable')
 def test_disabled_camera_keeps_existing_behavior(self):
  with patch.object(camera,'read',return_value=None):self.assertFalse(camera.guard(self.controller,{},self.now))

 def test_active_camera_timer_is_still_reconciled(self):
  import tempfile,json,contextlib,io
  import garage_light_reconcile as reconcile
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'data').mkdir();(root/'config').mkdir()
   (root/'data/garage_light_hold.json').write_text(json.dumps({'active':True}))
   (root/'config/sources.json').write_text(json.dumps({'garage_light':{'camera_motion':{'enabled':True}},'actions':{'port':18765}}))
   response=Mock();response.read.return_value=b'{}'
   context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
   with patch.object(reconcile,'ROOT',root),patch.object(reconcile.urllib.request,'urlopen',return_value=context) as request,contextlib.redirect_stdout(io.StringIO()):
    reconcile.main()
   self.assertEqual(request.call_count,1)

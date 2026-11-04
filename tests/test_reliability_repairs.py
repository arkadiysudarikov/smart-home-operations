import json
import sys
import tempfile
import shutil
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import announcement_ack as ack
import announcement_followup as follow
import announcement_pause as pause
import calendar_announcements as calendar
import garage_camera_event as edges
import garage_camera_occupancy as camera
import garage_light_reconcile as garage
import laundry_delivery as delivery
import deploy_runtime


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.now = 1792512000

    def test_ack_returns_while_relay_is_blocked_and_stops_future_repeats(self):
        ack.record(self.data, {'announcementId':'washer','message':'Finished.','ok':True}, self.now)
        entered, release = threading.Event(), threading.Event()
        def relay(*args):
            entered.set()
            release.wait(3)
        with patch('generate_alerts.run_indoor_homepod_announcement',side_effect=relay), patch.object(follow,'active',return_value=False):
            worker=threading.Thread(target=ack.tick,args=(self.data,self.now+300))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                cleared=threading.Event()
                clearer=threading.Thread(target=lambda:(ack.acknowledge(self.data),cleared.set()))
                clearer.start()
                self.assertTrue(cleared.wait(.5), 'acknowledgment waited for the relay')
                clearer.join(1)
            finally:
                release.set();worker.join(2)
            self.assertEqual(ack.tick(self.data,self.now+600),0)

    def test_ack_during_privacy_check_cancels_the_claim(self):
        ack.record(self.data, {'announcementId':'washer','message':'Finished.','ok':True}, self.now)
        def privacy(*args):
            ack.acknowledge(self.data)
            return 'allowed'
        with patch.object(follow,'calendar_disposition',side_effect=privacy), patch.object(follow,'active',return_value=False), patch('generate_alerts.run_indoor_homepod_announcement') as speak:
            self.assertEqual(ack.tick(self.data,self.now+300),0)
            speak.assert_not_called()

    def test_old_inflight_delivery_cannot_requeue_after_ack(self):
        ack.acknowledge(self.data)
        ack.record(self.data, {'announcementId':'washer','message':'Finished.','ok':True,
            'attemptStartedAt':'2020-01-01T00:00:00+00:00'})
        self.assertEqual(json.loads((self.data/'announcement_ack.json').read_text())['pending'],{})

    def test_ack_cancels_pending_audio_delivery_but_keeps_new_finishes(self):
        now=datetime.now(timezone.utc)
        with delivery.transaction(self.data,'washer') as state:
            delivery.enqueue(state,['announce_finish'],(now-timedelta(minutes=1)).isoformat())
        ack.acknowledge(self.data)
        execute=Mock(return_value=[{'ok':True}])
        delivery.process(self.data,'washer',{},execute)
        execute.assert_not_called()
        with delivery.transaction(self.data,'washer') as state:
            delivery.enqueue(state,['announce_finish'],(now+timedelta(minutes=1)).isoformat())
        delivery.process(self.data,'washer',{},execute)
        execute.assert_called_once()

    def test_terminal_calendar_is_pruned_but_unavailable_is_preserved(self):
        identifier='calendar-'+'a'*64
        event={'announcementId':identifier,'message':'Appointment.','ok':True}
        ack.record(self.data,event,self.now)
        with patch.object(follow,'calendar_disposition',return_value='unavailable'),patch.object(follow,'active',return_value=False):
            ack.tick(self.data,self.now+300)
        self.assertIn(identifier,json.loads((self.data/'announcement_ack.json').read_text())['pending'])
        with patch.object(follow,'calendar_disposition',return_value='expired'),patch.object(follow,'active',return_value=False):
            ack.tick(self.data,self.now+600)
        self.assertEqual(json.loads((self.data/'announcement_ack.json').read_text())['pending'],{})

    def test_confirmed_calendar_absence_is_terminal_not_reader_failure(self):
        config=self.data/'calendar.json';config.write_text('{}')
        now=datetime.now(timezone.utc)
        event={'announcementId':'calendar-'+'a'*64}
        with patch.object(calendar,'CONFIG',config),patch.object(calendar,'invoke',return_value={'generatedAt':now.isoformat(),'events':[]}):
            self.assertEqual(follow.calendar_disposition(event,now.timestamp()),'expired')
        with patch.object(calendar,'CONFIG',config),patch.object(calendar,'invoke',side_effect=RuntimeError('reader unavailable')):
            self.assertEqual(follow.calendar_disposition(event,now.timestamp()),'unavailable')

    def test_camera_pulse_keeps_five_minutes_after_clearance(self):
        now=datetime.now(timezone.utc)
        edges.record(self.data,True,(now-timedelta(seconds=90)).isoformat())
        edge=edges.record(self.data,False,(now-timedelta(seconds=60)).isoformat())
        state={'active':True,'holdSeconds':300,'lastActivityAt':(now-timedelta(seconds=300)).isoformat()}
        c=SimpleNamespace(garage_light_config=lambda:{},write_garage_light_hold_state=Mock(),schedule_garage_light_hold_check=Mock())
        with patch.object(camera,'read',return_value={'occupied':False,'lastActivityAt':edge['lastActivityAt']}):
            self.assertTrue(camera.guard(c,state,now))
            self.assertEqual(datetime.fromisoformat(state['holdUntil']),now+timedelta(seconds=240))
            self.assertFalse(camera.guard(c,state,now+timedelta(seconds=240)))

    def test_idle_garage_checks_are_throttled_but_edges_and_active_timers_are_prompt(self):
        now=datetime.now(timezone.utc).timestamp()
        previous={'checkedAt':now-15}
        self.assertFalse(garage.should_check({},previous,{},now))
        self.assertTrue(garage.should_check({'active':True},previous,{},now))
        self.assertTrue(garage.should_check({},previous,{'lastActivityAt':datetime.fromtimestamp(now-1,timezone.utc).isoformat()},now))
        self.assertTrue(garage.should_check({},previous,{},now+120))

    def test_delivery_failure_does_not_undo_committed_completion_or_retry_uncertain_audio(self):
        with delivery.transaction(self.data,'washer') as state:
            state.update(primaryArmed=False,completedCycles=1)
            delivery.enqueue(state,['announce_finish'],'now')
        def fail(*args):
            with delivery.transaction(self.data,'washer') as state:
                self.assertFalse(state['primaryArmed'])
            raise TimeoutError('uncertain relay')
        execute=Mock(side_effect=fail)
        delivery.process(self.data,'washer',{},execute)
        delivery.process(self.data,'washer',{},execute)
        execute.assert_called_once()
        state=json.loads((self.data/'washer_notifier_state.json').read_text())
        self.assertFalse(state['primaryArmed'])
        self.assertEqual(state['deliveryOutbox'][0]['status'],'uncertain')

    def test_new_start_cancels_undelivered_old_finish(self):
        state={}
        delivery.enqueue(state,['finish_on','notify_finish','announce_finish'],'old')
        delivery.enqueue(state,['finish_off'],'new')
        self.assertEqual([j['action'] for j in state['deliveryOutbox']],['finish_off'])

    def test_deploy_preserves_compiled_reader_and_live_configuration(self):
        source=self.data/'source';runtime=self.data/'runtime'
        (source/'scripts').mkdir(parents=True);(source/'scripts/app.py').write_text('value=2\n')
        (source/'config').mkdir();(source/'config/sources.json').write_text('{"newDefault":true}')
        (runtime/'config').mkdir(parents=True);(runtime/'config/sources.json').write_text('{"housePolicy":"preserve"}')
        (runtime/'config/private.json').write_text('private fixture')
        reader=runtime/'Smart Home Calendar Reader.app/reader';reader.parent.mkdir();reader.write_text('compiled fixture')
        deploy_runtime.deploy(source,runtime)
        self.assertEqual(reader.read_text(),'compiled fixture')
        self.assertEqual((runtime/'config/private.json').read_text(),'private fixture')
        self.assertEqual(json.loads((runtime/'config/sources.json').read_text())['housePolicy'],'preserve')
        self.assertEqual((runtime/'scripts/app.py').read_text(),'value=2\n')

    def test_tank_alert_obeys_same_pause_as_washer(self):
        state={'startedAt':self.now,'until':self.now+3600}
        self.assertTrue(pause.paused('detergent_tank_low',state,self.now+1))

    def test_deploy_rolls_back_already_replaced_files_on_failure(self):
        source=self.data/'source';runtime=self.data/'runtime'
        (source/'scripts').mkdir(parents=True);(runtime/'scripts').mkdir(parents=True)
        (source/'scripts/a.py').write_text('value=2\n');(source/'scripts/b.py').write_text('value=3\n')
        (runtime/'scripts/a.py').write_text('value=1\n')
        copy=shutil.copy2
        def fail_second(src,dst,*args,**kwargs):
            if Path(src)==source/'scripts/b.py':raise OSError('fixture copy failure')
            return copy(src,dst,*args,**kwargs)
        with patch.object(deploy_runtime.shutil,'copy2',side_effect=fail_second):
            with self.assertRaises(OSError):deploy_runtime.deploy(source,runtime)
        self.assertEqual((runtime/'scripts/a.py').read_text(),'value=1\n')
        self.assertFalse((runtime/'scripts/b.py').exists())

    def test_missing_tracked_dependency_blocks_deploy_before_any_change(self):
        source=self.data/'source';runtime=self.data/'runtime'
        (source/'scripts').mkdir(parents=True);(source/'.git').mkdir();(runtime/'scripts').mkdir(parents=True)
        (source/'scripts/a.py').write_text('import b\n');(source/'scripts/b.py').write_text('value=2\n')
        (runtime/'scripts/a.py').write_text('value=1\n')
        with patch.object(deploy_runtime.subprocess,'check_output',return_value='scripts/a.py\n'):
            with self.assertRaisesRegex(RuntimeError,'dependency'):
                deploy_runtime.deploy(source,runtime)
        self.assertEqual((runtime/'scripts/a.py').read_text(),'value=1\n')


if __name__ == '__main__':
    unittest.main()

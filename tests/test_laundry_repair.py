import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import laundry_ack as laundry
import announcement_ack as ack
import garage_light_reconcile as garage
import washer_notifier as notifier

class LaundryRepairTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,20,18,tzinfo=ZoneInfo('America/Los_Angeles'))
        self.current = dict(fresh=True, doorOpen=False, inUse=False, cycleActive=False)
        self.prior = dict(awaitingUnload=True, washFinishedAt=(self.now-timedelta(minutes=30)).isoformat(), lastCycleActive=False)

    def test_venting_countdown_is_not_a_new_wash_or_false_finish(self):
        current={**self.current,'cycleActive':False,'ventingActive':True,'inUse':True,'remainingSeconds':26000}
        prior={'lastCycleActive':True,'lastInUse':True,'primaryArmed':True,'runningSamples':20,'washStartedAt':(self.now-timedelta(hours=1)).isoformat()}
        state,actions=notifier.evolve_state(prior,current,self.now,{'finish_signal':'countdown_confirmed'})
        self.assertFalse(state['primaryArmed']);self.assertTrue(state['ventingArmed'])
        self.assertEqual(state['countdownStatus'],'venting_not_washing');self.assertNotIn('announce_finish',actions)

    def test_real_wash_to_venting_still_announces_completion(self):
        current={**self.current,'cycleActive':False,'ventingActive':True,'inUse':True,'remainingSeconds':28000}
        prior={'lastWasherMode':'wash','lastCycleActive':True,'lastInUse':True,'primaryArmed':True,'runningSamples':20,'washStartedAt':(self.now-timedelta(hours=1)).isoformat()}
        state,actions=notifier.evolve_state(prior,current,self.now,{'finish_signal':'countdown_confirmed'})
        self.assertIn('announce_finish',actions);self.assertTrue(state['awaitingUnload'])

    def test_open_then_closed_between_polls_cancels_reminder(self):
        c={**self.current,'doorLastOpenedAt':(self.now-timedelta(minutes=2)).isoformat()}
        s,a,clear_at=laundry.reconcile(self.prior,c,self.prior,['notify_reminder','reminder_on'],self.now,{'id':'washer'})
        self.assertFalse(s['awaitingUnload']);self.assertIn('reminder_off',a);self.assertNotIn('notify_reminder',a);self.assertIsNotNone(clear_at)

    def test_stale_or_old_open_does_not_acknowledge(self):
        for changes in [dict(fresh=False,doorOpen=True),dict(doorLastOpenedAt=(self.now-timedelta(hours=1)).isoformat()),dict(doorOpen=None)]:
            s,a,at=laundry.reconcile(self.prior,{**self.current,**changes},self.prior,[],self.now,{'id':'washer'})
            self.assertTrue(s['awaitingUnload']);self.assertIsNone(at)

    def test_tank_low_once_then_refill_rearms(self):
        c={**self.current,'detergentTankLow':True}
        s,a,_=laundry.reconcile({},c,{},[],self.now,{'id':'washer'})
        self.assertIn('announce_tank_low',a)
        s,a,_=laundry.reconcile(s,c,s,[],self.now,{'id':'washer'});self.assertNotIn('announce_tank_low',a)
        s,a,_=laundry.reconcile(s,{**c,'detergentTankLow':False},s,[],self.now,{'id':'washer'})
        s,a,_=laundry.reconcile(s,c,s,[],self.now,{'id':'washer'});self.assertIn('announce_tank_low',a)

    def test_failed_delivery_retries_and_unloading_cancels_it(self):
        results=[{'action':'announce_finish','ok':False}]
        state=laundry.remember_delivery({}, {}, ['announce_finish'], results,self.now)
        self.assertEqual(state['notificationDeliveryStatus'],'retry_pending')
        self.assertIn('announce_finish',laundry.retry_actions(state,self.current,{},[],self.now))
        self.assertNotIn('announce_finish',laundry.retry_actions(state,self.current,{},[],self.now,cleared=True))
        for _ in range(2):state=laundry.remember_delivery(state,state,['announce_finish'],results,self.now)
        self.assertEqual(state['notificationDeliveryStatus'],'failed')
        self.assertEqual(laundry.retry_actions(state,self.current,{},[],self.now),[])

    def test_low_tank_observed_at_night_announces_once_in_daytime(self):
        c={**self.current,'detergentTankLow':True}
        night=self.now.replace(hour=23)
        state,actions,_=laundry.reconcile({},c,{},[],night,{'id':'washer'})
        self.assertNotIn('announce_tank_low',actions)
        state,actions,_=laundry.reconcile(state,c,state,[],self.now+timedelta(days=1),{'id':'washer'})
        self.assertIn('announce_tank_low',actions)

    def test_only_target_appliance_and_older_snooze_cleared(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);event=dict(announcementId='washer',message='Wash finished.',ok=True,at=(self.now-timedelta(minutes=10)).isoformat())
            ack.record(p,event);ack.record(p,{**event,'announcementId':'dryer'})
            (p/'announcement_followup.json').write_text(json.dumps({'event':event}))
            self.assertTrue(laundry.clear(p,'washer',self.now.isoformat()))
            self.assertEqual(set(json.loads((p/'announcement_ack.json').read_text())['pending']),{'dryer'})
            self.assertEqual(json.loads((p/'announcement_followup.json').read_text()),{})
            self.assertFalse(laundry.clear(p,'dryer',(self.now-timedelta(hours=1)).isoformat()))

    def test_idle_source_tile_cannot_cancel_effective_completed_wash(self):
        s,a,at=laundry.reconcile({'lastCycleActive':False,'lastInUse':True},{**self.current,'cycleActive':True,'inUse':True}, {'lastCycleActive':False}, [],self.now,{'id':'washer'})
        self.assertIsNone(at)

class GarageRepairTests(unittest.TestCase):
    def controller(self,on=True,brightness=100,active=False):
        now=datetime.now(ZoneInfo('America/Los_Angeles'))
        return SimpleNamespace(GARAGE_LIGHT_HOLD_LOCK=threading.Lock(),read_garage_light_hold_state=lambda:{'active':active},garage_light_status=lambda:{'ok':True,'light':{'on':on,'brightness':brightness}},local_now=lambda:now,garage_light_hold_seconds=lambda:300,GARAGE_LIGHT_ID='test',write_garage_light_hold_state=Mock(),append_garage_activity_event=Mock(),schedule_garage_light_hold_check=Mock())

    def test_adopts_direct_on_without_sending_an_on_command(self):
        c=self.controller();result=garage.adopt(c)
        self.assertTrue(result['adopted']);c.schedule_garage_light_hold_check.assert_called_once()
        self.assertEqual(c.write_garage_light_hold_state.call_args.args[0]['holdSeconds'],300)

    def test_preserves_off_dimmed_or_existing_timer(self):
        for changes in [dict(on=False),dict(brightness=45),dict(active=True)]:
            c=self.controller(**changes);self.assertFalse(garage.adopt(c)['adopted']);c.schedule_garage_light_hold_check.assert_not_called()

    def test_snapshot_must_be_fresh_and_light_on(self):
        now=datetime.now().astimezone();snapshot={'captured_at':now.isoformat(),'homeEvents':{'currentCharacteristics':{'a':{'accessory':'Garage Light','service':'Garage Light','characteristic':'On','value':True},'b':{'accessory':'Garage Light','service':'Garage Light','characteristic':'Brightness','value':100}}}}
        self.assertTrue(garage.needs_timer(snapshot,{},now.timestamp()))
        self.assertFalse(garage.needs_timer(snapshot,{},now.timestamp()+301))
        self.assertFalse(garage.needs_timer(snapshot,{'active':True},now.timestamp()))

class GarageBoundedOccupancyTests(unittest.TestCase):
    def test_phone_cannot_hold_light_indefinitely(self):
        import action_server as server
        now=datetime.now(ZoneInfo('America/Los_Angeles'))
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'hold.json'
            p.write_text(json.dumps({'active':True,'lastActivityAt':(now-timedelta(seconds=301)).isoformat(), 'lastPhysicalActivityAt':(now-timedelta(seconds=901)).isoformat(),'holdSeconds':300}))
            off=Mock(return_value={'ok':True,'light':{'on':False}})
            with patch.multiple(server, GARAGE_LIGHT_HOLD_STATUS_PATH=p, GARAGE_ACTIVITY_EVENTS_PATH=Path(t)/'events.jsonl', garage_occupancy_status=lambda:{'enabled':True,'occupied':True}, garage_light_status=lambda:{'ok':True,'light':{'on':True,'brightness':100}}, set_garage_light_off=off, garage_light_config=lambda:{'restore_started_state':False}, schedule_garage_light_hold_check=Mock()):
                server.expire_garage_light_hold()
            off.assert_called_once()
            self.assertFalse(json.loads(p.read_text())['active'])

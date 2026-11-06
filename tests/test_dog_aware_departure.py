import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from dog_aware_departure import depart

class DogDepartureTests(unittest.TestCase):
    def test_only_explicit_true_arms_away(self):
        for value, mode in [(True,'away'),(False,'stay'),(None,'stay'),('true','stay'),(1,'stay')]:
            arm=Mock(return_value={'ok':True})
            self.assertEqual(depart(arm,lambda:value)['selectedMode'],mode)
            arm.assert_called_once_with(mode)
    def test_unavailable_flag_defaults_to_stay(self):
        def unavailable():raise OSError('offline')
        arm=Mock(return_value={'ok':True})
        self.assertEqual(depart(arm,unavailable)['selectedMode'],'stay')
        arm.assert_called_once_with('stay')
    def test_failed_arming_is_not_reported_successful(self):
        self.assertFalse(depart(lambda mode:{'ok':False},lambda:True)['ok'])

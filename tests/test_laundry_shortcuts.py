import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_laundry_shortcuts import workflow, APPLIANCES


class LaundryShortcutContractTest(unittest.TestCase):
    def test_every_command_returns_its_actual_ssh_result_to_siri(self):
        names = set()
        for appliance in APPLIANCES:
            payload = workflow(appliance, 'test-user')
            names.add(payload['WFWorkflowName'])
            ssh, reply = payload['WFWorkflowActions']
            request = ssh['WFWorkflowActionParameters']
            response = reply['WFWorkflowActionParameters']
            self.assertEqual(request['WFSSHScript'], appliance + '-unloaded')
            self.assertEqual(request['WFSSHAuthenticationType'], 'SSH Key')
            self.assertEqual(response['WFNoOutputSurfaceBehavior'], 'Respond')
            for field in ['WFOutput', 'WFResponse']:
                token = response[field]['Value']['attachmentsByRange']['{0, 1}']
                self.assertEqual(token['OutputUUID'], request['UUID'])
                self.assertEqual(token['Type'], 'ActionOutput')
        self.assertEqual(len(names), 3)
        with self.assertRaises(ValueError):
            workflow('all; shell command', 'test-user')

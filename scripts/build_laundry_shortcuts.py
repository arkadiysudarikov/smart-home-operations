"""Build reproducible, unsigned laundry acknowledgement Shortcuts for native import.

Import only after backing up/closing existing editors; replace each canonical
shortcut in place rather than keeping both. Never edit the Shortcuts database.
"""
import argparse
import getpass
import plistlib
import uuid
from pathlib import Path

APPLIANCES = ('washer', 'dryer', 'combo')


def workflow(appliance, user, host='m4-office-mini.local'):
    if appliance not in APPLIANCES:
        raise ValueError('Unknown laundry appliance')
    action_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'smart-home/laundry/' + appliance)).upper()
    result = {'Value': {'string': '\ufffc', 'attachmentsByRange': {'{0, 1}': {
        'OutputUUID': action_id, 'Type': 'ActionOutput', 'OutputName': 'Shell Script Result'}}},
        'WFSerializationType': 'WFTextTokenString'}
    return {
        'WFWorkflowName': 'I unloaded the ' + appliance,
        'WFWorkflowClientVersion': '3030.0.4',
        'WFWorkflowMinimumClientVersion': 900,
        'WFWorkflowMinimumClientVersionString': '900',
        'WFWorkflowTypes': [], 'WFWorkflowInputContentItemClasses': [],
        'WFWorkflowActions': [
            {'WFWorkflowActionIdentifier': 'is.workflow.actions.runsshscript',
             'WFWorkflowActionParameters': {'WFSSHScript': appliance + '-unloaded',
                 'WFSSHAuthenticationType': 'SSH Key', 'WFSSHHost': host,
                 'WFSSHUser': user, 'UUID': action_id}},
            {'WFWorkflowActionIdentifier': 'is.workflow.actions.output',
             'WFWorkflowActionParameters': {'WFNoOutputSurfaceBehavior': 'Respond',
                 'WFOutput': result, 'WFResponse': result,
                 'UUID': str(uuid.uuid5(uuid.NAMESPACE_URL, action_id + '/reply')).upper()}}
        ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--ssh-user', default=getpass.getuser())
    parser.add_argument('--host', default='m4-office-mini.local')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for appliance in APPLIANCES:
        payload = workflow(appliance, args.ssh_user, args.host)
        target = args.output_dir / (payload['WFWorkflowName'] + '.unsigned.shortcut')
        target.write_bytes(plistlib.dumps(payload))
        print(target)


if __name__ == '__main__':
    main()

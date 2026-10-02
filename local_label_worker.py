"""Run SigLIP rooms and explicitly requested Copilot photo drafts in one local process."""
import argparse
import os
import time

import config  # Load the repository .env before validating configuration.
from cloud_autolabel import heartbeat, process
from cloud_runtime import from_env


def enqueue_all(store):
    """Explicit one-time enqueue of all supported, retained acquisition photo sets."""
    counts = {'queued':0,'completed':0,'already_active':0,'skipped':0}
    offset = 0
    while True:
        page = store.queue({'scope':'acquisitions','queue':'all','offset':offset,'limit':40})
        for item in page['items']:
            if not item.get('image_count') or item.get('blocked'):
                counts['skipped'] += 1; continue
            current = store.document('autolabel-request:'+item['id']) or {}
            if current.get('status')=='running':
                counts['already_active'] += 1; continue
            try:
                response = store.request_autolabel({'property_id':item['id'],'label_provider':'copilot','all_photos':True})
                counts['completed' if response['status']=='completed' else 'queued'] += 1
            except (ValueError,RuntimeError): counts['skipped'] += 1
        offset += 40
        if offset>=page['total']: break
    print('Full Copilot queue: '+', '.join(f'{k}={v}' for k,v in counts.items()),flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enqueue-all',action='store_true',help='Explicitly queue all eligible properties and all selected original photos for Copilot drafts')
    parser.add_argument('--check',action='store_true',help='Check database configuration, Copilot login and vision model; no inference')
    parser.add_argument('--once',action='store_true',help='Process currently queued rooms and draft requests, then exit')
    args = parser.parse_args()
    if args.check and args.enqueue_all: parser.error('--check cannot enqueue labels')
    os.environ['STUDIO_AUTOLABEL_PROVIDER'] = 'hybrid'
    store = from_env().get_studio().store  # Fail before downloading/loading any model.
    from copilot_labels import CopilotLabels
    draft = CopilotLabels(store)
    try:
        available = draft.transport.preflight()
        print('Database ready. Copilot signed in. Vision model: '+draft.model,flush=True)
        if args.check: return
        if args.enqueue_all: enqueue_all(store)
        from automatic_labels import SiglipLabels, active_policy
        heartbeat(store,'loading','Loading local SigLIP checkpoint',stage='rooms')
        rooms = SiglipLabels(); rooms.policy = active_policy(); rooms.stage = 'rooms'
        from workbench_worker import WorkbenchScorer, process_pending_runs
        from workbench_training import process_training_request
        workbench = WorkbenchScorer(store,rooms)
        print('SigLIP ready. Processing queued rooms and Copilot drafts; Ctrl+C stops cleanly.',flush=True)
        while True:
            for classifier in (rooms,draft):
                heartbeat(store,'ready',stage=classifier.stage,provider=getattr(classifier,'provider',None))
                for identifier in store.autolabel_pending(stage=classifier.stage):
                    try:
                        if process(store,identifier,classifier): print(f'Completed {classifier.stage}: {identifier}',flush=True)
                    except Exception: print('Request failed; inspect its saved status before explicitly retrying.',flush=True)
            processed = process_pending_runs(store,workbench)
            if processed:
                print(f'Completed {processed} workbench model run(s).',flush=True)
            if process_training_request(store,workbench):
                print('Processed a workbench training request.',flush=True)
            if args.once: break
            time.sleep(5)
    finally:
        draft.close()
        heartbeat(store,'stopped',stage='rooms')
        heartbeat(store,'stopped',stage='features',provider='copilot')


if __name__=='__main__':
    try: main()
    except (ValueError,ImportError) as error:
        raise SystemExit(str(error)) from None

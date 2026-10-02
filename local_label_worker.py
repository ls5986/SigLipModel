"""Run SigLIP rooms and explicitly requested Copilot draft tests in one local process."""
import argparse
import os
import time

import config  # Load the repository .env before validating configuration.
from cloud_autolabel import heartbeat, process
from cloud_runtime import from_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Check database configuration, Copilot login and vision model; no inference')
    parser.add_argument('--once',action='store_true',help='Process currently queued rooms and requested eight-photo tests, then exit')
    args = parser.parse_args()
    os.environ['STUDIO_AUTOLABEL_PROVIDER'] = 'hybrid'
    store = from_env().get_studio().store  # Fail before downloading/loading any model.
    from copilot_labels import CopilotLabels
    draft = CopilotLabels(store)
    try:
        available = draft.transport.preflight()
        print('Database ready. Copilot signed in. Vision model: '+draft.model,flush=True)
        if args.check: return
        from automatic_labels import SiglipLabels, active_policy
        heartbeat(store,'loading','Loading local SigLIP checkpoint',stage='rooms')
        rooms = SiglipLabels(); rooms.policy = active_policy(); rooms.stage = 'rooms'
        print('SigLIP ready. Waiting for room requests and explicit Copilot tests (up to 8 selected photos).',flush=True)
        while True:
            for classifier in (rooms,draft):
                heartbeat(store,'ready',stage=classifier.stage,provider=getattr(classifier,'provider',None))
                for identifier in store.autolabel_pending(stage=classifier.stage):
                    try: process(store,identifier,classifier)
                    except Exception: print('Request failed; inspect its saved status before explicitly retrying.',flush=True)
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

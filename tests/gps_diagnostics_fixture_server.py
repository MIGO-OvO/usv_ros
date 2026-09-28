"""Loopback-only UI fixture; synthetic data, never connects to ROS or hardware."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import jsonify
from test_gps_diagnostics import evidence
from gps_fixtures import gps_position
from scripts.lib.gps_diagnostics import diagnose
from scripts.web_config_server import WebConfigServer


def main():
    with tempfile.TemporaryDirectory(prefix='usv-gps-ui-') as directory:
        os.environ['USV_MISSION_DATA_DIR'] = directory
        server = WebConfigServer(standalone=True)
        state = {'scenario': 'missing'}

        @server.app.route('/__fixture/<scenario>')
        def scenario(scenario):
            state['scenario'] = scenario
            return jsonify(state)

        def snapshot():
            scenario = state['scenario']
            cache = evidence(None if scenario == 'missing' else 1 if scenario == 'no_fix' else 3)
            data = cache.snapshot()
            if scenario == 'stale':
                data['records']['GPS_RAW_INT']['received_monotonic'] -= 5
            if scenario == 'timeout':
                data['records']['HEARTBEAT']['received_monotonic'] -= 5
            position = gps_position()
            if scenario == 'missing':
                position['fix_status'] = -1
            return jsonify(diagnose(data, position, {'connected': scenario != 'mavros', 'received_monotonic': time.monotonic()}))

        server.app.view_functions['gps_diagnostics'] = snapshot
        try:
            server.app.run(host='127.0.0.1', port=5087, threaded=True)
        finally:
            server.sample_storage.close()


if __name__ == '__main__':
    main()

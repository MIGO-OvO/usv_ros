#!/usr/bin/env python3
"""Read-only bridge diagnostics; JSON payloads are never guessed with regex/eval."""
import argparse
import json
import sys


def parse_payload(payload):
    try:
        value = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError('PAYLOAD_JSON_ERROR') from exc
    if not isinstance(value, dict):
        raise ValueError('PAYLOAD_SCHEMA_ERROR: expected object')
    return value


def parse_echo(text):
    import yaml
    if not text.strip():
        raise ValueError('EMPTY_MESSAGE')
    try:
        documents = [value for value in yaml.safe_load_all(text) if value is not None]
    except yaml.YAMLError as exc:
        raise ValueError('OUTER_YAML_ERROR') from exc
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise ValueError('OUTER_SCHEMA_ERROR: expected one String message')
    envelope = documents[0]
    if 'data' not in envelope:
        return parse_payload(text)  # Explicit raw JSON input, not Python dict syntax.
    if not isinstance(envelope['data'], str):
        raise ValueError('OUTER_SCHEMA_ERROR: data must be a string')
    return parse_payload(envelope['data'])


def summary(value):
    return 'bridge_diag: ' + ' '.join('{}={}'.format(key, value.get(key, '?')) for key in (
        'tcp_connected', 'tcp_session', 'fcu_heartbeat_age_s', 'last_complete_write_age_s',
        'reconnect_reason', 'transport', 'mavros_connected', 'tx_total', 'pkt_count'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--echo', action='store_true', help='parse rostopic echo from stdin')
    parser.add_argument('--timeout', type=float, default=12.0)
    args = parser.parse_args()
    try:
        if args.echo:
            value = parse_echo(sys.stdin.read())
        else:
            import rospy
            from std_msgs.msg import String
            rospy.init_node('usv_diagnostics_readonly', anonymous=True, disable_signals=True)
            topic = '/usv/bridge_diagnostics'
            if topic not in dict(rospy.get_published_topics()):
                print('bridge_diag: NO_PUBLISHER')
                return 1
            try:
                message = rospy.wait_for_message(topic, String, timeout=args.timeout)
            except rospy.ROSException:
                print('bridge_diag: WAIT_TIMEOUT')
                return 1
            value = parse_payload(message.data)
        print(summary(value))
        return 0
    except Exception as exc:
        print('bridge_diag: {}'.format(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())

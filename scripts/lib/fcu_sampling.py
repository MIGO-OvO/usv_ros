"""The ArduRover NAV_SCRIPT_TIME sampling exchange (V2_EXTENSION 31021)."""
import struct
from enum import IntEnum

MESSAGE_TYPE = 31021
VERSION = 1
FRAME = struct.Struct('<BBQHHH')


class Message(IntEnum):
    OFFER = 1
    READY = 2
    START = 3
    ACCEPTED = 4
    DONE = 5
    SKIP = 6
    FAILED_LOITER = 7
    FAILED_HOLD = 8
    CANCEL = 9


def encode(message, identity):
    return FRAME.pack(VERSION, message, *identity).ljust(249, b'\0')


def decode(payload):
    version, kind, token, sample_id, waypoint_seq, index = FRAME.unpack_from(bytes(payload))
    if version != VERSION or not token or not sample_id or not waypoint_seq or index >= waypoint_seq:
        raise ValueError('Invalid FCU sampling identity')
    return Message(kind), (token, sample_id, waypoint_seq, index)


def context(identity):
    token, sample_id, seq, index = identity
    return dict(source='fcu', fcu_token=str(token), sample_id=sample_id, waypoint_seq=seq, waypoint_index=index)

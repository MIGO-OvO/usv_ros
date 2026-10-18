import json
import unittest
import yaml

from scripts.bridge_diagnostics import parse_echo, parse_payload, summary


class DiagnosticsParsingTests(unittest.TestCase):
    def test_multiline_unicode_and_nested_json(self):
        value = {'tcp_session': 7, 'reconnect_reason': '接收异常\nEOF', 'transport': {'send_errors': 3}}
        wrapped = yaml.safe_dump({'data': json.dumps(value, ensure_ascii=False)}, width=20)
        self.assertEqual(parse_echo(wrapped + '---\n'), value)
        self.assertEqual(parse_payload(json.dumps(value)), value)
        self.assertEqual(parse_echo(json.dumps(value)), value)
        self.assertIn('tcp_session=7', summary(value))

    def test_distinct_parse_errors(self):
        for text, expected in (('', 'EMPTY_MESSAGE'), ('data: [', 'OUTER_YAML_ERROR'),
                               ('data: 1', 'OUTER_SCHEMA_ERROR'), ('data: bad', 'PAYLOAD_JSON_ERROR'),
                               ('data: "[]"', 'PAYLOAD_SCHEMA_ERROR'), ('data: "null"', 'PAYLOAD_SCHEMA_ERROR')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, expected):
                parse_echo(text)


if __name__ == '__main__':
    unittest.main()

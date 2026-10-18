"""Run the offline suite without the operator-excluded map/tile scenarios."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXCLUDED = ('test_map_', 'test_tile_', 'pollution', 'leaflet', 'offline_tile',
            'map_config', 'map_download', 'map_api', 'map_smoke', 'map_resource',
            'map_runtime', 'tile_cache', 'tile_proxy', 'geojson', 'heatmap', 'surface')


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


if __name__ == '__main__':
    tests = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
    selected = [test for test in tests if not any(term in test.id().lower() for term in EXCLUDED)]
    print('Selected %d; map-related excluded %d' % (len(selected), len(tests) - len(selected)), flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
    sys.exit(not result.wasSuccessful())

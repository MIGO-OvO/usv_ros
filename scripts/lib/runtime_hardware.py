"""Saved Web hardware settings shared with the serial controller at startup."""
import os

CONFIG_FILE = os.path.expanduser('~/usv_ws/config/sampling_config.json')


def hardware_to_pump_runtime_params(hw):
    mapping = {
        'angles': dict(hw.get('i2c_mapping', {})),
        'spectro_channel': int(hw.get('spectro_channel', 2)),
    }
    spectrometer = {
        'enabled': True,
        'auto_start': bool(hw.get('auto_start', False)),
        'ads_address': hw.get('ads_address', '0x40'),
        'mux': hw.get('mux', 'AIN0'),
        'gain': int(hw.get('gain', 1)),
        'vref_mode': hw.get('vref_mode', 'AVDD'),
        'adc_rate': max(20, int(hw.get('adc_rate', 90))),
        'publish_rate': max(20, int(hw.get('publish_rate', 90))),
        'spectro_output_hz': int(hw.get('spectro_output_hz', 10)),
        'continuous_mode': bool(hw.get('continuous_mode', True)),
        'reference_voltage': float(hw.get('reference_voltage', 0.0)),
        'baseline_voltage': float(hw.get('baseline_voltage', 0.0)),
    }
    return mapping, spectrometer, {'enabled': True, 'auto_start': True}

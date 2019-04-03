from typing import NamedTuple, Optional, Any, Mapping

from pynvml import NVMLError, NVML_ERROR_NOT_SUPPORTED, NVML_ERROR_NO_PERMISSION
from ruxit.api.exceptions import ConfigException


class DeviceUtilizationRates(NamedTuple):
    memory_total: float
    memory_used: float
    gpu: float
    memory_controller: float

    def __add__(self, other: "DeviceUtilizationRates") -> "DeviceUtilizationRates":
        memory_total = self.memory_total + other.memory_total
        memory_used = self.memory_used + other.memory_used
        gpu = self.gpu + other.gpu
        memory_controller = self.memory_controller + other.memory_controller
        return DeviceUtilizationRates(memory_total, memory_used, gpu, memory_controller)

    def divide_rates(self, divisor: float) -> "DeviceUtilizationRates":
        gpu = self.gpu / divisor
        memory_controller = self.memory_controller / divisor
        return DeviceUtilizationRates(self.memory_total, self.memory_used, gpu, memory_controller)


def nvml_error_to_string(error: NVMLError) -> str:
    if error.value == NVML_ERROR_NOT_SUPPORTED:
        return "N/A"
    if error.value == NVML_ERROR_NO_PERMISSION:
        return "Access denied"
    else:
        return str(error)


def add_ignoring_none(a: Optional[float], b: Optional[float]) -> float:
    if a is None:
        return b
    elif b is None:
        return a
    else:
        return a + b


def get_bool_param(config, key: str) -> bool:
    # TODO: in plugin.json the type is specified as 'String' not 'boolean', remove workaround once APM-168456 is fixed
    # TODO: a workaround until APM-165439 is fixed
    value = str(config[key]).lower()
    if value == "true":
        return True
    elif value == "false":
        return False
    else:
        raise ConfigException(f"value \"{value}\" specified for {key} is not a valid boolean")


def get_int_param(config: Mapping[str, Any], key: str) -> int:
    # TODO: same as for get_bool_param()
    value = str(config[key])
    try:
        return int(value)
    except ValueError:
        raise ConfigException(f"value \"{value}\" specified for {key} is not a valid integer")


def get_average(average: float, new_sample: float, sample_number: int) -> float:
    # Compute average on the fly
    return average + (new_sample - average) / sample_number

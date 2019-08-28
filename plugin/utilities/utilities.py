from typing import NamedTuple, Optional

from pynvml import NVMLError, NVML_ERROR_NOT_SUPPORTED, NVML_ERROR_NO_PERMISSION

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


def get_average(average: float, new_sample: float, sample_number: int) -> float:
    # Compute average on the fly
    return average + (new_sample - average) / sample_number

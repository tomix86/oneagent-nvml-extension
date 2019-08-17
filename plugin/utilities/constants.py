from typing import Dict, Optional

from pynvml import c_nvmlDevice_t, c_uint

SAMPLES_COUNT: int = 5
SAMPLING_INTERVAL: int = 2

MiB: int = 1024 * 1024

Pid = c_uint
MemUsage = Optional[float]
GPUProcesses = Dict[Pid, MemUsage]
DeviceHandle = c_nvmlDevice_t

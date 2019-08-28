from time import sleep
from typing import Dict, Tuple, List, KeysView, Any

from pynvml import *
from ruxit.api.base_plugin import BasePlugin
from ruxit.api.data import PluginMeasurement
from ruxit.api.selectors import ExplicitPgiSelector
from ruxit.api.exceptions import ConfigException

from utilities.constants import DeviceHandle, MiB, GPUProcesses, MemUsage, Pid, SAMPLES_COUNT, SAMPLING_INTERVAL
from utilities.utilities import DeviceUtilizationRates, nvml_error_to_string, get_average, get_bool_param, add_ignoring_none

"""
For documentation see README.md
"""


class NVMLPlugin(BasePlugin):
    devices_count: int = 0
    enable_debug_log: bool = False

    def raise_nvml_error(self, error: NVMLError) -> None:
        self.logger.warning(nvml_error_to_string(error))
        raise ConfigException(f"unexpected NVML error: {str(error)}") from error

    def log_debug(self, message: str) -> None:
        if self.enable_debug_log:
            self.logger.info("[DEBUG]: " + message)

    def sample_utilization_rates(self, handle: DeviceHandle) -> DeviceUtilizationRates:
        memory = nvmlDeviceGetMemoryInfo(handle)
        total = memory.total / MiB
        used = memory.used / MiB

        utilization = nvmlDeviceGetUtilizationRates(handle)
        utilization_gpu = utilization.gpu
        utilization_memory = utilization.memory
        self.log_debug(f"Sampled utilization rates: {used:.2f} MiB, {utilization_gpu}%, {utilization_memory}%")
        return DeviceUtilizationRates(total, used, utilization_gpu, utilization_memory)

    def set_host_results(self, utilization_rates: DeviceUtilizationRates, gpu_processes_count: int) -> None:
        def set_result(key: str, value: Any):
            self.results_builder.add_absolute_result(PluginMeasurement(key=key, value=value))

        set_result("gpu_mem_used", utilization_rates.memory_used)
        set_result("gpu_mem_total", utilization_rates.memory_total)
        set_result("gpu_mem_percentage_used", 100 * utilization_rates.memory_used / utilization_rates.memory_total)
        set_result("gpu_utilization", utilization_rates.gpu)
        set_result("gpu_memory_controller_utilization", utilization_rates.memory_controller)
        set_result("gpu_processes_count", gpu_processes_count)

    def set_pgi_results(self, pgi_id: int, aggregated_mem_usage: MemUsage) -> None:
        if aggregated_mem_usage is not None:
            measurement = PluginMeasurement(key="gpu_mem_used_by_pgi", value=aggregated_mem_usage, entity_selector=ExplicitPgiSelector(pgi_id))
            self.results_builder.add_absolute_result(measurement)
        else:  # Note: if we don't send these metrics it won't appear on the WebUI, this is expected (otherwise we would display a timeseries that does not make any sense)
            self.log_debug(f"Skipping gpu_mem_used_by_pgi metric for PGIID={pgi_id:02x} as the memory reading is empty")

    def sample_processes_information(self, handle: DeviceHandle) -> GPUProcesses:
        try:
            # List processes with a compute context (e.g. CUDA applications)
            compute_processes = nvmlDeviceGetComputeRunningProcesses(handle)
            # List processes with a graphics context (eg. applications using OpenGL, DirectX)
            graphics_processes = nvmlDeviceGetGraphicsRunningProcesses(handle)
            # Note: a single process may have both the graphics and compute context active at the same time
        except NVMLError as err:
            self.logger.warning(nvml_error_to_string(err))
            return {}

        processes = {}
        for p in compute_processes + graphics_processes:
            mem = p.usedGpuMemory
            processes[p.pid] = None if mem is None else mem / MiB

        self.log_debug(f"Sampled processes ({len(processes)}): {processes}")
        return processes

    def sample_devices_information(self) -> List[Tuple[GPUProcesses, DeviceUtilizationRates]]:
        data_for_devices = []
        for idx in range(self.devices_count):
            self.log_debug(f"Sampling GPU #{idx}")
            handle = nvmlDeviceGetHandleByIndex(idx)
            processes_info = self.sample_processes_information(handle)
            util_rates = self.sample_utilization_rates(handle)
            data_for_devices.append((processes_info, util_rates))

        return data_for_devices

    def get_gpus_info(self) -> List[Tuple[GPUProcesses, DeviceUtilizationRates]]:
        # Gather first sample
        data_for_devices = self.sample_devices_information()

        for sample_number in range(1, SAMPLES_COUNT):
            new_sample = self.sample_devices_information()
            for idx in range(0, len(data_for_devices)):
                previous = data_for_devices[idx]
                current = new_sample[idx]
                # We're only interested in processes that appear in all the samples
                processes_info = {k: v for k, v in previous[0].items() if k in current[0]}
                for pid in processes_info:
                    if processes_info[pid] is None:
                        continue
                    processes_info[pid] = get_average(processes_info[pid], current[0][pid], sample_number + 1)

                utilization = DeviceUtilizationRates(previous[1].memory_total,
                                                     get_average(previous[1].memory_used, current[1].memory_used, sample_number + 1),
                                                     get_average(previous[1].gpu, current[1].gpu, sample_number + 1),
                                                     get_average(previous[1].memory_controller, current[1].memory_controller, sample_number + 1))
                data_for_devices[idx] = (processes_info, utilization)

            sleep(SAMPLING_INTERVAL)

        for device_data in data_for_devices:
            self.log_debug(f"Device info:")
            percentage_used = device_data[1].memory_used / device_data[1].memory_total
            self.log_debug(f"...Memory usage [MiB]: {device_data[1].memory_used:.2f} / {device_data[1].memory_total:.0f} ({percentage_used:.0%})")
            self.log_debug(f"...GPU utilization: {device_data[1].gpu}%")
            self.log_debug(f"...Memory controller utilization: {device_data[1].memory_controller}%")
            self.log_debug(f"...Number of processes using the GPU: {len(device_data[0])}")
            self.log_debug(f"...PIDs and memory usage of processes using the GPU: {device_data[0]}")
        return data_for_devices

    def get_monitored_pgis_list(self, gpu_processes: KeysView[Pid]) -> Dict[int, object]:
        monitored_pgis = []

        pgi_list = self.find_all_processes(lambda process: process.pid in gpu_processes)
        for entry in pgi_list:
            pgi = entry[0]
            pid = entry[1].pid
            name = entry[1].process_name
            self.log_debug(f"{name} (pid: {pid}) from {pgi.group_name} process group"
                           f"(PGIID={pgi.group_instance_id:02x}, type={pgi.process_type}) is using the GPU")
            monitored_pgis.append(pgi)

        unique_pgis = {}
        for pgi in monitored_pgis:
            unique_pgis[pgi.group_instance_id] = pgi

        return unique_pgis

    def generate_metrics_for_pgis(self, gpu_processes_mem_usage: GPUProcesses, utilization_rates: DeviceUtilizationRates, monitored_pgis: Dict) -> None:
        gpu_processes_count = len(gpu_processes_mem_usage)

        self.logger.info(f"Sending host metrics")
        self.set_host_results(utilization_rates, gpu_processes_count)

        for pgi in monitored_pgis.values():
            self.log_debug(f"Processing '{pgi.group_name}' process group...")
            aggregated_mem_usage = None
            for process in pgi.processes:
                if process.pid not in gpu_processes_mem_usage:
                    continue

                memory_usage = gpu_processes_mem_usage[process.pid]
                self.log_debug(f"Adding memory usage ({memory_usage}) of process {process.pid} to aggregated counter")
                aggregated_mem_usage = add_ignoring_none(aggregated_mem_usage, memory_usage)

            pgi_id = pgi.group_instance_id
            self.log_debug(f"GPU mem usage [MiB] for PGIID={pgi_id:02x}: '{aggregated_mem_usage}'")
            self.logger.info(f"Sending metrics for '{pgi.group_name}' process group (PGIID={pgi_id:02x}, type={pgi.process_type})")
            self.set_pgi_results(pgi_id, aggregated_mem_usage)

    def detect_devices(self) -> None:
        self.devices_count = nvmlDeviceGetCount()
        for i in range(self.devices_count):
            handle = nvmlDeviceGetHandleByIndex(i)
            device_name = nvmlDeviceGetName(handle).decode("UTF-8")
            self.logger.info(f"Device nr. {i}: '{device_name}'")

    def aggregate_data_from_multiple_devices(self, data: List[Tuple[GPUProcesses, DeviceUtilizationRates]]) -> Tuple[GPUProcesses, DeviceUtilizationRates]:
        gpu_processes_mem_usage = {}
        summed_utilization_rates = DeviceUtilizationRates(0, 0, 0, 0)
        for device_info in data:
            self.log_debug(f"Aggregating device data: {device_info}")
            for pid, memory in device_info[0].items():
                gpu_processes_mem_usage[pid] = add_ignoring_none(gpu_processes_mem_usage.get(pid), memory)

            summed_utilization_rates += device_info[1]

        utilization_rates = summed_utilization_rates.divide_rates(len(data))
        self.log_debug(f"Aggregated device data: {utilization_rates}, processes count: {len(gpu_processes_mem_usage)}")
        return gpu_processes_mem_usage, utilization_rates

    def initialize(self, **kwargs) -> None:
        try:
            nvmlInit()
            driver_version = nvmlSystemGetDriverVersion().decode("UTF-8")
            nvml_version = nvmlSystemGetNVMLVersion().decode("UTF-8")
            self.logger.info(f"NVML initialized, driver version: {driver_version}, NVML version: {nvml_version}")
            self.detect_devices()
        except NVMLError as error:
            self.raise_nvml_error(error)

    def close(self, **kwargs) -> None:
        try:
            nvmlShutdown()
            self.logger.info(f"NVML shut down")
        except NVMLError as error:
            self.raise_nvml_error(error)

    def query(self, **kwargs) -> None:
        config = kwargs["config"]
        self.enable_debug_log = get_bool_param(config, "enable_debug_log")

        try:
            data_for_devices = self.get_gpus_info()
            gpu_processes_mem_usage, utilization_rates = self.aggregate_data_from_multiple_devices(data_for_devices)
            monitored_pgis = self.get_monitored_pgis_list(gpu_processes_mem_usage.keys())
            self.generate_metrics_for_pgis(gpu_processes_mem_usage, utilization_rates, monitored_pgis)
        except NVMLError as error:
            self.raise_nvml_error(error)

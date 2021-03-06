# Copyright 2014 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Records the time required to boot a cluster of VMs."""

from perfkitbenchmarker import configs
from perfkitbenchmarker import vm_util
from perfkitbenchmarker import sample


BENCHMARK_NAME = 'cluster_boot'
BENCHMARK_CONFIG = """
cluster_boot:
  description: >
      Create a cluster, record all times to boot.
      Specify the cluster size with --num_vms.
  vm_groups:
    default:
      vm_spec: *default_single_core
      vm_count: null
"""


def GetConfig(user_config):
    return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(unused_benchmark_spec):
    pass


def GetTimeToBoot(vms):
    """Creates Samples for the boot time of a list of VMs.

    The boot time is the time difference from before the VM is created to when
    the VM is responsive to SSH commands.

    Args:
      vms: List of BaseVirtualMachine subclasses.

    Returns:
      List of Samples containing the boot time.
    """
    def _GetTimeToBoot(vm, vm_index):
        metadata = {'num_cpus': vm.num_cpus, 'machine_instance': vm_index,
                    'num_vms': len(vms), 'os_type': vm.OS_TYPE}
        assert vm.bootable_time
        assert vm.create_start_time
        assert vm.bootable_time >= vm.create_start_time
        value = vm.bootable_time - vm.create_start_time
        return sample.Sample('Boot Time', value, 'seconds', metadata)
    params = [((vm, i), {}) for i, vm in enumerate(vms)]
    samples = vm_util.RunThreaded(_GetTimeToBoot, params)
    assert len(samples) == len(vms)
    return samples


def Run(benchmark_spec):
    """Measure the boot time for all VMs.

    Args:
      benchmark_spec: The benchmark specification. Contains all data that is
          required to run the benchmark.

    Returns:
      An empty list (all boot samples will be added later).
    """
    return []


def Cleanup(unused_benchmark_spec):
    pass

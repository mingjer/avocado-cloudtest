#!/usr/bin/env python
# Copyright 2013 Mirantis, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


import functools
import logging
import traceback

import neutronclient.common.exceptions as neutron_exc

from fuel_health.common.utils.data_utils import rand_name
import fuel_health.nmanager
import fuel_health.test

LOG = logging.getLogger(__name__)


def check_compute_nodes():
    """Decorator that checks a compute existence in the environment.

    Decorated tests must be skipped if there are no compute nodes.
    """
    def decorator(func):

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if (not self.config.compute.compute_nodes and
                    not self.config.compute.use_vcenter):
                self.skipTest('There are no compute nodes in the environment. '
                              'Test skipped.')
            return func(self, *args, **kwargs)
        return wrapper
    return decorator


class CeilometerBaseTest(fuel_health.nmanager.PlatformServicesBaseClass):

    @classmethod
    def setUpClass(cls):
        super(CeilometerBaseTest, cls).setUpClass()
        if cls.manager.clients_initialized:
            cls.wait_interval = cls.config.compute.build_interval
            cls.wait_timeout = cls.config.compute.build_timeout
            cls.objects_for_delete = []
            cls.nova_notifications = ['memory', 'vcpus', 'disk.root.size',
                                      'disk.ephemeral.size']
            cls.neutron_network_notifications = ['network', 'network.create',
                                                 'network.update']
            cls.neutron_subnet_notifications = ['subnet', 'subnet.create',
                                                'subnet.update']
            cls.neutron_port_notifications = ['port', 'port.create',
                                              'port.update']
            cls.neutron_router_notifications = ['router', 'router.create',
                                                'router.update']
            cls.neutron_floatingip_notifications = ['ip.floating.create',
                                                    'ip.floating.update']
            cls.volume_events = [
                'volume.create.start', 'volume.create.end',
                'volume.delete.start', 'volume.delete.end',
                'volume.update.start', 'volume.update.end',
                'volume.resize.start', 'volume.resize.end',
                'volume.attach.start', 'volume.attach.end',
                'volume.detach.start', 'volume.detach.end']
            cls.snapshot_events = [
                'snapshot.create.start', 'snapshot.create.end',
                'snapshot.delete.start', 'snapshot.delete.end']
            cls.glance_notifications = ['image.size', 'image.update',
                                        'image.upload', 'image.download',
                                        'image.serve', 'image.delete']
            cls.swift_notifications = ['storage.objects.incoming.bytes',
                                       'storage.objects.outgoing.bytes',
                                       'storage.api.request']
            cls.heat_notifications = ['stack.create', 'stack.update',
                                      'stack.delete', 'stack.resume',
                                      'stack.suspend']
            cls.keystone_user_notifications = [
                'identity.user.created', 'identity.user.deleted',
                'identity.user.updated']
            cls.keystone_role_notifications = [
                'identity.role.created', 'identity.role.updated',
                'identity.role.deleted']
            cls.keystone_role_assignment_notifications = [
                'identity.role_assignment.created',
                'identity.role_assignment.deleted']
            cls.keystone_project_notifications = [
                'identity.project.created', 'identity.project.updated',
                'identity.project.deleted']
            cls.keystone_group_notifications = [
                'identity.group.created', 'identity.group.updated',
                'identity.group.deleted']
            cls.keystone_trust_notifications = [
                'identity.trust.created',
                'identity.trust.deleted']
            cls.sahara_cluster_notifications = [
                'cluster.create', 'cluster.update', 'cluster.delete']

    def setUp(self):
        super(CeilometerBaseTest, self).setUp()
        self.check_clients_state()
        if not self.ceilometer_client:
            self.skipTest('Ceilometer is unavailable.')

    def create_server(self, name, **kwargs):
        server = self._create_server(self.compute_client, name, **kwargs)
        self.addCleanup(
            self.delete_resource,
            delete_method=lambda: self.compute_client.servers.delete(
                server.id),
            get_method=lambda: self.compute_client.servers.get(server.id))
        return server

    def create_alarm(self, **kwargs):
        """This method provides creation of alarm."""
        if 'name' in kwargs:
            kwargs['name'] = rand_name(kwargs['name'])
        alarm = self.ceilometer_client.alarms.create(**kwargs)
        self.objects_for_delete.append((self.ceilometer_client.alarms.delete,
                                        alarm.alarm_id))
        return alarm

    def get_state(self, alarm_id):
        """This method provides getting state."""
        return self.ceilometer_client.alarms.get_state(alarm_id=alarm_id)

    def verify_state(self, alarm_id, state):
        """This method provides getting state."""
        alarm_state_resp = self.get_state(alarm_id)
        if not alarm_state_resp == state:
            self.fail('State was not setted')

    def wait_for_resource_status(self, resource_client, resource_id, status):
        self.status_timeout(resource_client, resource_id, status)

    def wait_for_alarm_status(self, alarm_id, status=None):
        """The method is a customization of test.status_timeout()."""

        def check_status():
            try:
                alarm_state_resp = self.get_state(alarm_id)
            except Exception:
                alarm_state_resp = None
            if status:
                if alarm_state_resp == status:
                    return True
            elif alarm_state_resp == 'alarm' or 'ok':
                return True  # All good.
            LOG.debug("Waiting for state to get alarm status.")

        if not fuel_health.test.call_until_true(check_status, 1000, 10):
            actual_status = self.get_state(alarm_id)
            self.fail(
                "Timed out waiting to become alarm status. "
                "Expected status:{exp_status}; "
                "Actual status:{act_status}".format(
                    exp_status=status if status else "'alarm' or 'ok'",
                    act_status=actual_status))

    def wait_for_object_sample(self, obj, query, ceilo_obj_type):
        """This method is to wait for sample to add it to database.
        query example:
        query=[
        {'field':'resource',
        'op':'eq',
        'value':'000e6838-471b-4a14-8da6-655fcff23df1'
        }]
        """
        kwargs = {"q": query}
        if ceilo_obj_type == 'sample':
            method = self.ceilometer_client.samples.list
            kwargs["meter_name"] = obj
        elif ceilo_obj_type == "event":
            query.append({'field': 'event_type', 'op': 'eq', 'value': obj})
            method = self.ceilometer_client.events.list

        def check_status():
            try:
                body = method(**kwargs)
            except Exception:
                body = None
            if body:
                return True

        if not fuel_health.test.call_until_true(check_status, 600, 10):
            self.fail(
                "Timed out waiting for object: {obj} "
                "with query:{query}".format(obj=obj, query=query))

    def wait_for_statistic_of_metric(self, meter_name, query=None,
                                     period=None):
        """The method is a customization of test.status_timeout()."""

        def check_status():
            stat_state_resp = self.ceilometer_client.statistics.list(
                meter_name, q=query, period=period)
            if len(stat_state_resp) > 0:
                return True  # All good.
            LOG.debug("Waiting for while metrics will available.")

        if not fuel_health.test.call_until_true(check_status, 600, 10):

            self.fail("Timed out waiting to become alarm")
        else:
            return self.ceilometer_client.statistics.list(meter_name, q=query,
                                                          period=period)

    def wait_for_ceilo_objects(self, object_list, query, ceilo_obj_type):
        for obj in object_list:
            self.wait_for_object_sample(obj, query, ceilo_obj_type)

    def create_image_sample(self, image_id):
        sample = self.ceilometer_client.samples.create(
            resource_id=image_id, counter_name='image', counter_type='delta',
            counter_unit='image', counter_volume=1,
            resource_metadata={'user': 'example_metadata'})
        return sample

    def get_samples_count(self, meter_name, query):
        return self.ceilometer_client.statistics.list(
            meter_name=meter_name, q=query)[0].count

    def wait_samples_count(self, meter_name, query, count):

        def check_count():
            new_count = self.get_samples_count(meter_name, query)
            return new_count > count

        if not fuel_health.test.call_until_true(check_count, 60, 1):
            self.fail('Count of samples list isn\'t '
                      'greater than expected value')

    def check_event_type(self, event_type):
        event_list = [event.event_type for event
                      in self.ceilometer_client.event_types.list()]
        if event_type not in event_list:
            self.fail('"{event_type}" not found in event type list.'.format(
                event_type=event_type))

    def check_event_message_id(self, events_list, instance_id):
        for event in events_list:
            try:
                if next(x['value'] for x in event.traits
                        if x['name'] == "instance_id") == instance_id:
                    return event.message_id
            except StopIteration:
                self.fail('Trait "instance_id" not found in trait list.')
        self.fail('No events found for "{instance_id}" instance.'.format(
            instance_id=instance_id))

    def check_traits(self, event_type, traits):
        trait_desc = [desc.name for desc in
                      self.ceilometer_client.trait_descriptions.list(
                          event_type)]
        for trait in traits:
            if trait not in trait_desc:
                self.fail('Trait "{trait}" not found in trait list.'.format(
                    trait=trait))

    def identity_helper(self):
        user_pass = rand_name("ceilo-user-pass")
        user_name = rand_name("ceilo-user-update")
        tenant_name = rand_name("ceilo-tenant-update")
        tenant = self.identity_client.tenants.create(rand_name("ceilo-tenant"))
        self.objects_for_delete.append((
            self.identity_client.tenants.delete, tenant))
        self.identity_client.tenants.update(tenant.id, name=tenant_name)
        user = self.identity_client.users.create(
            rand_name("ceilo-user"), user_pass, tenant.id)
        self.objects_for_delete.append((
            self.identity_client.users.delete, user))
        self.identity_client.users.update(user, name=user_name)
        role = self.identity_v3_client.roles.create(rand_name("ceilo-role"))
        self.identity_v3_client.roles.update(
            role, user=user.id, project=tenant.id)
        self.identity_v3_client.roles.grant(
            role, user=user.id, project=tenant.id)
        self.objects_for_delete.append((
            self.identity_client.roles.delete, role))
        user_client = self.manager_class()._get_identity_client(
            user_name, user_pass, tenant_name, 3)
        trust = user_client.trusts.create(
            self.identity_v3_client.user_id, user.id, [role.name], tenant.id)
        self.objects_for_delete.append((user_client.trusts.delete, trust))
        group = self.identity_v3_client.groups.create(rand_name("ceilo-group"))
        self.objects_for_delete.append((
            self.identity_v3_client.groups.delete, group))
        self.identity_v3_client.groups.update(
            group, name=rand_name("ceilo-group-update"))
        self.identity_v3_client.groups.delete(group)
        user_client.trusts.delete(trust)
        self.identity_v3_client.roles.revoke(
            role, user=user.id, project=tenant.id)
        self.identity_client.roles.delete(role)
        self.identity_client.users.delete(user)
        self.identity_client.tenants.delete(tenant)
        return tenant, user, role, group, trust

    def neutron_helper(self):
        net = self.neutron_client.create_network(
            {"network": {"name": rand_name("ceilo-net")}})["network"]
        self.addCleanup(self.cleanup_resources,
                        [(self.neutron_client.delete_network, net["id"])])
        self.neutron_client.update_network(
            net["id"], {"network": {"name": rand_name("ceilo-net-update")}})

        subnet = self.neutron_client.create_subnet(
            {"subnet": {"name": rand_name("ceilo-subnet"),
                        "network_id": net["id"],
                        "ip_version": 4,
                        "cidr": "10.0.7.0/24"}})["subnet"]
        self.addCleanup(self.cleanup_resources,
                        [(self.neutron_client.delete_subnet, subnet["id"])])
        self.neutron_client.update_subnet(
            subnet["id"], {"subnet": {"name": rand_name("ceilo-subnet")}})

        port = self.neutron_client.create_port({
            "port": {"name": rand_name("ceilo-port"),
                     "network_id": net["id"]}})['port']
        self.addCleanup(self.cleanup_resources,
                        [(self.neutron_client.delete_port, port["id"])])
        self.neutron_client.update_port(
            port["id"], {"port": {"name": rand_name("ceilo-port-update")}})

        router = self.neutron_client.create_router(
            {"router": {"name": rand_name("ceilo-router")}})['router']
        self.addCleanup(self.cleanup_resources,
                        [(self.neutron_client.delete_router, router["id"])])
        self.neutron_client.update_router(
            router["id"],
            {"router": {"name": rand_name("ceilo-router-update")}})

        external_network = self.find_external_network()
        try:
            body = {
                "floatingip": {
                    "floating_network_id": external_network["id"]
                }
            }
            fl_ip = self.neutron_client.create_floatingip(body)["floatingip"]
        except neutron_exc.IpAddressGenerationFailureClient:
            self.fail('No more IP addresses available on external network.')
        self.addCleanup(self.cleanup_resources,
                        [(self.neutron_client.delete_floatingip, fl_ip["id"])])
        self.neutron_client.update_floatingip(
            fl_ip["id"], {"floatingip": {"port_id": None}})

        self.neutron_client.delete_floatingip(fl_ip["id"])
        self.neutron_client.delete_router(router["id"])
        self.neutron_client.delete_port(port["id"])
        self.neutron_client.delete_subnet(subnet["id"])
        self.neutron_client.delete_network(net["id"])

        return net, subnet, port, router, fl_ip

    def sahara_helper(self, image_id, plugin_name, hadoop_version):
        #  Find flavor id for sahara instances
        flavor_id = next(
            flavor.id for flavor in
            self.compute_client.flavors.list() if flavor.name == 'm1.small')

        private_net_id, floating_ip_pool = self.create_network_resources()
        #  Create json for node group
        node_group = {'name': 'all-in-one',
                      'flavor_id': flavor_id,
                      'node_processes': ['nodemanager', 'datanode',
                                         'resourcemanager', 'namenode',
                                         'historyserver'],
                      'count': 1,
                      'auto_security_group': True}
        if floating_ip_pool:
            node_group['floating_ip_pool'] = floating_ip_pool

        #  Create json for Sahara cluster
        cluster_json = {'name': rand_name("ceilo-cluster"),
                        'plugin_name': plugin_name,
                        'hadoop_version': hadoop_version,
                        'default_image_id': image_id,
                        'cluster_configs': {'HDFS': {'dfs.replication': 1}},
                        'node_groups': [node_group],
                        'net_id': private_net_id}

        #  Create Sahara cluster
        cluster = self.sahara_client.clusters.create(**cluster_json)
        self.addCleanup(
            self.delete_resource,
            delete_method=lambda: self.sahara_client.clusters.delete(
                cluster.id),
            get_method=lambda: self.sahara_client.clusters.get(cluster.id))

        #  Wait for change cluster state for metric: cluster.update
        def check_status():
            cluster_state = self.sahara_client.clusters.get(cluster.id).status
            return cluster_state in ['Waiting', 'Active', 'Error']
        fuel_health.test.call_until_true(check_status, 300, 1)

        #  Delete cluster
        self.sahara_client.clusters.delete(cluster.id)

        return cluster

    def glance_helper(self):
        image = self.glance_client.images.create(
            name=rand_name('ostf-ceilo-image'))
        self.objects_for_delete.append((self.glance_client.images.delete,
                                        image.id))
        self.glance_client.images.update(image.id, data='data',
                                         disk_format='qcow2',
                                         container_format='bare')
        self.glance_client.images.upload(image.id, 'upload_data')
        self.glance_client.images.data(image.id)
        self.glance_client.images.delete(image.id)
        return image

    def volume_helper(self, instance):
        device = '/dev/vdb'
        #   Create a volume
        volume = self.volume_client.volumes.create(
            name=rand_name('ost1_test-ceilo-volume'), size=1)
        self.addCleanup(
            self.delete_resource,
            delete_method=lambda: self.volume_client.volumes.delete(volume),
            get_method=lambda: self.volume_client.volumes.get(volume.id))
        #   Wait for "Available" status of the volume
        self.wait_for_resource_status(
            self.volume_client.volumes, volume.id, 'available')
        #   Resize the volume
        self.volume_client.volumes.extend(volume, 2)
        self.wait_for_resource_status(
            self.volume_client.volumes, volume.id, 'available')
        #   Create a volume snapshot
        snapshot = self.volume_client.volume_snapshots.create(
            volume.id, name=rand_name('ost1_test-'))
        self.addCleanup(
            self.delete_resource,
            delete_method=lambda: self.volume_client.volume_snapshots.delete(
                snapshot),
            get_method=lambda: self.volume_client.volume_snapshots.get(
                snapshot.id))
        #   Wait for "Available" status of the snapshot
        self.wait_for_resource_status(
            self.volume_client.volume_snapshots, snapshot.id, 'available')
        #   Update the volume name
        self.volume_client.volumes.update(volume, name="ost1_test-update")
        #   Attach the volume to the instance
        self.volume_client.volumes.attach(volume.id, instance.id, device)
        #   Detach the volume from the instance
        self.volume_client.volumes.detach(volume.id)
        #   Delete the volume snapshot
        self.delete_resource(
            delete_method=lambda: self.volume_client.volume_snapshots.delete(
                snapshot),
            get_method=lambda: self.volume_client.volume_snapshots.get(
                snapshot.id))
        #   Delete the volume
        self.delete_resource(
            delete_method=lambda: self.volume_client.volumes.delete(volume),
            get_method=lambda: self.volume_client.volumes.get(volume.id))
        return volume, snapshot

    @staticmethod
    def cleanup_resources(object_list):
        for method, resource in object_list:
            try:
                method(resource)
            except Exception:
                LOG.debug(traceback.format_exc())

    @classmethod
    def tearDownClass(cls):
        if cls.manager.clients_initialized:
            cls.cleanup_resources(cls.objects_for_delete)
        super(CeilometerBaseTest, cls).tearDownClass()
    """
    def create_image_sample(self, image_id):
        sample = self.ceilometer_client.samples.create(
            resource_id=image_id, counter_name='image', counter_type='delta',
            counter_unit='image', counter_volume=1,
            resource_metadata={'user': 'example_metadata'})
        return sample
    """
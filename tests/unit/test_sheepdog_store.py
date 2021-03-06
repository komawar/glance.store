# Copyright 2013 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import StringIO

import mock

from glance.store._drivers import sheepdog
from glance.store.openstack.common import processutils
from glance.store.tests import base


class TestSheepdogStore(base.StoreBaseTest):

    def setUp(self):
        """Establish a clean test environment"""
        super(TestSheepdogStore, self).setUp()

        def _fake_execute(*cmd, **kwargs):
            pass

        self.config(default_store='sheepdog',
                    group='glance_store')

        execute = mock.patch.object(processutils, 'execute').start()
        execute.side_effect = _fake_execute
        self.addCleanup(execute.stop)
        self.store = sheepdog.Store(self.conf)

    def test_cleanup_when_add_image_exception(self):
        called_commands = []

        def _fake_run_command(command, data, *params):
            called_commands.append(command)

        with mock.patch.object(sheepdog.SheepdogImage, '_run_command') as cmd:
            cmd.side_effect = _fake_run_command
            data = StringIO.StringIO('xx')
            self.store.add('fake_image_id', data, 2)
            self.assertEqual(called_commands, ['list -r', 'create', 'write'])

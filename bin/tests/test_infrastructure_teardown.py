"""
Unit test for infrastructure_teardown.py
"""

import unittest
import logging
from mock import patch, call, MagicMock
from testfixtures import LogCapture

import infrastructure_teardown


class TestInfrastructureTeardown(unittest.TestCase):
    """ Test suite for infrastructure_teardown.py """
    def setUp(self):
        self.os_environ = {'TERRAFORM': 'test/path/terraform'}

    @patch('infrastructure_teardown.subprocess.check_call')
    @patch('infrastructure_teardown.glob.glob')
    @patch('infrastructure_teardown.os')
    def test_destroy_resources(self, mock_os, mock_glob, mock_check_call):
        """ Test infrastructure_teardown.destroy_resources """
        mock_os.path.dirname.return_value = 'teardown/script/path'
        mock_os.environ.__getitem__.side_effect = self.os_environ.__getitem__
        mock_os.environ.__contains__.side_effect = self.os_environ.__contains__
        mock_os.getcwd.return_value = 'previous/directory'
        mock_os.path.isfile.return_value = True
        mock_os.path.exists.return_value = False  # The /data/infrastructure_provisioning check
        mock_glob.return_value = True

        infrastructure_teardown.destroy_resources()

        mock_glob.assert_called_with('teardown/script/path/provisioned.*')
        chdir_calls = [call('teardown/script/path'), call('previous/directory')]
        mock_os.chdir.assert_has_calls(chdir_calls)
        mock_os.path.isfile.assert_called_with('cluster.json')
        mock_check_call.assert_called_with(
            [self.os_environ['TERRAFORM'], 'destroy', '-var-file=cluster.json', '-auto-approve'])

    @patch('infrastructure_teardown.glob.glob')
    @patch('infrastructure_teardown.os')
    def test_destroy_resources_no_cluster_json(self, mock_os, mock_glob):
        """ Test infrastructure_teardown.destroy_resources when there is no cluster.json file """
        mock_os.path.dirname.return_value = 'teardown/script/path'
        mock_os.environ.__getitem__.side_effect = self.os_environ.__getitem__
        mock_os.environ.__contains__.side_effect = self.os_environ.__contains__
        mock_os.getcwd.return_value = 'previous/directory'
        mock_os.path.isfile.return_value = False
        mock_glob.return_value = True
        with LogCapture(level=logging.CRITICAL) as critical:
            with self.assertRaises(UserWarning):

                infrastructure_teardown.destroy_resources()

            critical.check(
                ('infrastructure_teardown', 'CRITICAL',
                 'In infrastructure_teardown.py and cluster.json does not exist. Giving up.'))
        mock_glob.assert_called_with('teardown/script/path/provisioned.*')
        chdir_calls = [call('teardown/script/path'), call('previous/directory')]
        mock_os.chdir.assert_has_calls(chdir_calls)
        mock_os.path.isfile.assert_called_with('cluster.json')

    @patch('common.atlas_setup.AtlasSetup')
    def test_destroy_atlas_resources(self, mock_atlas_setup):
        mock_atlas = MagicMock(name='atlas', autospec=True)
        mock_atlas_setup.return_value = mock_atlas

        infrastructure_teardown.destroy_atlas_resources()

        mock_atlas_setup.assert_called()
        mock_atlas.destroy.assert_called()


if __name__ == '__main__':
    unittest.main()

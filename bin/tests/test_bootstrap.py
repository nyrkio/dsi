"""
Unit tests for 'bootstrap.py'.
"""

from __future__ import print_function

import copy
import logging
import os
import shutil
import subprocess
import unittest

import ruamel.yaml as yaml
from mock import patch
from testfixtures import LogCapture

import bootstrap
from common.config import ConfigDict
import test_lib.structlog_for_test as structlog_for_test


class TestBootstrap(unittest.TestCase):
    """
    Test suite for bootstrap.py.
    """

    TERRAFORM_CONFIG = {
        'terraform': './terraform',
        'terraform_version_check': 'Terraform v0.9.11',
        'production': False
    }

    def setUp(self):
        """
        Running setUp to allow for tearDown
        """
        # Setup logging so that structlog uses stdlib, and LogCapture works
        structlog_for_test.setup_logging()
        self.directory = "WORK"

    def tearDown(self):
        """
        Cleaning up directories and files
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap.yml')
        bootstrap2_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap2.yml')
        test_override_path = os.path.dirname(os.path.abspath(__file__))
        paths = [
            'test_dsipath', 'test_directory', 'test_credentials', 'testdir', 'test_old_dir',
            'test_new_dir', 'test_cred_path'
        ]
        for path in paths:
            try:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
                shutil.rmtree(path)
            except OSError:
                pass
        try:
            os.remove(bootstrap_path)
        except OSError:
            pass
        try:
            os.remove(bootstrap2_path)
        except OSError:
            pass
        try:
            os.remove(os.path.join(test_override_path, 'overrides.yml'))
        except OSError:
            pass

    def test_parse_command_line_no_args(self):
        """
        Testing for parse_command_line (no args), modifying config
        """
        expected_config = {'directory': '.'}
        test_config = {}
        test_config = bootstrap.parse_command_line(test_config, [])
        self.assertEqual(test_config, expected_config)

    def test_parse_command_line_all_args(self):
        """
        Testing for parse_command_line (all args given), modifying config
        """
        args = [
            '--directory', 'test_directory', '--debug', '--bootstrap-file', './test/bootstrap.yml',
            '--log-file', 'log.txt', '--verbose'
        ]
        master_config = {}
        master_config['directory'] = 'test_directory'
        master_config['bootstrap_file'] = './test/bootstrap.yml'
        test_config = {}
        test_config = bootstrap.parse_command_line(test_config, args)
        self.assertEqual(test_config, master_config)

    def test_parse_command_line_all_args_alternate(self):
        """
        Testing for parse_command_line (all alt cmds), modifying config
        """
        args = [
            '--directory', 'test_directory', '-d', '-b', './test/bootstrap.yml', '--log-file',
            'log.txt', '-v'
        ]
        master_config = {}
        master_config['directory'] = 'test_directory'
        master_config['bootstrap_file'] = './test/bootstrap.yml'
        test_config = {}
        test_config = bootstrap.parse_command_line(test_config, args)
        self.assertEqual(test_config, master_config)
        try:
            os.remove('log.txt')
        except OSError:
            pass

    def test_copy_config_files(self):
        """
        Testing copy_config_files moves between dummy directories
        """
        test_config = {}
        test_dsipath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_dsipath')
        test_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_directory')
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'infrastructure_provisioning'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'mongodb_setup'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'test_control'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'workload_setup'))
        os.mkdir(test_directory)
        open(
            os.path.join(test_dsipath, 'configurations', 'infrastructure_provisioning',
                         'infrastructure_provisioning.single.yml'), 'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'mongodb_setup', 'mongodb_setup.FIII.yml'),
            'w').close()
        open(os.path.join(test_dsipath, 'configurations', 'test_control', 'test_control.ycsb.yml'),
             'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'workload_setup',
                         'workload_setup.ycsb.yml'), 'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'workload_setup',
                         'workload_setup.ycsb.yml'), 'w').close()
        test_config['infrastructure_provisioning'] = 'single'
        test_config['mongodb_setup'] = 'FIII'
        test_config['storageEngine'] = 'wiredTiger'
        test_config['test_control'] = 'ycsb'
        test_config['workload_setup'] = 'ycsb'
        test_config['production'] = False
        bootstrap.copy_config_files(test_dsipath, test_config, test_directory)
        master_files = {
            'infrastructure_provisioning.yml', 'mongodb_setup.yml', 'test_control.yml',
            'workload_setup.yml'
        }
        test_files = set(os.listdir(test_directory))
        self.assertEqual(test_files, master_files)

    def test_copy_cluster_setup(self):
        """
        Testing copy_config_files moves with cluster_setup instead of mongodb_setup
        """
        test_config = {}
        test_dsipath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_dsipath')
        test_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_directory')
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'infrastructure_provisioning'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'cluster_setup'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'cluster_setup', 'cratedb'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'test_control'))
        os.makedirs(os.path.join(test_dsipath, 'configurations', 'workload_setup'))
        os.mkdir(test_directory)
        open(
            os.path.join(test_dsipath, 'configurations', 'infrastructure_provisioning',
                         'infrastructure_provisioning.single.yml'), 'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'cluster_setup', 'cratedb',
                         'cluster_setup.cratedb.standalone.yml'), 'w').close()
        open(os.path.join(test_dsipath, 'configurations', 'test_control', 'test_control.ycsb.yml'),
             'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'workload_setup',
                         'workload_setup.ycsb.yml'), 'w').close()
        open(
            os.path.join(test_dsipath, 'configurations', 'workload_setup',
                         'workload_setup.ycsb.yml'), 'w').close()
        test_config['infrastructure_provisioning'] = 'single'
        test_config['cluster_setup'] = 'cratedb.standalone'
        test_config['test_control'] = 'ycsb'
        test_config['workload_setup'] = 'ycsb'
        test_config['production'] = False
        bootstrap.copy_config_files(test_dsipath, test_config, test_directory)
        master_files = {
            'infrastructure_provisioning.yml', 'cluster_setup.yml', 'test_control.yml',
            'workload_setup.yml'
        }
        test_files = set(os.listdir(test_directory))
        print(test_directory)
        print(test_files)
        self.assertEqual(test_files, master_files)

    @patch('os.path.exists')
    def test_setup_overrides_no_file_config_vals(self, mock_path_exists):
        """
        Testing setup_overrides where path = False and config vals given
        """
        mock_path_exists.return_value = False
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        master_overrides = {}
        master_overrides.update({
            'infrastructure_provisioning': {
                'tfvars': {
                    'ssh_key_file': 'test_ssh_key_file.pem',
                    'ssh_key_name': 'test_ssh_key_name',
                    'tags': {
                        'owner': 'testuser',
                        'expire-on-delta': 24
                    }
                }
            }
        })
        real_configdict.raw['bootstrap'] = {}
        real_configdict.raw['bootstrap']['overrides'] = master_overrides

        test_override_path = os.path.dirname(os.path.abspath(__file__))
        test_override_dict = {}

        # Call to setup_overrides creates 'overrides.yml' in current dir
        bootstrap.setup_overrides(real_configdict, test_override_path)
        with open(os.path.join(test_override_path, 'overrides.yml'), 'r') as test_override_file:
            test_override_dict = yaml.load(test_override_file)
        self.assertEqual(test_override_dict, master_overrides)

        # Removing created file
        os.remove(os.path.join(test_override_path, 'overrides.yml'))

    def test_setup_overrides_file_exists_config_vals(self):
        """
        Testing setup_overrides where path = True and config vals given
        """
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        test_override_path = os.path.dirname(os.path.abspath(__file__))
        master_overrides = {}
        master_overrides.update({
            'infrastructure_provisioning': {
                'tfvars': {
                    'ssh_key_file': 'test_ssh_key_file1.pem',
                    'ssh_key_name': 'test_ssh_key_name1',
                    'tags': {
                        'owner': 'testuser1',
                        'expire-on-delta': 24
                    }
                }
            }
        })
        real_configdict.raw['bootstrap']['overrides'] = master_overrides
        test_override_str = yaml.dump(
            {
                'infrastructure_provisioning': {
                    'tfvars': {
                        'ssh_key_file': 'test_ssh_key_file2.pem',
                        'ssh_key_name': 'test_ssh_key_name2',
                        'tags': {
                            'owner': 'testuser2',
                            'expire-on-delta': 48
                        }
                    }
                }
            },
            default_flow_style=False)

        # Creating 'overrides.yml' in current dir
        with open(os.path.join(test_override_path, 'overrides.yml'), 'w') as test_override_file:
            test_override_file.write(test_override_str)

        # Call to setup_overrides updates 'overrides.yml' in current dir
        bootstrap.setup_overrides(real_configdict, test_override_path)

        test_override_dict = {}
        with open(os.path.join(test_override_path, 'overrides.yml'), 'r') as test_override_file:
            test_override_dict = yaml.load(test_override_file)

        self.assertEqual(test_override_dict, master_overrides)

        # Removing created file
        os.remove(os.path.join(test_override_path, 'overrides.yml'))

    @patch('os.path.exists')
    def test_setup_overrides_no_file_empty_config(self, mock_path_exists):
        """
        Testing setup_overrides, path = False and config vals not given
        """
        mock_path_exists.return_value = False
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        test_override_path = os.path.dirname(os.path.abspath(__file__))

        # Call to setup_overrides creates 'overrides.yml' in current dir
        bootstrap.setup_overrides(real_configdict, test_override_path)

        self.assertFalse(os.path.exists(os.path.join(test_override_path, 'overrides.yml')))

    def test_setup_overrides_file_exists_empty_config(self):
        """
        Testing setup_overrides, path = True and config vals not given
        """
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        test_override_path = os.path.dirname(os.path.abspath(__file__))
        test_override_str = yaml.dump({}, default_flow_style=False)

        # Creating 'overrides.yml' in current dir
        with open(os.path.join(test_override_path, 'overrides.yml'), 'w+') as test_override_file:
            test_override_file.write(test_override_str)

        # Call to setup_overrides updates 'overrides.yml' in current dir
        bootstrap.setup_overrides(real_configdict, test_override_path)

        test_override_dict = {}
        with open(os.path.join(test_override_path, 'overrides.yml'), 'r') as test_override_file:
            test_override_dict = yaml.load(test_override_file)

        self.assertEqual(test_override_dict, {})

        # Removing created file
        os.remove(os.path.join(test_override_path, 'overrides.yml'))

    def test_setup_overrides_default_username(self):
        """
        Testing setup_overrides fails if user doesn't change username
        from bootstrap.example.yml default
        """
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        real_configdict.raw['bootstrap']['overrides'] = {
            'infrastructure_provisioning': {
                'tfvars': {
                    'tags': {
                        'owner': 'your.username'
                    }
                }
            }
        }
        test_override_path = os.path.dirname(os.path.abspath(__file__))
        with self.assertRaises(AssertionError):
            bootstrap.setup_overrides(real_configdict, test_override_path)

        # Removing created file
        try:
            os.remove(os.path.join(test_override_path, 'overrides.yml'))
        except OSError:
            pass

    def test_setup_overrides_type_error(self):
        """
        Testing setup_overrides doesn't throw TypeError.
        """
        real_configdict = ConfigDict('bootstrap')
        real_configdict.load()

        real_configdict.raw['bootstrap']['overrides'] = {
            'infrastructure_provisioning': {
                'tfvars': {
                    'tags': None
                }
            }
        }
        test_override_path = os.path.dirname(os.path.abspath(__file__))
        bootstrap.setup_overrides(real_configdict, test_override_path)

        # Removing created file
        try:
            os.remove(os.path.join(test_override_path, 'overrides.yml'))
        except OSError:
            pass

    @patch('subprocess.check_output')
    def test_find_terraform_no_except_tf_not_in_config(self, mock_check_output):
        """
        Testing find_terraform when terraform found in path.
        """
        # Just in case
        if 'TERRAFORM' in os.environ:
            del os.environ['TERRAFORM']

        mock_check_output.return_value = '/usr/bin/terraform'
        terraform = bootstrap.find_terraform('/', {})
        self.assertEqual(terraform, '/usr/bin/terraform')

    @patch('subprocess.check_output')
    @patch('bootstrap.requests')
    @patch('bootstrap._extract_zip')
    def test_find_terraform_download(self, mock_extract, mock_requests, mock_check_output):
        """
        Testing find_terraform goes to download when not found in PATH.
        """
        config = {'terraform_url': 'http://example.com/download/terraform'}
        mock_check_output.side_effect = subprocess.CalledProcessError('Test', 1)
        bootstrap.find_terraform('/Users/testuser/default', config)
        mock_extract.assert_called()
        mock_requests.get.assert_called_with(config['terraform_url'])

    @patch('subprocess.check_output')
    def test_terraform_wrong_version(self, mock_check_output):
        """
        Testing validate_terraform fails on incorrect version
        """
        # pylint: disable=line-too-long
        mock_check_output.return_value = "Terraform v0.6.16"
        config = {
            'terraform':
                './terraform',
            'terraform_version_check':
                'Terraform v0.9.11',
            'terraform_linux_download':
                'https://releases.hashicorp.com/terraform/0.12.16/terraform_0.12.16_linux_amd64.zip',
            'terraform_mac_download':
                'https://releases.hashicorp.com/terraform/0.12.16/terraform_0.12.16_darwin_amd64.zip',
            'production':
                False
        }
        with LogCapture(level=logging.CRITICAL) as crit:
            with self.assertRaises(AssertionError):
                bootstrap.validate_terraform(self.directory, config)
            actual = crit.actual()
            expected = (
                'bootstrap', 'CRITICAL',
                "[critical ] No Terraform download url found for your operating system. Automatic terraform download is not supported."
            )
            import pprint
            pprint.pprint(actual)
            self.assertEqual(expected[0], crit.records[0].name)
            self.assertEqual(expected[1], crit.records[0].levelname)
            self.assertTrue(crit.records[0].getMessage().startswith(expected[2]))

    @patch('subprocess.check_output')
    def test_terraform_call_fails(self, mock_check_output):
        """
        Testing validate_terraform fails when terraform call fails
        """
        mock_check_output.side_effect = subprocess.CalledProcessError(1, None)
        mock_check_output.return_value = "Terraform v0.6.16"
        config = {
            'terraform': './terraform',
            'terraform_version_check': 'Terraform v0.9.11',
            'production': False
        }
        with LogCapture(level=logging.CRITICAL) as crit:
            with self.assertRaises(AssertionError):
                bootstrap.validate_terraform(self.directory, config)
            crit_logs = set(crit.actual())

            message_1 = (u'[critical ] See documentation for installing terraform: '
                         u'http://bit.ly/2ufjQ0R [bootstrap]')
            message_2 = u'[critical ] Call to terraform failed.      [bootstrap]'
            crit_expected = {('bootstrap', 'CRITICAL', message_1),
                             ('bootstrap', 'CRITICAL', message_2)}
            self.assertTrue(crit_expected.issubset(crit_logs))

    @patch('subprocess.check_output')
    def test_terraform_cannot_execute(self, mock_check_output):
        """
        Testing validate_terraform fails when terraform doesn't run
        """
        mock_check_output.side_effect = subprocess.CalledProcessError(126, None)
        mock_check_output.return_value = "Terraform v0.6.16"
        config = copy.copy(self.TERRAFORM_CONFIG)
        with LogCapture(level=logging.CRITICAL) as crit:
            with self.assertRaises(AssertionError):
                bootstrap.validate_terraform(self.directory, config)
            crit_logs = set(crit.actual())
            crit_expected = {(
                'bootstrap', 'CRITICAL',
                u'[critical ] See documentation for installing terraform: http://bit.ly/2ufjQ0R [bootstrap]'
            ),
                             ('bootstrap', 'CRITICAL',
                              u'[critical ] Cannot execute terraform binary file. [bootstrap]')}
            self.assertTrue(crit_expected.issubset(crit_logs))

    @patch('subprocess.check_output')
    def test_terraform_not_found(self, mock_check_output):
        """
        Testing validate_terraform fails when terraform is not found
        """
        mock_check_output.side_effect = subprocess.CalledProcessError(127, None)
        mock_check_output.return_value = "Terraform v0.6.16"
        config = copy.copy(self.TERRAFORM_CONFIG)
        with LogCapture(level=logging.CRITICAL) as crit:
            with self.assertRaises(AssertionError):
                bootstrap.validate_terraform(self.directory, config)
            crit_logs = set(crit.actual())
            crit_expected = {(
                'bootstrap', 'CRITICAL',
                u'[critical ] See documentation for installing terraform: http://bit.ly/2ufjQ0R [bootstrap]'
            ), ('bootstrap', 'CRITICAL', u'[critical ] No terraform binary file found. [bootstrap]')
                             }
            self.assertTrue(crit_expected.issubset(crit_logs))

    @patch('subprocess.check_output')
    def test_terraform_valid(self, mock_check_output):
        """
        Testing validate_terraform with valid inputs
        """
        mock_check_output.return_value = 'Terraform v0.9.11'
        config = copy.copy(self.TERRAFORM_CONFIG)
        bootstrap.validate_terraform(self.directory, config)
        self.assertEqual(config, config)

    @patch('common.utils.get_dsi_bin_dir')
    def test_write_dsienv(self, mock_dsi_bin_dir):
        """
        Testing write_dsienv
        """
        mock_dsi_bin_dir.return_value = '/Users/test_user/dsipath/bin'
        directory = os.path.dirname(os.path.abspath(__file__))
        terraform = "/Users/test_user/terraform"
        master_dsienv = ('export PATH=/Users/test_user/dsipath/bin:$PATH\n'
                         'export TERRAFORM=/Users/test_user/terraform\n')
        bootstrap.write_dsienv(directory, terraform)

        with open(os.path.join(directory, "dsienv.sh")) as dsienv:
            test_dsienv = dsienv.read()
            self.assertEqual(test_dsienv[:len(master_dsienv)], master_dsienv)
        os.remove(os.path.join(directory, "dsienv.sh"))

    def test_load_bootstrap_no_file(self):
        """
        Testing that load_bootstrap fails when file doesn't exist
        """
        config = {'bootstrap_file': './notarealpath/bootstrap.yml', 'production': False}
        directory = os.getcwd()
        with LogCapture(level=logging.CRITICAL) as crit:
            with self.assertRaises(AssertionError):
                bootstrap.load_bootstrap(config, directory)
            crit.check(
                ('bootstrap', 'CRITICAL',
                 u'[critical ] Location specified for bootstrap.yml is invalid. [bootstrap]'))

    def test_load_bootstrap_different_filename(self):
        """
        Testing that load_bootstrap works with alternate file names
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap2.yml')
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        config = {'bootstrap_file': bootstrap_path, 'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('owner: test_owner')
        bootstrap.load_bootstrap(config, directory)
        self.assertEqual(config['owner'], 'test_owner')

    def test_load_bootstrap_local_file_makedir(self):
        """
        Testing that load_bootstrap makes nonexistent directory and copies into it
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap.yml')
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        config = {'bootstrap_file': bootstrap_path, 'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('owner: test_owner')
        bootstrap.load_bootstrap(config, directory)
        self.assertEqual(config['owner'], 'test_owner')

    @patch('subprocess.check_output')
    def test_bootstrap_respects_existing_expansions_file(self, mock_check_output):
        """
        Test we don't create a new expansions yaml file if already there
        """
        mock_check_output.return_value = 'Terraform v0.12.16'

        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap.yml')
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        expansions_file = os.path.join(directory, 'expansions.yml')

        os.mkdir(directory)

        secret_overrides_path = os.path.join(directory, 'overrides.yml')
        with open(secret_overrides_path, 'w') as config:
            config.writelines([
                'runtime_secret: { aws_access_key: dummy, aws_secret_key: dummy }',
            ])

        with open(bootstrap_path, 'w') as config:
            config.writelines([
                # this is the only entry that's actually needed
                'infrastructure_provisioning: single',
            ])

        with open(expansions_file, 'w+') as expansions:
            expansions.writelines(['from_expansions: true'])

        self.assertTrue(os.path.exists(expansions_file))

        bootstrap.run_bootstrap({'directory': directory, 'bootstrap_file': bootstrap_path})
        with open(expansions_file, 'r') as expansions:
            self.assertEqual(expansions.read(), 'from_expansions: true')

    def test_load_bootstrap_copy_file_to_local(self):
        """
        Testing that load_bootstrap copies specified file in 'testdir' to local directory
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'testdir/bootstrap.yml')
        bootstrap_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        os.mkdir(bootstrap_directory)
        config = {'bootstrap_file': bootstrap_path, 'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('owner: test_owner')
        bootstrap.load_bootstrap(config, os.path.dirname(os.path.abspath(__file__)))

        # confirms that load_bootstrap copies file into working directory correctly
        self.assertEqual(config['owner'], 'test_owner')

    def test_load_bootstrap_copy_same_file(self):
        """
        Testing that load_bootstrap copies specified file in 'testdir' to
        local directory and fails on collision
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'testdir/bootstrap.yml')
        wrong_bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            './bootstrap.yml')
        bootstrap_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        os.mkdir(bootstrap_directory)
        config = {'bootstrap_file': bootstrap_path, 'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('owner: test_owner')
        with open(wrong_bootstrap_path, 'w') as wrong_bootstrap_file:
            wrong_bootstrap_file.write('owner: test_owner')
        with self.assertRaises(AssertionError):
            bootstrap.load_bootstrap(config, os.path.dirname(os.path.abspath(__file__)))

    def test_load_bootstrap_given_file_and_dir(self):
        """
        Testing that load_bootstrap copies file from 'test_old_dir' into
        'test_new_dir' without collisions
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'test_old_dir/bootstrap.yml')
        bootstrap_new_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               'test_new_dir/')
        bootstrap_old_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               'test_old_dir/')
        os.mkdir(bootstrap_new_directory)
        os.mkdir(bootstrap_old_directory)
        config = {'bootstrap_file': bootstrap_path, 'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('platform: test_platform')
        bootstrap.load_bootstrap(config, bootstrap_new_directory)

        # confirms that load_bootstrap copies file into working directory correctly
        self.assertEqual(config['platform'], 'test_platform')

    def test_load_bootstrap_no_file_specified(self):
        """
        Testing that load_bootstrap loads defaults into config object without bootstrap.yml
        """
        master_config = {
            'analysis':
                'common',
            'workload_setup':
                'common',
            'infrastructure_provisioning':
                'single',
            'mongodb_setup':
                'standalone',
            'platform':
                'linux',
            'production':
                False,
            'storageEngine':
                'wiredTiger',
            'terraform_linux_download':
                'https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip',
            'terraform_mac_download':
                'https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_darwin_amd64.zip',
            'terraform_version_check':
                'Terraform v1.7.5',
            'test_control':
                'ycsb'
        }
        test_config = {}
        bootstrap.load_bootstrap(test_config, '.')
        self.assertEqual(test_config, master_config)
        # confirms that load_bootstrap copies file into working directory correctly

    def test_load_bootstrap_copy_file_default_to_local(self):
        """
        Testing that load_bootstrap uses local file if --bootstrap-file flag not used
        """
        bootstrap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bootstrap.yml')
        bootstrap_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdir/')
        os.mkdir(bootstrap_directory)
        config = {'production': False}
        with open(bootstrap_path, 'w') as bootstrap_file:
            bootstrap_file.write('owner: test_owner')
        current_path = os.getcwd()
        os.chdir(os.path.join(bootstrap_directory, '..'))
        bootstrap.load_bootstrap(config, bootstrap_directory)
        os.chdir(current_path)

        # confirms that load_bootstrap copies local file into working directory if not specified
        self.assertEqual(config['owner'], 'test_owner')

    def test_bootstrap_list(self):
        bootstrap.run_bootstrap({'list': True})
        # Nothing to assert, it can print whatever. Just confirming it ran.


if __name__ == '__main__':
    unittest.main()

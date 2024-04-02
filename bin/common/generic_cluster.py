"""
Classes to control MongoDB clusters.
"""

import logging
import os
import sys
import time

import yaml

from common import host_factory
from common.host_utils import ssh_user_and_key_file
from common.models.host_info import HostInfo
from common.config import copy_obj

LOG = logging.getLogger(__name__)

DEFAULT_MEMBER_PRIORITY = 1


def create_cluster(topology, config):
    """
    Create MongoNode, ReplSet, or ShardCluster from topology config
    :param topology: topology config to create - see MongoNode, ReplSet, ShardedCluster docs
    :param config: root ConfigDict
    """
    cluster_type = topology['cluster_type']
    LOG.info('creating topology: %s', cluster_type)
    if cluster_type == 'standalone':
        return ClusterNode(topology=topology, config=config)
    elif cluster_type == 'replset':
        raise NotImplementedError()
    elif cluster_type == 'sharded_cluster':
        raise NotImplementedError()
    LOG.fatal('unknown cluster_type: %s', cluster_type)
    return sys.exit(1)


# I want to use self.id for nodes here.
# pylint: disable=invalid-name
class GenericCluster(object):
    """ Abstract base class for all clusters """
    def __init__(self, topology, config):
        """
        :param topology: Cluster specific configuration
        :param ConfigDict config: root ConfigDict

        """
        self.config = config
        self.topology = topology

    def wait_until_up(self):
        """ Checks to make sure node is up and accessible"""
        raise NotImplementedError()

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """ Start the cluster """
        raise NotImplementedError()

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """ Shutdown the cluster gracefully """
        raise NotImplementedError()

    def destroy(self, max_time_ms, nodes=None):
        """ Kill the cluster """
        raise NotImplementedError()

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        """Ensures necessary files are setup

        :param restart_clean_db_dir Should we clean db dir on restart. If not specified, uses value
        from ConfigDict.
        :param restart_clean_logs   Should we clean logs and diagnostic data. If not specified,
        uses value from ConfigDict.
        """
        raise NotImplementedError()

    def add_default_users(self):
        """
        Add the default users.

        Required for authentication to work properly. Assumes that the cluster is already up and
        running. It must connect to the appropriate node before authentication is enabled. Once the
        users are added, the cluster is rebooted with authentication enabled and any connections
        from there on out must use the authentication string.
        """
        raise NotImplementedError()

    def __str__(self):
        """ String describing the cluster """
        raise NotImplementedError()

    def close(self):
        """Closes SSH connections to remote hosts."""
        raise NotImplementedError()


class ClusterNode(GenericCluster):
    """Represents a mongo[ds] program on a remote host."""

    # pylint: disable=too-many-instance-attributes
    def __init__(self, topology, config):
        """
        :param topology: Read-only options for node, example:
        {
            'public_ip': '127.0.0.1',
            'private_ip': '127.0.0.1',
            'config_file': {},
            'mongo_dir': '/usr/bin',
            'priority': 10,
            'clean_logs': True,
            'clean_db_dir': True,
            'is_mongos': False,
            'is_configsvr': False
        }
        :param config: root ConfigDict
        """
        super(ClusterNode, self).__init__(topology, config)

        self.id = topology.get('id')
        self.cluster_setup = self.config['cluster_setup']
        self.exec_program = self.cluster_setup['exec_program']
        self.shutdown_command = self.cluster_setup['shutdown_command']
        if isinstance(self.exec_program, str):
            self.exec_program = list(self.exec_program)

        self.exec_command = self.cluster_setup['exec_command']

        self.public_ip = topology['node']['public_ip']
        self.private_ip = topology['node'].get('private_ip', self.public_ip)
        self.bin_dir = self.cluster_setup['directories'].get('bin_dir', 'bin')

        # NB: we could specify these defaults in default.yml if not already!
        # TODO: https://jira.mongodb.org/browse/PERF-1246 For the next 3 configs, ConfigDict does
        #       not "magically" combine the common setting with the node specific one (topology vs
        #       mongodb_setup). We should add that to ConfigDict to make these lines as simple as
        #       the rest.
        self.clean_logs = topology.get('clean_logs', self.cluster_setup.get('clean_logs', True))
        self.clean_db_dir = topology.get('clean_db_dir',
                                         self.cluster_setup.get('clean_db_dir', True))

        self.node_config_file = copy_obj(topology.get('config_file', {}))

        # TODO: Allow these to be specified per node... (Same todo as above.)
        self.logdir = self.cluster_setup['directories']['log_dir']
        self.port = self.cluster_setup['meta']['port']
        self.dbdir = self.cluster_setup['directories']['data_dir']
        self.config_dir = self.cluster_setup['directories']['config_dir']
        self.lockfile = self.cluster_setup['files']['lock']

        self.numactl_prefix = ""
        if self.cluster_setup["numactl"]:
            self.numactl_prefix = self.config['infrastructure_provisioning'].get(
                'numactl_prefix', '')

        # The numactl_prefix is used in shell scripts directly invoked from yaml configs as a shell
        # variable, so it has to be a string. Ideally we want the configuration for numactl to
        # already be a list of commandline arguments.
        if isinstance(self.numactl_prefix, str):
            self.numactl_prefix = self.numactl_prefix.split(' ')

        # Accessed via @property
        self._host = None

    # This is a @property versus a plain self.host var for 2 reasons:
    # 1. We don't need to be doing SSH stuff or be reading related
    #    configs if we never actually access the host var, and the host
    #    constructors eagerly do this stuff.
    # 2. It makes things slightly easier to test :)
    @property
    def host(self):
        """Access to remote or local host."""
        if self._host is None:
            host_info = self._compute_host_info()
            self._host = host_factory.make_host(host_info)
        return self._host

    def _compute_host_info(self):
        """Create host wrapper to run commands."""
        (ssh_user, ssh_key_file) = ssh_user_and_key_file(self.config)
        return HostInfo(public_ip=self.public_ip,
                        private_ip=self.private_ip,
                        ssh_user=ssh_user,
                        ssh_key_file=ssh_key_file)

    def wait_until_up(self):
        """ Checks to make sure node is up and accessible"""
        LOG.debug("Wait until node %s is actually up.", self.hostport_private())
        check_string = self.cluster_setup["check_node_up"]
        i = 0
        dump = False
        while not self.exec_client_shell(check_string.format(self.public_ip),
                                         dump_on_error=dump) and i < 10:
            i += 1
            if i == 9:
                dump = False
            time.sleep(1)
        if i == 10:
            LOG.error("Node %s not up at end of wait_until_up", self.public_ip)
            return False
        return True

    def setup_host(self, restart_clean_db_dir=None, restart_clean_logs=None, nodes=None):
        if isinstance(nodes, list) and self.id not in nodes:
            # Launch request is limited to some nodes, and this node isn't one of them.
            return True

        self.shutdown(max_time_ms=300 * 1000)
        self.kill()

        if restart_clean_db_dir is not None:
            _clean_db_dir = restart_clean_db_dir
        else:
            _clean_db_dir = self.clean_db_dir
        _clean_logs = restart_clean_logs if restart_clean_logs is not None else self.clean_logs

        _journal_dir = self.config['cluster_setup']['directories'].get('journal_dir', False)

        setup_cmd_args = {
            'clean_db_dir': _clean_db_dir,
            'clean_logs': _clean_logs,
            'dbdir': self.dbdir,
            'journal_dir': _journal_dir,
            'config_dir': self.config_dir,
            'logdir': self.logdir,
        }
        LOG.debug("setup_cmd_args:")
        LOG.debug(setup_cmd_args)
        commands = ClusterNode._generate_setup_commands(setup_cmd_args)
        return self.host.run(commands)

    @staticmethod
    def _generate_setup_commands(setup_args):
        commands = []

        # Clean the logs
        if setup_args['clean_logs']:
            commands.append(['rm', '-rf', os.path.join(setup_args['logdir'], '*.log')])
            commands.append(['rm', '-rf', os.path.join(setup_args['logdir'], 'core.*')])
        # Create the data/logs directories
        commands.append(['mkdir', '-p', setup_args['logdir']])

        if setup_args['dbdir'] and setup_args['clean_db_dir']:
            commands.append(['rm', '-rf', setup_args['dbdir']])

            if setup_args['journal_dir']:
                commands.append(['rm', '-rf', setup_args['journal_dir']])

            commands.append(['mkdir', '-p', setup_args['dbdir']])

            # If not clean_db_dir assume that this has already been done.
            # Create separate journal directory and link to the database
            if setup_args['journal_dir']:
                commands.append(['mkdir', '-p', setup_args['journal_dir']])

        #commands.append(['rm', '-rf', setup_args['config_dir']])
        commands.append(['mkdir', '-p', setup_args['config_dir']])

        return commands

    # pylint: disable=unused-argument
    def launch_cmd(self, use_numactl=True, enable_auth=False):
        """Returns the command to start this node."""
        remote_file_name = self.node_config_file["remote_path"]
        config_contents = self.node_config_file['content']
        if not isinstance(config_contents, str):
            config_contents = yaml.dump(config_contents, default_flow_style=False)
        self.host.create_file(remote_file_name, config_contents)
        self.host.run(['cat', remote_file_name])

        for other in self.cluster_setup.get("other_config_files", list()):
            config_contents = yaml.dump(other["content"])
            other_remote_file_name = other["remote_path"]
            LOG.info("Uploading config file %s", other_remote_file_name)
            self.host.create_file(other_remote_file_name, config_contents)

        cmd = [self.exec_command]

        if use_numactl and self.numactl_prefix:
            if not isinstance(self.numactl_prefix, list):
                raise ValueError('numactl_prefix must be a list of commands, given: {}'.format(
                    self.numactl_prefix))
            cmd = self.numactl_prefix + cmd

        LOG.debug("cmd is %s", str(cmd))
        return cmd

    def launch(self, initialize=True, use_numactl=True, enable_auth=False, nodes=None):
        """
        Starts this node.

        :param boolean initialize: Initialize the node. This doesn't do anything for the
                                     base node
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Launch request is limited to some nodes, and this node isn't one of them.
            return True

        # initialize is explicitly not used for now for a single node. We may want to use it in
        # the future
        _ = initialize
        launch_cmd = self.launch_cmd(use_numactl=use_numactl, enable_auth=enable_auth)
        if not self.host.run(launch_cmd):
            LOG.error("failed launch command: %s", launch_cmd)
            self.dump_log()
            return False
        return self.wait_until_up()

    def exec_client_shell(self, exec_string, max_time_ms=None, dump_on_error=True):
        """
        Execute `exec_string` in the client's shell on the underlying host
        :param str js_string: the javascript to evaluate.
        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :param bool dump_on_error: print 100 lines of the cluster node's log on error
        :return: True if the client shell exits successfully
        """
        if self.host.exec_client_shell(exec_string, self.config, max_time_ms=max_time_ms) != 0:
            # Some functions call this in a loop, so we may not want to dump the same log repeatedly
            if dump_on_error:
                self.dump_log()
            return False
        return True

    def dump_log(self):
        """Dump the log file to the process log"""
        LOG.info('Dumping log for node %s', self.hostport_public())
        self.host.run(['tail', '-n', '100', self.logdir + "/*.log"])

    def hostport_private(self):
        """Returns the string representation this host/port."""
        return '{0}:{1}'.format(self.private_ip, self.port)

    connection_string_private = hostport_private

    def hostport_public(self):
        """Returns the string representation this host/port."""
        return '{0}:{1}'.format(self.public_ip, self.port)

    connection_string_public = hostport_public

    def shutdown(self, max_time_ms, auth_enabled=None, retries=20, nodes=None):
        """
        Shutdown the node gracefully.

        For the max_time_ms parameter, see :method:`Host.exec_command`
        :return: True if shutdownServer command ran successfully.
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Shutdown request is limited to some nodes, and this node isn't one of them.
            return True

        for i in range(retries):
            # If there's a problem, don't dump 20x100 lines of log
            dump_on_error = i < 2
            try:
                LOG.info("Shutting down node %s, first trying with the provided shutdown_command.",
                         self.private_ip)
                # TODO: Graceful shutdown via exec_client_shell so that primaries step down first.
                self.host.run(self.shutdown_command, dump_on_error=False)
            except Exception:  # pylint: disable=broad-except
                LOG.error(
                    "Error shutting down ClusterNode at %s:%s",
                    self.public_ip,
                    self.port,
                )

            for prog in self.exec_program:
                if self.host.run(["pgrep -l"] + [prog]):
                    LOG.warning(
                        "%s %s:%s did not shutdown yet",
                        prog,
                        self.public_ip,
                        self.port,
                    )
                else:
                    return True
                time.sleep(10)
        return False

    def destroy(self, max_time_ms, nodes=None):
        """Kills the remote mongo program. First it sends SIGTERM every second for up to
        max_time_ms. It also always sends a SIGKILL and cleans up dbdir if this attribute is set.

        For the max_time_ms parameter, see
            :method:`Host.exec_command`
        :return: bool True if there are no processes matching 'mongo' on completion.
        """
        if isinstance(nodes, list) and self.id not in nodes:
            # Shutdown request is limited to some nodes, and this node isn't one of them.
            return True

        return_value = False
        try:
            return_value = self.term(max_time_ms=max_time_ms)
        finally:
            if not return_value:
                LOG.warning(
                    "%s %s:%s did not shutdown cleanly! Will now SIGKILL and delete lock file.",
                    self.exec_program,
                    self.public_ip,
                    self.port,
                )
            # ensure the processes are dead and cleanup
            return_value = self.kill()

            if self.dbdir:
                self.host.run(['rm', '-rf', self.lockfile])

        return return_value

    def kill(self):
        """Kill exec_program on remote host."""
        return_value = True
        for prog in self.exec_program:
            return_value = return_value and self.host.kill_remote_procs(prog,
                                                                        signal_number='SIGKILL')
        return return_value

    def term(self, max_time_ms=None):
        """Terminate exec_program on remote host."""
        return_value = True
        for prog in self.exec_program:
            return_value = return_value and self.host.kill_remote_procs(
                prog, signal_number='SIGTERM', max_time_ms=max_time_ms)
        return return_value

    def add_default_users(self):
        raise NotImplementedError()

    def close(self):
        """Closes SSH connections to remote hosts."""
        self.host.close()

    def __str__(self):
        """String describing this node"""
        return '{}: {}'.format(self.exec_program[0], self.hostport_public())
